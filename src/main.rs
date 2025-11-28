use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::get,
    Json, Router,
};
use serde::{Deserialize, Serialize};
use sqlx::sqlite::{SqlitePool, SqlitePoolOptions};
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::info;
use uuid::Uuid;

mod db;
mod error;
mod models;
#[cfg(test)]
mod tests;

use db::Database;
use error::AppError;
use models::{CreateShotRequest, ShotResponse};

/// Application state shared across handlers
#[derive(Clone)]
pub struct AppState {
    pub db: Arc<Database>,
}

/// Query parameters for listing shots
#[derive(Debug, Deserialize)]
pub struct ShotQuery {
    pub backend: Option<String>,
    pub circuit_hash: Option<String>,
    pub n_qubits: Option<i32>,
    pub limit: Option<i64>,
}

/// Health check response
#[derive(Serialize)]
struct HealthResponse {
    status: String,
    version: String,
}

/// Create the application router
pub fn create_router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health_check))
        .route("/shots", get(list_shots).post(create_shot))
        .route("/shots/{id}", get(get_shot).delete(delete_shot))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

/// Health check endpoint
async fn health_check() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
    })
}

/// List shots with optional filtering
async fn list_shots(
    State(state): State<AppState>,
    Query(query): Query<ShotQuery>,
) -> Result<Json<Vec<ShotResponse>>, AppError> {
    let shots = state
        .db
        .list_shots(
            query.backend.as_deref(),
            query.circuit_hash.as_deref(),
            query.n_qubits,
            query.limit.unwrap_or(100),
        )
        .await?;

    let responses: Vec<ShotResponse> = shots.into_iter().map(ShotResponse::from).collect();
    Ok(Json(responses))
}

/// Create a new shot
async fn create_shot(
    State(state): State<AppState>,
    Json(request): Json<CreateShotRequest>,
) -> Result<(StatusCode, Json<ShotResponse>), AppError> {
    let shot = state.db.create_shot(request).await?;
    Ok((StatusCode::CREATED, Json(ShotResponse::from(shot))))
}

/// Get a specific shot by ID
async fn get_shot(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<ShotResponse>, AppError> {
    let shot = state.db.get_shot(id).await?;
    Ok(Json(ShotResponse::from(shot)))
}

/// Delete a shot by ID
async fn delete_shot(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<StatusCode, AppError> {
    state.db.delete_shot(id).await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Initialize the database and run migrations
pub async fn init_db(database_url: &str) -> Result<SqlitePool, sqlx::Error> {
    let pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect(database_url)
        .await?;

    // Run migrations
    sqlx::migrate!("./migrations").run(&pool).await?;

    Ok(pool)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "osr_server=debug,tower_http=debug".into()),
        )
        .init();

    // Database URL (default to SQLite file)
    let database_url =
        std::env::var("DATABASE_URL").unwrap_or_else(|_| "sqlite:osr.db?mode=rwc".to_string());

    info!("Connecting to database: {}", database_url);
    let pool = init_db(&database_url).await?;

    let state = AppState {
        db: Arc::new(Database::new(pool)),
    };

    let app = create_router(state);

    let addr = std::env::var("OSR_BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8000".to_string());
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    info!("Server listening on {}", addr);

    axum::serve(listener, app).await?;

    Ok(())
}
