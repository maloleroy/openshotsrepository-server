use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::json;
#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("resource not found")]
    NotFound,
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    Conflict(String),
    #[error("{0}")]
    TooLarge(String),
    #[error("database error")]
    Database(#[from] sqlx::Error),
    #[error("invalid JSON")]
    Json(#[from] serde_json::Error),
}
pub fn bad(message: impl Into<String>) -> AppError {
    AppError::BadRequest(message.into())
}
impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = match &self {
            Self::NotFound => StatusCode::NOT_FOUND,
            Self::BadRequest(_) | Self::Json(_) => StatusCode::BAD_REQUEST,
            Self::Conflict(_) => StatusCode::CONFLICT,
            Self::TooLarge(_) => StatusCode::PAYLOAD_TOO_LARGE,
            Self::Database(e) => {
                if let Some(e) = e.as_database_error() {
                    match e.code().as_deref() {
                        Some("23503") | Some("23514") | Some("22P02") => StatusCode::BAD_REQUEST,
                        Some("23505") | Some("55000") => StatusCode::CONFLICT,
                        _ => {
                            tracing::error!(error=%e,"database failure");
                            StatusCode::INTERNAL_SERVER_ERROR
                        }
                    }
                } else {
                    tracing::error!(error=%e,"database failure");
                    StatusCode::INTERNAL_SERVER_ERROR
                }
            }
        };
        let message = if status == StatusCode::INTERNAL_SERVER_ERROR {
            "internal database error".to_string()
        } else {
            self.to_string()
        };
        (status, Json(json!({"error":message}))).into_response()
    }
}
