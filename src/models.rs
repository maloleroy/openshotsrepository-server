use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use std::collections::HashMap;

/// Internal shot representation stored in database
#[derive(Debug, Clone, FromRow)]
pub struct Shot {
    pub id: String,
    pub counts: String, // JSON-encoded counts
    pub backend: String,
    pub circuit_hash: String,
    pub n_qubits: i32,
    pub timestamp: DateTime<Utc>,
    pub tags: String, // JSON-encoded tags
}

/// Request body for creating a new shot
#[derive(Debug, Deserialize)]
pub struct CreateShotRequest {
    pub counts: HashMap<String, i64>,
    pub backend: String,
    pub circuit_hash: String,
    pub n_qubits: i32,
    #[serde(default)]
    pub tags: HashMap<String, serde_json::Value>,
}

/// API response for a shot
#[derive(Debug, Serialize)]
pub struct ShotResponse {
    pub id: String,
    pub counts: HashMap<String, i64>,
    pub backend: String,
    pub circuit_hash: String,
    pub n_qubits: i32,
    pub timestamp: DateTime<Utc>,
    pub tags: HashMap<String, serde_json::Value>,
}

impl From<Shot> for ShotResponse {
    fn from(shot: Shot) -> Self {
        let counts: HashMap<String, i64> =
            serde_json::from_str(&shot.counts).unwrap_or_default();
        let tags: HashMap<String, serde_json::Value> =
            serde_json::from_str(&shot.tags).unwrap_or_default();

        ShotResponse {
            id: shot.id,
            counts,
            backend: shot.backend,
            circuit_hash: shot.circuit_hash,
            n_qubits: shot.n_qubits,
            timestamp: shot.timestamp,
            tags,
        }
    }
}
