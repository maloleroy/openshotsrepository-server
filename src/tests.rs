#[cfg(test)]
mod tests {
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use serde_json::{json, Value};
    use sqlx::sqlite::SqlitePoolOptions;
    use std::sync::Arc;
    use tower::ServiceExt;

    use crate::db::Database;
    use crate::models::CreateShotRequest;
    use crate::{create_router, AppState};

    async fn setup_test_db() -> AppState {
        let pool = SqlitePoolOptions::new()
            .connect("sqlite::memory:")
            .await
            .expect("Failed to create in-memory database");

        // Run migrations
        sqlx::migrate!("./migrations")
            .run(&pool)
            .await
            .expect("Failed to run migrations");

        AppState {
            db: Arc::new(Database::new(pool)),
        }
    }

    #[tokio::test]
    async fn test_health_check() {
        let state = setup_test_db().await;
        let app = create_router(state);

        let response = app
            .oneshot(Request::builder().uri("/health").body(Body::empty()).unwrap())
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        assert_eq!(json["status"], "ok");
        assert!(json["version"].is_string());
    }

    #[tokio::test]
    async fn test_list_shots_empty() {
        let state = setup_test_db().await;
        let app = create_router(state);

        let response = app
            .oneshot(Request::builder().uri("/shots").body(Body::empty()).unwrap())
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        assert!(json.is_array());
        assert_eq!(json.as_array().unwrap().len(), 0);
    }

    #[tokio::test]
    async fn test_create_shot() {
        let state = setup_test_db().await;
        let app = create_router(state);

        let shot_data = json!({
            "counts": {"00": 500, "11": 500},
            "backend": "ibm_aachen",
            "circuit_hash": "abc123",
            "n_qubits": 2,
            "tags": {"experiment": "test"}
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/shots")
                    .header("Content-Type", "application/json")
                    .body(Body::from(shot_data.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::CREATED);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        assert!(json["id"].is_string());
        assert_eq!(json["backend"], "ibm_aachen");
        assert_eq!(json["circuit_hash"], "abc123");
        assert_eq!(json["n_qubits"], 2);
        assert_eq!(json["counts"]["00"], 500);
        assert_eq!(json["counts"]["11"], 500);
    }

    #[tokio::test]
    async fn test_get_shot() {
        let state = setup_test_db().await;
        
        // Create a shot directly in the database
        let request = CreateShotRequest {
            counts: [("00".to_string(), 100), ("11".to_string(), 100)]
                .into_iter()
                .collect(),
            backend: "test_backend".to_string(),
            circuit_hash: "test_hash".to_string(),
            n_qubits: 2,
            tags: Default::default(),
        };
        let created = state.db.create_shot(request).await.unwrap();

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri(format!("/shots/{}", created.id))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        assert_eq!(json["id"], created.id);
        assert_eq!(json["backend"], "test_backend");
    }

    #[tokio::test]
    async fn test_get_shot_not_found() {
        let state = setup_test_db().await;
        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/shots/00000000-0000-0000-0000-000000000000")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn test_delete_shot() {
        let state = setup_test_db().await;
        
        // Create a shot directly in the database
        let request = CreateShotRequest {
            counts: [("00".to_string(), 100)].into_iter().collect(),
            backend: "test_backend".to_string(),
            circuit_hash: "test_hash".to_string(),
            n_qubits: 2,
            tags: Default::default(),
        };
        let created = state.db.create_shot(request).await.unwrap();

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .method("DELETE")
                    .uri(format!("/shots/{}", created.id))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NO_CONTENT);
    }

    #[tokio::test]
    async fn test_filter_by_backend() {
        let state = setup_test_db().await;
        
        // Create shots with different backends
        for (backend, count) in [("ibm_aachen", 2), ("ibm_kyoto", 1)] {
            for _ in 0..count {
                let request = CreateShotRequest {
                    counts: [("00".to_string(), 100)].into_iter().collect(),
                    backend: backend.to_string(),
                    circuit_hash: "test_hash".to_string(),
                    n_qubits: 2,
                    tags: Default::default(),
                };
                state.db.create_shot(request).await.unwrap();
            }
        }

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/shots?backend=ibm_aachen")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        let shots = json.as_array().unwrap();
        assert_eq!(shots.len(), 2);
        for shot in shots {
            assert_eq!(shot["backend"], "ibm_aachen");
        }
    }

    #[tokio::test]
    async fn test_filter_by_n_qubits() {
        let state = setup_test_db().await;
        
        // Create shots with different qubit counts
        for n_qubits in [2, 5, 5, 10] {
            let request = CreateShotRequest {
                counts: [("00".to_string(), 100)].into_iter().collect(),
                backend: "test_backend".to_string(),
                circuit_hash: "test_hash".to_string(),
                n_qubits,
                tags: Default::default(),
            };
            state.db.create_shot(request).await.unwrap();
        }

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/shots?n_qubits=5")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        let shots = json.as_array().unwrap();
        assert_eq!(shots.len(), 2);
        for shot in shots {
            assert_eq!(shot["n_qubits"], 5);
        }
    }

    #[tokio::test]
    async fn test_filter_by_circuit_hash() {
        let state = setup_test_db().await;
        
        // Create shots with different circuit hashes
        for hash in ["hash_a", "hash_a", "hash_b"] {
            let request = CreateShotRequest {
                counts: [("00".to_string(), 100)].into_iter().collect(),
                backend: "test_backend".to_string(),
                circuit_hash: hash.to_string(),
                n_qubits: 2,
                tags: Default::default(),
            };
            state.db.create_shot(request).await.unwrap();
        }

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/shots?circuit_hash=hash_a")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        let shots = json.as_array().unwrap();
        assert_eq!(shots.len(), 2);
    }

    #[tokio::test]
    async fn test_limit_results() {
        let state = setup_test_db().await;
        
        // Create 10 shots
        for _ in 0..10 {
            let request = CreateShotRequest {
                counts: [("00".to_string(), 100)].into_iter().collect(),
                backend: "test_backend".to_string(),
                circuit_hash: "test_hash".to_string(),
                n_qubits: 2,
                tags: Default::default(),
            };
            state.db.create_shot(request).await.unwrap();
        }

        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/shots?limit=3")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let json: Value = serde_json::from_slice(&body).unwrap();

        let shots = json.as_array().unwrap();
        assert_eq!(shots.len(), 3);
    }
}
