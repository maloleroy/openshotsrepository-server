use osr_server::{AppState, create_router, db::Database, init_db};
use std::sync::Arc;
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "osr_server=info,tower_http=info".into()),
        )
        .init();
    let url = std::env::var("DATABASE_URL")
        .map_err(|_| "DATABASE_URL must point to PostgreSQL; see docs/deployment.md")?;
    let pool = init_db(&url).await?;
    let app = create_router(AppState {
        db: Arc::new(Database::new(pool)),
    });
    let addr = std::env::var("OSR_BIND_ADDR").unwrap_or_else(|_| "127.0.0.1:8000".into());
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!(address=%addr,"server ready");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let ctrl_c = async {
                tokio::signal::ctrl_c()
                    .await
                    .expect("install Ctrl-C handler")
            };
            #[cfg(unix)]
            {
                let mut terminate =
                    tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                        .expect("install SIGTERM handler");
                tokio::select! {_=ctrl_c=>{},_=terminate.recv()=>{}}
            }
            #[cfg(not(unix))]
            ctrl_c.await;
        })
        .await?;
    Ok(())
}
