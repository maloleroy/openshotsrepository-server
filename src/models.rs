use crate::error::{AppError, bad};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use uuid::Uuid;
fn object() -> Value {
    json!({})
}
fn kind() -> String {
    "hardware_counts".into()
}
fn association() -> String {
    "unknown".into()
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CollectionInput {
    #[serde(default = "kind")]
    pub result_kind: String,
    pub n_qubits: i32,
    pub backend: Option<String>,
    pub circuit_hash: Option<String>,
    #[serde(default = "association")]
    pub circuit_association: String,
    pub circuit_id: Option<Uuid>,
    pub problem_id: Option<Uuid>,
    pub execution_id: Option<Uuid>,
    pub calibration_id: Option<Uuid>,
    pub qaoa_p: Option<i32>,
    pub executed_at: Option<DateTime<Utc>>,
    pub supersedes_id: Option<Uuid>,
    #[serde(default = "object")]
    pub metadata: Value,
    #[serde(default)]
    pub sources: Vec<SourceLink>,
    #[serde(default)]
    pub parents: Vec<Uuid>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SourceLink {
    pub source_id: Uuid,
    #[serde(default = "object")]
    pub locator: Value,
}
impl CollectionInput {
    pub fn counts(&self) -> bool {
        self.result_kind.ends_with("_counts")
    }
    pub fn validate(&self) -> Result<(), AppError> {
        if !(1..=256).contains(&self.n_qubits) {
            return Err(bad("n_qubits must be 1..256 measured bits"));
        }
        if ![
            "hardware_counts",
            "simulation_counts",
            "mitigated_counts",
            "generated_counts",
            "unknown_counts",
            "probabilities",
            "quasi_probabilities",
            "weighted_samples",
        ]
        .contains(&self.result_kind.as_str())
        {
            return Err(bad("unknown result_kind"));
        }
        if !["confirmed", "inferred", "unknown"].contains(&self.circuit_association.as_str()) {
            return Err(bad("invalid circuit_association"));
        }
        if self.circuit_hash.as_ref().is_some_and(|s| !valid_hash(s)) {
            return Err(bad("circuit_hash must be lowercase SHA256"));
        }
        if self.circuit_association == "confirmed" && self.circuit_hash.is_none() {
            return Err(bad("confirmed association requires circuit_hash"));
        }
        if self.result_kind == "hardware_counts"
            && self.backend.as_ref().is_none_or(|s| s.is_empty())
        {
            return Err(bad("hardware counts require backend"));
        }
        if self.qaoa_p.is_some_and(|p| p < 1) || !self.metadata.is_object() {
            return Err(bad("invalid p or metadata"));
        }
        Ok(())
    }
}
pub fn valid_hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Default, Deserialize)]
pub struct CollectionQuery {
    #[serde(default)]
    pub include_superseded: bool,
    pub backend: Option<String>,
    pub circuit_hash: Option<String>,
    pub n_qubits: Option<i32>,
    pub result_kind: Option<String>,
    pub circuit_association: Option<String>,
    pub problem_id: Option<Uuid>,
    pub execution_id: Option<Uuid>,
    pub circuit_id: Option<Uuid>,
    pub supersedes_id: Option<Uuid>,
    pub calibration_id: Option<Uuid>,
    pub qaoa_p: Option<i32>,
    pub executed_after: Option<DateTime<Utc>>,
    pub executed_before: Option<DateTime<Utc>>,
    pub cursor: Option<Uuid>,
    pub limit: Option<i64>,
}
#[derive(Deserialize)]
pub struct CacheInput {
    pub backend: String,
    pub circuit_hash: String,
    pub n_qubits: i32,
    pub shots: u32,
    pub executed_after: Option<DateTime<Utc>>,
    pub executed_before: Option<DateTime<Utc>>,
    pub calibration_id: Option<Uuid>,
}

#[derive(Default, Deserialize)]
pub struct ResourceQuery {
    pub cursor: Option<Uuid>,
    pub limit: Option<i64>,
    pub problem_id: Option<Uuid>,
    pub status: Option<String>,
    pub backend: Option<String>,
    pub kind: Option<String>,
    pub circuit_hash: Option<String>,
    pub n_variables: Option<i32>,
    pub qaoa_p: Option<i32>,
}
