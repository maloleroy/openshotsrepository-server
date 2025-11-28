use chrono::Utc;
use sqlx::sqlite::SqlitePool;
use uuid::Uuid;

use crate::error::AppError;
use crate::models::{CreateShotRequest, Shot};

/// Database operations
pub struct Database {
    pool: SqlitePool,
}

impl Database {
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// List shots with optional filtering
    pub async fn list_shots(
        &self,
        backend: Option<&str>,
        circuit_hash: Option<&str>,
        n_qubits: Option<i32>,
        limit: i64,
    ) -> Result<Vec<Shot>, AppError> {
        let mut query = String::from(
            "SELECT id, counts, backend, circuit_hash, n_qubits, timestamp, tags FROM shots WHERE 1=1",
        );
        let mut args: Vec<String> = Vec::new();

        if let Some(b) = backend {
            query.push_str(&format!(" AND backend = ?{}", args.len() + 1));
            args.push(b.to_string());
        }

        if let Some(ch) = circuit_hash {
            query.push_str(&format!(" AND circuit_hash = ?{}", args.len() + 1));
            args.push(ch.to_string());
        }

        if let Some(nq) = n_qubits {
            query.push_str(&format!(" AND n_qubits = ?{}", args.len() + 1));
            args.push(nq.to_string());
        }

        query.push_str(&format!(" ORDER BY timestamp DESC LIMIT ?{}", args.len() + 1));

        // Build the query dynamically
        let mut sql_query = sqlx::query_as::<_, Shot>(&query);

        for arg in &args {
            sql_query = sql_query.bind(arg);
        }
        sql_query = sql_query.bind(limit);

        let shots = sql_query.fetch_all(&self.pool).await?;
        Ok(shots)
    }

    /// Create a new shot
    pub async fn create_shot(&self, request: CreateShotRequest) -> Result<Shot, AppError> {
        let id = Uuid::new_v4().to_string();
        let timestamp = Utc::now();
        let counts_json = serde_json::to_string(&request.counts)?;
        let tags_json = serde_json::to_string(&request.tags)?;

        sqlx::query(
            r#"
            INSERT INTO shots (id, counts, backend, circuit_hash, n_qubits, timestamp, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&id)
        .bind(&counts_json)
        .bind(&request.backend)
        .bind(&request.circuit_hash)
        .bind(request.n_qubits)
        .bind(timestamp)
        .bind(&tags_json)
        .execute(&self.pool)
        .await?;

        Ok(Shot {
            id,
            counts: counts_json,
            backend: request.backend,
            circuit_hash: request.circuit_hash,
            n_qubits: request.n_qubits,
            timestamp,
            tags: tags_json,
        })
    }

    /// Get a shot by ID
    pub async fn get_shot(&self, id: Uuid) -> Result<Shot, AppError> {
        let shot = sqlx::query_as::<_, Shot>(
            "SELECT id, counts, backend, circuit_hash, n_qubits, timestamp, tags FROM shots WHERE id = ?",
        )
        .bind(id.to_string())
        .fetch_optional(&self.pool)
        .await?
        .ok_or(AppError::NotFound)?;

        Ok(shot)
    }

    /// Delete a shot by ID
    pub async fn delete_shot(&self, id: Uuid) -> Result<(), AppError> {
        let result = sqlx::query("DELETE FROM shots WHERE id = ?")
            .bind(id.to_string())
            .execute(&self.pool)
            .await?;

        if result.rows_affected() == 0 {
            return Err(AppError::NotFound);
        }

        Ok(())
    }
}
