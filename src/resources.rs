//! The table and column allowlists are static; user input never becomes an SQL identifier.
use crate::{
    binary::{BitString, sha},
    db::Database,
    error::{AppError, bad},
    models::valid_hash,
};
use serde_json::{Value, json};
use sqlx::Row;
use uuid::Uuid;
fn fields(name: &str) -> Result<&'static [&'static str], AppError> {
    Ok(match name {
        "sources" => &["uri", "sha256", "artifact_id", "data"],
        "problems" => &["kind", "n_variables", "data"],
        "solutions" => &["problem_id", "state", "status", "objective", "data"],
        "circuits" => &[
            "circuit_hash",
            "fingerprint_scheme",
            "fingerprint_artifact_id",
            "n_qubits",
            "n_clbits",
            "problem_id",
            "qaoa_p",
            "data",
        ],
        "calibrations" => &["backend", "measured_at", "data"],
        "executions" => &[
            "backend",
            "circuit_id",
            "calibration_id",
            "job_id",
            "executed_at",
            "data",
        ],
        _ => return Err(AppError::NotFound),
    })
}
impl Database {
    pub async fn create_resource(
        &self,
        name: &str,
        mut value: Value,
        key: Option<String>,
    ) -> Result<Value, AppError> {
        let columns = fields(name)?;
        let data = value
            .as_object_mut()
            .ok_or_else(|| bad("expected JSON object"))?;
        if data.keys().any(|k| !columns.contains(&k.as_str())) {
            return Err(bad("unknown resource field"));
        }
        data.entry("data").or_insert(json!({}));
        if !data["data"].is_object() {
            return Err(bad("data must be an object"));
        }
        let request_hash = sha(&serde_json::to_vec(&value)?);
        match name {
            "sources" => {
                if !valid_hash(value["sha256"].as_str().unwrap_or(""))
                    || value["uri"].as_str().is_none_or(str::is_empty)
                {
                    return Err(bad("source requires URI and SHA256"));
                }
                if let Some(id) = value["artifact_id"].as_str() {
                    let hash: Option<String> =
                        sqlx::query_scalar("SELECT sha256 FROM artifacts WHERE id=$1")
                            .bind(parse_uuid(id)?)
                            .fetch_optional(&self.pool)
                            .await?;
                    if hash.as_deref() != value["sha256"].as_str() {
                        return Err(bad("source artifact checksum differs"));
                    }
                }
            }
            "problems" => {
                if value["kind"].as_str().is_none_or(str::is_empty) {
                    return Err(bad("problem kind required"));
                }
                let n = value["n_variables"]
                    .as_i64()
                    .ok_or_else(|| bad("integer n_variables required"))?;
                if !(1..=256).contains(&n) {
                    return Err(bad("n_variables must be 1..256"));
                }
                if let Some(edges) = value["data"]["edges"].as_array() {
                    for e in edges {
                        let edge = e
                            .as_array()
                            .ok_or_else(|| bad("edge must be [u,v,weight]"))?;
                        if edge.len() != 3
                            || edge[..2]
                                .iter()
                                .any(|x| x.as_i64().is_none_or(|i| i < 0 || i >= n))
                            || edge[2].as_f64().is_none()
                        {
                            return Err(bad("invalid weighted edge"));
                        }
                    }
                }
            }
            "solutions" => {
                let pid = parse_uuid(value["problem_id"].as_str().unwrap_or(""))?;
                let n: Option<i32> =
                    sqlx::query_scalar("SELECT n_variables FROM problems WHERE id=$1")
                        .bind(pid)
                        .fetch_optional(&self.pool)
                        .await?;
                let state = BitString::parse(
                    value["state"]
                        .as_str()
                        .ok_or_else(|| bad("solution state required"))?,
                    n.ok_or_else(|| bad("unknown problem"))? as u16,
                )?;
                value["state"] = json!(format!(
                    "\\x{}",
                    state
                        .bytes()
                        .iter()
                        .map(|b| format!("{b:02x}"))
                        .collect::<String>()
                ));
            }
            "circuits" => {
                let id = parse_uuid(value["fingerprint_artifact_id"].as_str().unwrap_or(""))?;
                let artifact = sqlx::query("SELECT sha256,format FROM artifacts WHERE id=$1")
                    .bind(id)
                    .fetch_optional(&self.pool)
                    .await?
                    .ok_or_else(|| bad("unknown fingerprint artifact"))?;
                if value["circuit_hash"].as_str()
                    != Some(artifact.get::<String, _>("sha256").as_str())
                {
                    return Err(bad("circuit hash must match fingerprint artifact"));
                }
                let scheme = value["fingerprint_scheme"].as_str().unwrap_or("");
                if !["qasm3-sha256-v1", "artifact-sha256-v1"].contains(&scheme)
                    || (scheme == "qasm3-sha256-v1"
                        && artifact.get::<String, _>("format") != "qasm3")
                {
                    return Err(bad("unsupported fingerprint scheme or artifact format"));
                }
                if value["data"]["bound"] != true {
                    return Err(bad("store bound circuits; set data.bound=true"));
                }
            }
            "calibrations" => {
                if value["backend"].as_str().is_none_or(str::is_empty) {
                    return Err(bad("calibration requires backend"));
                }
            }
            "executions" => {
                if let Some(id) = value["calibration_id"].as_str() {
                    let backend: Option<String> =
                        sqlx::query_scalar("SELECT backend FROM calibrations WHERE id=$1")
                            .bind(parse_uuid(id)?)
                            .fetch_optional(&self.pool)
                            .await?;
                    if backend.as_deref() != value["backend"].as_str() {
                        return Err(bad("execution/calibration backend mismatch"));
                    }
                }
            }
            _ => {}
        }
        let mut tx = self.pool.begin().await?;
        if let Some(key) = &key {
            sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))")
                .bind(format!("{name}:{key}"))
                .execute(&mut *tx)
                .await?;
            if let Some(row) = sqlx::query(&format!(
                "SELECT id,request_hash FROM {name} WHERE idempotency_key=$1"
            ))
            .bind(key)
            .fetch_optional(&mut *tx)
            .await?
            {
                if row.get::<String, _>("request_hash") != request_hash {
                    return Err(AppError::Conflict(
                        "idempotency key reused with different resource".into(),
                    ));
                }
                let id = row.get("id");
                tx.commit().await?;
                return self.resource(name, id).await;
            }
        }
        let id = Uuid::new_v4();
        let sql = format!(
            "INSERT INTO {name}(id,{},idempotency_key,request_hash) SELECT $1,{},$3,$4 FROM jsonb_populate_record(NULL::{name},$2) r",
            columns.join(","),
            columns
                .iter()
                .map(|c| format!("r.{c}"))
                .collect::<Vec<_>>()
                .join(",")
        );
        sqlx::query(&sql)
            .bind(id)
            .bind(value)
            .bind(key)
            .bind(request_hash)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        self.resource(name, id).await
    }
    pub async fn resource(&self, name: &str, id: Uuid) -> Result<Value, AppError> {
        fields(name)?;
        let v: Option<Value> = sqlx::query_scalar(&format!(
            "SELECT to_jsonb(r)-'request_hash'-'idempotency_key' FROM {name} r WHERE id=$1"
        ))
        .bind(id)
        .fetch_optional(&self.pool)
        .await?;
        v.ok_or(AppError::NotFound)
    }
    pub async fn resources(
        &self,
        name: &str,
        cursor: Option<Uuid>,
        limit: i64,
    ) -> Result<Value, AppError> {
        fields(name)?;
        if !(1..=1000).contains(&limit) {
            return Err(bad("limit must be 1..1000"));
        }
        if let Some(id) = cursor {
            self.resource(name, id).await?;
        }
        let mut items:Vec<Value>=sqlx::query_scalar(&format!("SELECT to_jsonb(r)-'request_hash'-'idempotency_key' FROM {name} r WHERE $1::uuid IS NULL OR (created_at,id)<(SELECT created_at,id FROM {name} WHERE id=$1) ORDER BY created_at DESC,id DESC LIMIT $2")).bind(cursor).bind(limit+1).fetch_all(&self.pool).await?;
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
    pub async fn artifact(
        &self,
        format: &str,
        media_type: &str,
        bytes: &[u8],
    ) -> Result<Value, AppError> {
        if format.is_empty() || format.len() > 128 {
            return Err(bad("artifact format required (max 128 characters)"));
        }
        let hash = sha(bytes);
        let id = Uuid::new_v4();
        sqlx::query("INSERT INTO artifacts(id,sha256,format,media_type,content,size) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(sha256) DO NOTHING").bind(id).bind(&hash).bind(format).bind(media_type).bind(bytes).bind(bytes.len() as i64).execute(&self.pool).await?;
        let value: Value =
            sqlx::query_scalar("SELECT to_jsonb(a)-'content' FROM artifacts a WHERE sha256=$1")
                .bind(hash)
                .fetch_one(&self.pool)
                .await?;
        if value["format"] != format || value["media_type"] != media_type {
            return Err(AppError::Conflict(
                "artifact bytes already registered with different format/media type".into(),
            ));
        }
        Ok(value)
    }
}
fn parse_uuid(s: &str) -> Result<Uuid, AppError> {
    s.parse().map_err(|_| bad("invalid UUID"))
}
