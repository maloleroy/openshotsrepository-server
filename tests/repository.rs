use osr_server::{
    AppState,
    binary::{BitString, Value, encode},
    create_router,
    db::Database,
    models::{CacheInput, CollectionInput, CollectionQuery},
};
use serde_json::json;
use sqlx::PgPool;
use std::sync::Arc;
use uuid::Uuid;
fn input(kind: &str) -> CollectionInput {
    serde_json::from_value(json!({"result_kind":kind,"n_qubits":256,"backend":"device","circuit_hash":"a".repeat(64),"circuit_association":"confirmed"})).unwrap()
}
#[sqlx::test(migrations = "./migrations/postgres")]
#[ignore = "requires DATABASE_URL pointing to a disposable PostgreSQL server"]
async fn immutable_publication_and_retries(pool: PgPool) {
    let db = Database::new(pool.clone());
    let upload = db
        .start(input("hardware_counts"), Some("source-1".into()))
        .await
        .unwrap();
    let id: Uuid = upload["id"].as_str().unwrap().parse().unwrap();
    assert_eq!(id.get_version_num(), 4);
    let low = encode(256, &[(BitString([0; 4]), Value::Count(u32::MAX))]);
    let high = encode(256, &[(BitString([u64::MAX; 4]), Value::Count(5))]);
    db.put_chunk(id, 1, &high).await.unwrap();
    assert!(db.finalize(id).await.is_err());
    assert!(db.collection(id).await.is_err());
    db.put_chunk(id, 0, &low).await.unwrap();
    let a = db.finalize(id).await.unwrap();
    assert_eq!(a["shots"], u32::MAX as u64 + 5);
    assert_eq!(a["chunks"], 2);
    assert_eq!(a, db.finalize(id).await.unwrap());
    db.put_chunk(id, 0, &low).await.unwrap();
    assert!(db.put_chunk(id, 0, &high).await.is_err());
    assert!(
        sqlx::query("UPDATE collections SET backend='other' WHERE id=$1")
            .bind(id)
            .execute(&pool)
            .await
            .is_err()
    );
    assert!(
        sqlx::query("DELETE FROM collection_chunks WHERE collection_id=$1")
            .bind(id)
            .execute(&pool)
            .await
            .is_err()
    );
    assert_eq!(
        db.start(input("hardware_counts"), Some("source-1".into()))
            .await
            .unwrap()["id"],
        a["id"]
    );
    assert!(
        db.start(input("simulation_counts"), Some("source-1".into()))
            .await
            .is_err()
    );
}
#[sqlx::test(migrations = "./migrations/postgres")]
#[ignore = "requires PostgreSQL"]
async fn probabilities_and_cross_block_order(pool: PgPool) {
    let db = Database::new(pool);
    let id: Uuid = db.start(input("quasi_probabilities"), None).await.unwrap()["id"]
        .as_str()
        .unwrap()
        .parse()
        .unwrap();
    db.put_chunk(
        id,
        0,
        &encode(
            256,
            &[
                (BitString([0; 4]), Value::Weight(-0.1)),
                (BitString([1, 0, 0, 0]), Value::Weight(1.1)),
            ],
        ),
    )
    .await
    .unwrap();
    assert_eq!(db.finalize(id).await.unwrap()["weight_sum"], 1.0);
    let id: Uuid = db.start(input("probabilities"), None).await.unwrap()["id"]
        .as_str()
        .unwrap()
        .parse()
        .unwrap();
    assert!(
        db.put_chunk(
            id,
            0,
            &encode(256, &[(BitString([0; 4]), Value::Weight(-0.1))])
        )
        .await
        .is_err()
    );
    db.put_chunk(
        id,
        0,
        &encode(256, &[(BitString([1, 0, 0, 0]), Value::Weight(0.5))]),
    )
    .await
    .unwrap();
    db.put_chunk(
        id,
        1,
        &encode(256, &[(BitString([0; 4]), Value::Weight(0.5))]),
    )
    .await
    .unwrap();
    assert!(db.finalize(id).await.is_err());
}
#[sqlx::test(migrations = "./migrations/postgres")]
#[ignore = "requires PostgreSQL"]
async fn pagination_and_cache_excludes_derived_results(pool: PgPool) {
    let db = Database::new(pool);
    for kind in [
        "hardware_counts",
        "simulation_counts",
        "mitigated_counts",
        "hardware_counts",
    ] {
        let id: Uuid = db.start(input(kind), None).await.unwrap()["id"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();
        db.put_chunk(
            id,
            0,
            &encode(
                256,
                &[
                    (BitString([0; 4]), Value::Count(10)),
                    (BitString([u64::MAX; 4]), Value::Count(10)),
                ],
            ),
        )
        .await
        .unwrap();
        db.finalize(id).await.unwrap();
    }
    let first = db
        .list(CollectionQuery {
            limit: Some(2),
            ..Default::default()
        })
        .await
        .unwrap();
    let cursor = first["next_cursor"].as_str().unwrap().parse().unwrap();
    let second = db
        .list(CollectionQuery {
            limit: Some(2),
            cursor: Some(cursor),
            ..Default::default()
        })
        .await
        .unwrap();
    assert_eq!(second["items"].as_array().unwrap().len(), 2);
    assert!(second["next_cursor"].is_null());
    let req = |shots| CacheInput {
        backend: "device".into(),
        circuit_hash: "a".repeat(64),
        n_qubits: 256,
        shots,
        executed_after: None,
        executed_before: None,
        calibration_id: None,
    };
    let sampled = db.sample(req(35)).await.unwrap();
    assert_eq!(sampled["available_shots"], 40);
    assert_eq!(
        sampled["counts"]
            .as_object()
            .unwrap()
            .values()
            .map(|n| n.as_u64().unwrap())
            .sum::<u64>(),
        35
    );
    assert_eq!(sampled["collection_ids"].as_array().unwrap().len(), 2);
    assert_eq!(db.sample(req(41)).await.unwrap()["hit"], false);
}
#[sqlx::test(migrations = "./migrations/postgres")]
#[ignore = "requires PostgreSQL"]
async fn resources_solutions_and_http_errors(pool: PgPool) {
    let db = Database::new(pool.clone());
    let problem=db.create_resource("problems",json!({"kind":"maxcut","n_variables":3,"data":{"edges":[[0,1,-2.5]],"isolated_vertices":[2]}}),Some("problem".into())).await.unwrap();
    let sol=db.create_resource("solutions",json!({"problem_id":problem["id"],"state":"101","status":"optimal","objective":2.5,"data":{"solver":"CPLEX","evidence":"source pool"}}),None).await.unwrap();
    assert_eq!(sol["state"].as_str().unwrap().len(), 66);
    assert!(
        db.create_resource(
            "solutions",
            json!({"problem_id":problem["id"],"state":"1111","status":"optimal"}),
            None
        )
        .await
        .is_err()
    );
    let app = axum_test::TestServer::new(create_router(AppState { db: Arc::new(db) })).unwrap();
    app.get("/health").await.assert_status_ok();
    app.delete(&format!("/shots/{}", Uuid::new_v4()))
        .await
        .assert_status(axum::http::StatusCode::METHOD_NOT_ALLOWED);
    app.post("/collections")
        .json(&json!({"n_qubits":257,"backend":"device","counts":{"0":1}}))
        .await
        .assert_status_bad_request();
    app.get("/collections?limit=0")
        .await
        .assert_status_bad_request();
}
