use crate::{
    binary::{self, BitString, Value as SampleValue},
    error::{AppError, bad},
    models::*,
};
use serde_json::{Value, json};
use sqlx::{PgPool, Postgres, QueryBuilder, Row};
use uuid::Uuid;
#[derive(Clone)]
pub struct Database {
    pub pool: PgPool,
}
impl Database {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
    pub async fn collection(&self, id: Uuid) -> Result<Value, AppError> {
        let v:Option<Value>=sqlx::query_scalar("SELECT to_jsonb(c) - 'idempotency_key' || jsonb_build_object('sources',COALESCE((SELECT jsonb_agg(jsonb_build_object('source_id',source_id,'locator',locator)) FROM collection_sources WHERE collection_id=c.id),'[]'::jsonb),'parents',COALESCE((SELECT jsonb_agg(parent_id) FROM collection_parents WHERE collection_id=c.id),'[]'::jsonb)) FROM collections c WHERE id=$1").bind(id).fetch_optional(&self.pool).await?;
        v.ok_or(AppError::NotFound)
    }
    pub async fn start(
        &self,
        input: CollectionInput,
        key: Option<String>,
    ) -> Result<Value, AppError> {
        input.validate()?;
        let data = serde_json::to_value(input)?;
        let hash = binary::sha(&serde_json::to_vec(&data)?);
        let mut tx = self.pool.begin().await?;
        if let Some(key) = &key {
            sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))")
                .bind(format!("upload:{key}"))
                .execute(&mut *tx)
                .await?;
            if let Some(row) = sqlx::query(
                "SELECT id,request_hash,completed FROM uploads WHERE idempotency_key=$1",
            )
            .bind(key)
            .fetch_optional(&mut *tx)
            .await?
            {
                if row.get::<String, _>("request_hash") != hash {
                    return Err(AppError::Conflict(
                        "idempotency key reused with different metadata".into(),
                    ));
                }
                return Ok(
                    json!({"id":row.get::<Uuid,_>("id"),"completed":row.get::<bool,_>("completed")}),
                );
            }
        }
        let id = Uuid::new_v4();
        sqlx::query(
            "INSERT INTO uploads(id,metadata,idempotency_key,request_hash) VALUES($1,$2,$3,$4)",
        )
        .bind(id)
        .bind(data)
        .bind(key)
        .bind(hash)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(json!({"id":id,"completed":false}))
    }
    pub async fn upload(&self, id: Uuid) -> Result<Value, AppError> {
        let row = sqlx::query("SELECT metadata,completed FROM uploads WHERE id=$1")
            .bind(id)
            .fetch_optional(&self.pool)
            .await?
            .ok_or(AppError::NotFound)?;
        let chunks:Vec<Value>=sqlx::query_scalar("SELECT jsonb_build_object('index',chunk_index,'sha256',sha256,'entries',entries,'shots',shots,'weight_sum',weight_sum) FROM (SELECT chunk_index,sha256,entries,shots,weight_sum FROM upload_chunks WHERE upload_id=$1 UNION ALL SELECT chunk_index,sha256,entries,shots,weight_sum FROM collection_chunks WHERE collection_id=$1) blocks ORDER BY chunk_index").bind(id).fetch_all(&self.pool).await?;
        Ok(
            json!({"id":id,"metadata":row.get::<Value,_>("metadata"),"completed":row.get::<bool,_>("completed"),"chunks":chunks}),
        )
    }
    pub async fn put_chunk(&self, id: Uuid, index: i32, bytes: &[u8]) -> Result<(), AppError> {
        if index < 0 {
            return Err(bad("negative chunk index"));
        }
        let block = binary::decode(bytes)?;
        let hash = binary::sha(bytes);
        let mut tx = self.pool.begin().await?;
        let upload = sqlx::query("SELECT metadata,completed FROM uploads WHERE id=$1 FOR UPDATE")
            .bind(id)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or(AppError::NotFound)?;
        let input: CollectionInput = serde_json::from_value(upload.get("metadata"))?;
        if block.width as i32 != input.n_qubits || block.weighted == input.counts() {
            return Err(bad("chunk width or encoding differs from upload"));
        }
        if input.result_kind != "quasi_probabilities"
            && block
                .entries
                .iter()
                .any(|(_, v)| matches!(v,SampleValue::Weight(x) if *x<0.))
        {
            return Err(bad("negative weights only allowed for quasi_probabilities"));
        }
        if let Some(old) = sqlx::query_scalar::<_, String>(
            "SELECT sha256 FROM upload_chunks WHERE upload_id=$1 AND chunk_index=$2",
        )
        .bind(id)
        .bind(index)
        .fetch_optional(&mut *tx)
        .await?
        {
            return if old == hash {
                Ok(())
            } else {
                Err(AppError::Conflict(
                    "chunk already exists with different content".into(),
                ))
            };
        }
        if upload.get::<bool, _>("completed") {
            let old: Option<String> = sqlx::query_scalar(
                "SELECT sha256 FROM collection_chunks WHERE collection_id=$1 AND chunk_index=$2",
            )
            .bind(id)
            .bind(index)
            .fetch_optional(&mut *tx)
            .await?;
            if old.as_deref() == Some(hash.as_str()) {
                return Ok(());
            }
            return Err(AppError::Conflict("collection is already published".into()));
        }
        sqlx::query("INSERT INTO upload_chunks(upload_id,chunk_index,payload,sha256,entries,shots,weight_sum,first_state,last_state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)").bind(id).bind(index).bind(bytes).bind(hash).bind(block.entries.len() as i32).bind(block.shots).bind(block.weight_sum).bind(block.entries[0].0.bytes()).bind(block.entries.last().unwrap().0.bytes()).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(())
    }
    pub async fn finalize(&self, id: Uuid) -> Result<Value, AppError> {
        let mut tx = self.pool.begin().await?;
        let upload = sqlx::query(
            "SELECT metadata,completed,idempotency_key FROM uploads WHERE id=$1 FOR UPDATE",
        )
        .bind(id)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or(AppError::NotFound)?;
        if upload.get::<bool, _>("completed") {
            tx.commit().await?;
            return self.collection(id).await;
        }
        let input: CollectionInput = serde_json::from_value(upload.get("metadata"))?;
        input.validate()?;
        // Related records must agree. A missing value may remain unknown, never a fabricated timestamp.
        if let Some(cid) = input.circuit_id {
            let c = sqlx::query(
                "SELECT circuit_hash,n_clbits,problem_id,qaoa_p FROM circuits WHERE id=$1",
            )
            .bind(cid)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or_else(|| bad("unknown circuit_id"))?;
            if input.circuit_hash.as_deref() != Some(c.get::<String, _>("circuit_hash").as_str())
                || input.n_qubits != c.get::<i32, _>("n_clbits")
                || (input.problem_id.is_some() && input.problem_id != c.get("problem_id"))
                || (input.qaoa_p.is_some()
                    && c.get::<Option<i32>, _>("qaoa_p").is_some()
                    && input.qaoa_p != c.get("qaoa_p"))
            {
                return Err(bad("collection differs from referenced circuit"));
            }
        }
        if let Some(eid) = input.execution_id {
            let e = sqlx::query(
                "SELECT backend,circuit_id,calibration_id,executed_at FROM executions WHERE id=$1",
            )
            .bind(eid)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or_else(|| bad("unknown execution_id"))?;
            if input.backend != e.get("backend")
                || input.circuit_id != e.get("circuit_id")
                || input.calibration_id != e.get("calibration_id")
                || input.executed_at != e.get("executed_at")
            {
                return Err(bad("collection differs from referenced execution"));
            }
        }
        if let Some(cid) = input.calibration_id {
            let backend: Option<String> =
                sqlx::query_scalar("SELECT backend FROM calibrations WHERE id=$1")
                    .bind(cid)
                    .fetch_optional(&mut *tx)
                    .await?;
            if backend.is_none() || backend != input.backend {
                return Err(bad("calibration backend differs from collection"));
            }
        }
        let chunks=sqlx::query("SELECT chunk_index,entries,shots,weight_sum,first_state,last_state FROM upload_chunks WHERE upload_id=$1 ORDER BY chunk_index").bind(id).fetch_all(&mut *tx).await?;
        if chunks.is_empty() {
            return Err(bad("cannot publish an empty collection"));
        }
        let mut last = None;
        let mut entries = 0i64;
        let mut shots = 0i64;
        let mut weight = 0f64;
        for (i, c) in chunks.iter().enumerate() {
            if c.get::<i32, _>("chunk_index") != i as i32 {
                return Err(bad("chunk indices must be contiguous from zero"));
            }
            let first = BitString::from_bytes(&c.get::<Vec<u8>, _>("first_state"));
            if last.is_some_and(|v| v >= first) {
                return Err(bad("chunks must have disjoint ascending states"));
            }
            last = Some(BitString::from_bytes(&c.get::<Vec<u8>, _>("last_state")));
            entries = entries
                .checked_add(c.get::<i32, _>("entries") as i64)
                .ok_or_else(|| bad("entry total overflow"))?;
            shots = shots
                .checked_add(c.get::<Option<i64>, _>("shots").unwrap_or(0))
                .ok_or_else(|| bad("shot total overflow"))?;
            weight += c.get::<Option<f64>, _>("weight_sum").unwrap_or(0.);
        }
        if !weight.is_finite()
            || (["probabilities", "quasi_probabilities"].contains(&input.result_kind.as_str())
                && (weight - 1.).abs() > 1e-9)
        {
            return Err(bad("probability weights must sum to one within 1e-9"));
        }
        sqlx::query("INSERT INTO collections(id,result_kind,n_qubits,backend,circuit_hash,circuit_association,circuit_id,problem_id,execution_id,calibration_id,qaoa_p,executed_at,supersedes_id,idempotency_key,metadata,entries,shots,weight_sum,chunks) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)")
            .bind(id).bind(&input.result_kind).bind(input.n_qubits).bind(&input.backend).bind(&input.circuit_hash).bind(&input.circuit_association).bind(input.circuit_id).bind(input.problem_id).bind(input.execution_id).bind(input.calibration_id).bind(input.qaoa_p).bind(input.executed_at).bind(input.supersedes_id).bind(upload.get::<Option<String>,_>("idempotency_key")).bind(&input.metadata).bind(entries).bind(input.counts().then_some(shots)).bind((!input.counts()).then_some(weight)).bind(chunks.len() as i32).execute(&mut *tx).await?;
        sqlx::query("INSERT INTO collection_chunks SELECT upload_id,chunk_index,payload,sha256,entries,shots,weight_sum FROM upload_chunks WHERE upload_id=$1").bind(id).execute(&mut *tx).await?;
        for source in &input.sources {
            sqlx::query("INSERT INTO collection_sources VALUES($1,$2,$3)")
                .bind(id)
                .bind(source.source_id)
                .bind(&source.locator)
                .execute(&mut *tx)
                .await?;
        }
        for parent in &input.parents {
            sqlx::query("INSERT INTO collection_parents VALUES($1,$2)")
                .bind(id)
                .bind(parent)
                .execute(&mut *tx)
                .await?;
        }
        sqlx::query("UPDATE uploads SET completed=TRUE WHERE id=$1")
            .bind(id)
            .execute(&mut *tx)
            .await?;
        // Staging remains resumable metadata, but do not retain a second copy of published payloads.
        sqlx::query("DELETE FROM upload_chunks WHERE upload_id=$1")
            .bind(id)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        self.collection(id).await
    }
    pub async fn chunk(&self, id: Uuid, index: i32) -> Result<(Vec<u8>, String), AppError> {
        let row=sqlx::query("SELECT payload,sha256 FROM collection_chunks WHERE collection_id=$1 AND chunk_index=$2").bind(id).bind(index).fetch_optional(&self.pool).await?.ok_or(AppError::NotFound)?;
        let bytes: Vec<u8> = row.get("payload");
        let hash: String = row.get("sha256");
        if binary::sha(&bytes) != hash {
            return Err(AppError::Conflict("stored chunk checksum mismatch".into()));
        }
        Ok((bytes, hash))
    }
    pub async fn list(&self, q: CollectionQuery) -> Result<Value, AppError> {
        let limit = q.limit.unwrap_or(100);
        if !(1..=1000).contains(&limit) {
            return Err(bad("limit must be 1..1000"));
        }
        let mut b = QueryBuilder::<Postgres>::new(
            "SELECT to_jsonb(c)-'idempotency_key' FROM collections c WHERE TRUE",
        );
        macro_rules! filter {
            ($field:ident,$column:literal) => {
                if let Some(v) = q.$field {
                    b.push(concat!(" AND ", $column, " = ")).push_bind(v);
                }
            };
        }
        filter!(backend, "backend");
        filter!(circuit_hash, "circuit_hash");
        filter!(n_qubits, "n_qubits");
        filter!(result_kind, "result_kind");
        filter!(circuit_association, "circuit_association");
        filter!(problem_id, "problem_id");
        filter!(calibration_id, "calibration_id");
        filter!(qaoa_p, "qaoa_p");
        if let Some(v) = q.executed_after {
            b.push(" AND executed_at >= ").push_bind(v);
        }
        if let Some(v) = q.executed_before {
            b.push(" AND executed_at <= ").push_bind(v);
        }
        if let Some(id) = q.cursor {
            let exists: bool =
                sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM collections WHERE id=$1)")
                    .bind(id)
                    .fetch_one(&self.pool)
                    .await?;
            if !exists {
                return Err(bad("unknown cursor"));
            }
            b.push(" AND (created_at,id) < (SELECT created_at,id FROM collections WHERE id = ")
                .push_bind(id)
                .push(")");
        }
        b.push(" ORDER BY created_at DESC,id DESC LIMIT ")
            .push_bind(limit + 1);
        let mut items: Vec<Value> = b.build_query_scalar().fetch_all(&self.pool).await?;
        let more = items.len() > limit as usize;
        if more {
            items.pop();
        }
        let next = if more {
            items.last().map(|v| v["id"].clone())
        } else {
            None
        };
        Ok(json!({"items":items,"next_cursor":next}))
    }
}
