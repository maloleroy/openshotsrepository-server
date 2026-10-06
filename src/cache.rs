use crate::{
    binary::{self, BitString, Value},
    db::Database,
    error::{AppError, bad},
    models::CacheInput,
};
use rand::Rng;
use serde_json::{Value as Json, json};
use sqlx::Row;
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;
impl Database {
    pub async fn sample(&self, input: CacheInput) -> Result<Json, AppError> {
        if !(1..=256).contains(&input.n_qubits)
            || input.shots == 0
            || input.shots > 1_000_000
            || !crate::models::valid_hash(&input.circuit_hash)
        {
            return Err(bad(
                "cache requires SHA256, width 1..256 and shots 1..1000000",
            ));
        }
        let runs=sqlx::query("SELECT id,shots FROM collections c WHERE NOT EXISTS(SELECT 1 FROM collections newer WHERE newer.supersedes_id=c.id) AND result_kind='hardware_counts' AND circuit_association='confirmed' AND backend=$1 AND circuit_hash=$2 AND n_qubits=$3 AND ($4::timestamptz IS NULL OR executed_at >= $4) AND ($5::timestamptz IS NULL OR executed_at <= $5) AND ($6::uuid IS NULL OR calibration_id=$6) ORDER BY created_at,id").bind(input.backend).bind(input.circuit_hash).bind(input.n_qubits).bind(input.executed_after).bind(input.executed_before).bind(input.calibration_id).fetch_all(&self.pool).await?;
        let total = runs.iter().try_fold(0u64, |sum, r| {
            sum.checked_add(r.get::<i64, _>("shots") as u64)
                .ok_or_else(|| bad("cache population overflow"))
        })?;
        if total < input.shots as u64 {
            return Ok(json!({"hit":false,"available_shots":total}));
        }
        // Floyd's algorithm: uniform observation positions without replacement, O(requested shots).
        let ranks = {
            let mut rng = rand::thread_rng();
            let mut selected = BTreeSet::new();
            for j in total - input.shots as u64..total {
                let candidate = rng.gen_range(0..=j);
                if !selected.insert(candidate) {
                    selected.insert(j);
                }
            }
            selected
        };
        let mut ranks = ranks.into_iter().peekable();
        let mut offset = 0u64;
        let mut counts = BTreeMap::<BitString, u32>::new();
        let mut contributors = Vec::new();
        for run in runs {
            let id: Uuid = run.get("id");
            let run_end = offset + run.get::<i64, _>("shots") as u64;
            if ranks.peek().is_none_or(|r| *r >= run_end) {
                offset = run_end;
                continue;
            }
            contributors.push(id);
            let chunks=sqlx::query("SELECT chunk_index,shots FROM collection_chunks WHERE collection_id=$1 ORDER BY chunk_index").bind(id).fetch_all(&self.pool).await?;
            for chunk in chunks {
                let end = offset + chunk.get::<i64, _>("shots") as u64;
                if ranks.peek().is_none_or(|r| *r >= end) {
                    offset = end;
                    continue;
                }
                let (bytes, _) = self.chunk(id, chunk.get("chunk_index")).await?;
                let block = binary::decode(&bytes)?;
                for (state, value) in block.entries {
                    let Value::Count(n) = value else {
                        return Err(bad("cache contains weighted block"));
                    };
                    let next = offset + n as u64;
                    while ranks.peek().is_some_and(|r| *r < next) {
                        *counts.entry(state).or_default() += 1;
                        ranks.next();
                    }
                    offset = next;
                }
            }
        }
        if ranks.peek().is_some()
            || counts.values().map(|v| *v as u64).sum::<u64>() != input.shots as u64
        {
            return Err(AppError::Conflict(
                "stored cache totals differ from payloads".into(),
            ));
        }
        let counts: BTreeMap<String, u32> = counts
            .into_iter()
            .map(|(s, n)| (s.string(input.n_qubits as u16), n))
            .collect();
        Ok(
            json!({"hit":true,"counts":counts,"shots":input.shots,"available_shots":total,"collection_ids":contributors,"shot_order":"reconstructed_from_counts"}),
        )
    }
}
