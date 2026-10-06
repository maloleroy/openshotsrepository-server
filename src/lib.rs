pub mod binary;
mod cache;
pub mod db;
pub mod error;
pub mod models;
mod resources;
use crate::{
    binary::{BitString, Value as SampleValue},
    db::Database,
    error::{AppError, bad},
    models::{CacheInput, CollectionInput, CollectionQuery, ResourceQuery},
};
use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post, put},
};
use serde::Deserialize;
use serde_json::{Value, json};
use sqlx::{PgPool, Row, postgres::PgPoolOptions};
use std::{collections::BTreeMap, sync::Arc};
use uuid::Uuid;
#[derive(Clone)]
pub struct AppState {
    pub db: Arc<Database>,
}
pub async fn init_db(url: &str) -> Result<PgPool, Box<dyn std::error::Error>> {
    let max = std::env::var("OSR_DB_CONNECTIONS")
        .unwrap_or("16".into())
        .parse()?;
    let pool = PgPoolOptions::new()
        .max_connections(max)
        .connect(url)
        .await?;
    sqlx::migrate!("./migrations/postgres").run(&pool).await?;
    Ok(pool)
}
pub fn create_router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/artifacts", post(artifact))
        .route("/artifacts/{id}", get(get_artifact))
        .route("/collections", get(list).post(create_collection))
        .route("/collections/{id}", get(collection))
        .route("/collections/{id}/chunks/{index}", get(chunk))
        .route("/uploads", post(start))
        .route("/uploads/{id}", get(upload))
        .route("/uploads/{id}/chunks/{index}", put(put_chunk))
        .route("/uploads/{id}/finalize", post(finalize))
        .route("/cache/sample", post(sample))
        .route("/shots", get(list_shots).post(create_shot))
        .route("/shots/{id}", get(shot))
        .route("/{resource}", get(resource_list).post(resource_create))
        .route("/{resource}/{id}", get(resource_get))
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .layer(tower_http::cors::CorsLayer::permissive())
        .layer(tower_http::trace::TraceLayer::new_for_http())
        .with_state(state)
}
async fn health(State(s): State<AppState>) -> Response {
    match sqlx::query("SELECT 1").execute(&s.db.pool).await {
        Ok(_) => (
            StatusCode::OK,
            Json(json!({"status":"ok","version":env!("CARGO_PKG_VERSION")})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"status":"unavailable"})),
        )
            .into_response(),
    }
}
fn key(headers: &HeaderMap) -> Result<Option<String>, AppError> {
    let key = headers
        .get("Idempotency-Key")
        .map(|v| {
            v.to_str()
                .map(str::to_owned)
                .map_err(|_| bad("invalid idempotency key"))
        })
        .transpose()?;
    if key.as_ref().is_some_and(|s| s.is_empty() || s.len() > 512) {
        return Err(bad("idempotency key must be 1..512 characters"));
    }
    Ok(key)
}
async fn start(
    State(s): State<AppState>,
    h: HeaderMap,
    Json(v): Json<CollectionInput>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.start(v, key(&h)?).await?))
}
async fn upload(State(s): State<AppState>, Path(id): Path<Uuid>) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.upload(id).await?))
}
async fn put_chunk(
    State(s): State<AppState>,
    Path((id, index)): Path<(Uuid, i32)>,
    body: Bytes,
) -> Result<StatusCode, AppError> {
    s.db.put_chunk(id, index, &body).await?;
    Ok(StatusCode::NO_CONTENT)
}
async fn finalize(
    State(s): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.finalize(id).await?))
}
async fn collection(
    State(s): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.collection(id).await?))
}
async fn list(
    State(s): State<AppState>,
    Query(q): Query<CollectionQuery>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.list(q).await?))
}
fn binary_response(bytes: Vec<u8>, hash: String) -> Response {
    (
        [
            ("Content-Type", "application/vnd.osr.osb1".to_owned()),
            ("X-Content-SHA256", hash),
        ],
        bytes,
    )
        .into_response()
}
async fn chunk(
    State(s): State<AppState>,
    Path((id, index)): Path<(Uuid, i32)>,
) -> Result<Response, AppError> {
    let (bytes, hash) = s.db.chunk(id, index).await?;
    Ok(binary_response(bytes, hash))
}
async fn sample(
    State(s): State<AppState>,
    Json(v): Json<CacheInput>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.sample(v).await?))
}
#[derive(Deserialize)]
struct ArtifactQuery {
    format: String,
    #[serde(default = "default_media")]
    media_type: String,
}
fn default_media() -> String {
    "application/octet-stream".into()
}
async fn artifact(
    State(s): State<AppState>,
    Query(q): Query<ArtifactQuery>,
    bytes: Bytes,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.artifact(&q.format, &q.media_type, &bytes).await?))
}
async fn get_artifact(
    State(s): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Response, AppError> {
    let row = sqlx::query("SELECT content,sha256,media_type FROM artifacts WHERE id=$1")
        .bind(id)
        .fetch_optional(&s.db.pool)
        .await?
        .ok_or(AppError::NotFound)?;
    let bytes: Vec<u8> = row.get("content");
    let hash: String = row.get("sha256");
    if binary::sha(&bytes) != hash {
        return Err(AppError::Conflict("artifact checksum mismatch".into()));
    }
    Ok((
        [
            ("Content-Type", row.get::<String, _>("media_type")),
            ("X-Content-SHA256", hash),
        ],
        bytes,
    )
        .into_response())
}
async fn resource_create(
    State(s): State<AppState>,
    Path(name): Path<String>,
    h: HeaderMap,
    Json(v): Json<Value>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.create_resource(&name, v, key(&h)?).await?))
}
async fn resource_get(
    State(s): State<AppState>,
    Path((name, id)): Path<(String, Uuid)>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.resource(&name, id).await?))
}
async fn resource_list(
    State(s): State<AppState>,
    Path(name): Path<String>,
    Query(q): Query<ResourceQuery>,
) -> Result<Json<Value>, AppError> {
    Ok(Json(s.db.resources(&name, q).await?))
}
async fn create_collection(
    State(s): State<AppState>,
    h: HeaderMap,
    Json(mut v): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let counts = v
        .as_object_mut()
        .ok_or_else(|| bad("expected JSON object"))?
        .remove("counts");
    let weights = v.as_object_mut().unwrap().remove("weights");
    let input: CollectionInput = serde_json::from_value(v)?;
    input.validate()?;
    let weighted = !input.counts();
    if counts.is_some() == weights.is_some() || weighted != weights.is_some() {
        return Err(bad(
            "supply exactly one counts or weights matching result_kind",
        ));
    }
    let values = counts.or(weights).unwrap();
    let values = values
        .as_object()
        .ok_or_else(|| bad("counts/weights must be an object"))?;
    if values.len() > 100_000 {
        return Err(AppError::TooLarge(
            "use binary chunk uploads for large collections".into(),
        ));
    }
    let mut entries = BTreeMap::new();
    for (k, v) in values {
        let state = BitString::parse(
            k,
            input
                .n_qubits
                .try_into()
                .map_err(|_| bad("invalid width"))?,
        )?;
        let value = if weighted {
            SampleValue::Weight(v.as_f64().ok_or_else(|| bad("numeric weights required"))?)
        } else {
            SampleValue::Count(
                v.as_u64()
                    .and_then(|n| u32::try_from(n).ok())
                    .ok_or_else(|| bad("counts must fit u32"))?,
            )
        };
        entries.insert(state, value);
    }
    if entries.is_empty() {
        return Err(bad("empty collection"));
    }
    let upload = s.db.start(input.clone(), key(&h)?).await?;
    let id = upload["id"].as_str().unwrap().parse().unwrap();
    let entries: Vec<_> = entries.into_iter().collect();
    for (i, chunk) in entries.chunks(binary::MAX_ENTRIES).enumerate() {
        s.db.put_chunk(id, i as i32, &binary::encode(input.n_qubits as u16, chunk))
            .await?;
    }
    if upload["completed"] == true
        && s.db.collection(id).await?["chunks"].as_u64()
            != Some(entries.len().div_ceil(binary::MAX_ENTRIES) as u64)
    {
        return Err(AppError::Conflict(
            "retry has different number of chunks".into(),
        ));
    }
    Ok(Json(s.db.finalize(id).await?))
}
async fn materialize(db: &Database, id: Uuid) -> Result<Value, AppError> {
    let meta = db.collection(id).await?;
    if !meta["result_kind"].as_str().unwrap().ends_with("_counts") {
        return Err(bad("use collection API for weighted results"));
    }
    if meta["entries"].as_u64().unwrap() > 100_000 {
        return Err(AppError::TooLarge(
            "use collection chunks for large results".into(),
        ));
    }
    let mut counts = BTreeMap::new();
    for i in 0..meta["chunks"].as_i64().unwrap() {
        let (bytes, _) = db.chunk(id, i as i32).await?;
        let b = binary::decode(&bytes)?;
        for (state, value) in b.entries {
            if let SampleValue::Count(n) = value {
                counts.insert(state.string(b.width), n);
            }
        }
    }
    Ok(
        json!({"id":id,"counts":counts,"backend":meta["backend"],"n_qubits":meta["n_qubits"],"circuit_hash":meta["circuit_hash"],"timestamp":meta["created_at"],"tags":meta["metadata"],"result_kind":meta["result_kind"],"executed_at":meta["executed_at"]}),
    )
}
async fn shot(State(s): State<AppState>, Path(id): Path<Uuid>) -> Result<Json<Value>, AppError> {
    Ok(Json(materialize(&s.db, id).await?))
}
async fn list_shots(
    State(s): State<AppState>,
    Query(q): Query<CollectionQuery>,
) -> Result<Json<Value>, AppError> {
    let page = s.db.list(q).await?;
    let mut values = Vec::new();
    let mut budget = 0u64;
    for meta in page["items"].as_array().unwrap() {
        if !meta["result_kind"].as_str().unwrap().ends_with("_counts") {
            continue;
        }
        budget += meta["entries"].as_u64().unwrap();
        if budget > 100_000 {
            return Err(AppError::TooLarge(
                "use paginated collections and chunks".into(),
            ));
        }
        values.push(materialize(&s.db, meta["id"].as_str().unwrap().parse().unwrap()).await?);
    }
    Ok(Json(json!(values)))
}
async fn create_shot(
    state: State<AppState>,
    h: HeaderMap,
    Json(mut v): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let tags = v
        .as_object_mut()
        .ok_or_else(|| bad("expected object"))?
        .remove("tags")
        .unwrap_or(json!({}));
    v["metadata"] = tags;
    v["result_kind"] = json!("hardware_counts");
    v["circuit_association"] = json!("confirmed");
    let Json(meta) = create_collection(state.clone(), h, Json(v)).await?;
    Ok(Json(
        materialize(&state.db, meta["id"].as_str().unwrap().parse().unwrap()).await?,
    ))
}
