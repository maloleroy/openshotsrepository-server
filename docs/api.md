# OSR 0.2 HTTP API

JSON is UTF-8. IDs are UUIDv4. SHA256 values are lowercase hex. Application errors contain `{"error":"..."}` with HTTP 400 (invalid input/links), 404 (missing resource), 409 (conflicting replay/immutable resource), 413 (use bulk API), or 500 (internal failure). Axum extraction errors use text responses: invalid query fields return 400; JSON that does not match a request type returns 422. Query parameters and typed collection/cache/source-link fields reject unknown names; extensible scientific metadata belongs in metadata/data/locator objects. Published records have no edit/delete operations; create a correction with `supersedes_id`.

## Definitions

`POST /artifacts?format=qpy&media_type=application/octet-stream` takes original raw bytes and returns ID, SHA256, size and timestamps. `GET /artifacts/{id}` returns bytes and `X-Content-SHA256`. Identical bytes are deduplicated; conflicting format/media type for identical bytes is rejected.

`POST /{resource}` and `GET /{resource}/{id}` support:

| Resource | Required fields | Optional links/metadata |
| --- | --- | --- |
| sources | uri, sha256 | artifact_id, data |
| problems | kind, n_variables | data: weighted edges `[u,v,weight]`, vertex ordering, constraints/objective |
| solutions | problem_id, state, status | objective, data: solver/evidence/source |
| circuits | circuit_hash, fingerprint_scheme, fingerprint_artifact_id, n_qubits, n_clbits | problem_id, qaoa_p, data (bound must be true) |
| calibrations | backend | measured_at, data: partial/complete properties, units, physical wires, per-property times |
| executions | data (defaults to {}) | backend, circuit_id, calibration_id, job_id, executed_at |

Definition `data` is an extensible object. Problem-specific objective conventions and parameter names belong there. Solutions accept a fixed-width binary state, matching the problem width, and return a 32-byte little-endian PostgreSQL bytea hex representation. Status is candidate/feasible/optimal/target/forbidden; membership of a solver pool does not establish optimality. Numeric objective is optional. Circuits require a bound artifact: `qasm3-sha256-v1` fingerprints the QASM3 artifact, or `artifact-sha256-v1` identifies the bytes of another declared format. Original QPY archive and circuit index can be retained in data.

`GET /{resource}?limit=100&cursor={id}` returns `{"items":[...],"next_cursor":null|uuid}`. Resource filters include problem_id/status for solutions, problem_id/circuit_hash/qaoa_p for circuits, backend for executions/calibrations, and kind/n_variables for problems. Unsupported filters return 400. Pagination follows immutable `(created_at,id)` descending order. Use returned cursors; do not derive them from a client clock.

An optional `Idempotency-Key` on definition POSTs and upload creation gives safe retries. Reusing a key for different content returns 409. Without a key, equal execution metadata creates separate executions.

## Collections

`POST /uploads` takes:

```json
{
  "result_kind": "hardware_counts",
  "n_qubits": 49,
  "backend": "ibm_boston",
  "circuit_hash": "<64 lowercase hex characters>",
  "circuit_association": "confirmed",
  "circuit_id": "<optional UUID>",
  "problem_id": "<optional UUID>",
  "execution_id": "<optional UUID>",
  "calibration_id": "<optional UUID>",
  "qaoa_p": 3,
  "executed_at": null,
  "metadata": {"layout": {"logical_to_physical": [0,1,2]}},
  "sources": [{"source_id":"<UUID>","locator":{"array_key":"data_0"}}],
  "parents": [],
  "supersedes_id": null
}
```

The illustrative layout is incomplete; supplied mappings must describe their basis and completeness. Fields with unavailable values may be omitted or null. `n_qubits` means measured classical width, 1–256, for compatibility with the original client. Circuit quantum width is separate. `executed_at` must be a timestamp with timezone, not an inferred filename date. Circuit association is confirmed/inferred/unknown; confirmed requires a fingerprint. Related circuits/executions/calibrations must agree with collection metadata. Source links and parents are validated by foreign keys.

Counts kinds: hardware_counts, simulation_counts, mitigated_counts, generated_counts, unknown_counts. Float kinds: probabilities, quasi_probabilities, weighted_samples. Hardware counts require a backend. Probability sums must be one within 1e-9; only quasi-probabilities may be negative. Source shot order is not stored.

Upload creation returns `{"id":"...","completed":false}`. Send OSB1 bytes (see database-design.md) to `PUT /uploads/{id}/chunks/{index}`. Indices start at zero. Uploading the same chunk again succeeds; different bytes at that index conflict. Inspect `GET /uploads/{id}` to resume. `POST /uploads/{id}/finalize` publishes all chunks and links atomically and returns collection metadata. Repeating finalization returns the same published collection. Incomplete/invalid staging never appears in result queries.

Use `POST /uploads/{id}/finalize?expected_chunks=N` to assert the intended number of blocks. The check occurs under the publication lock and rejects a mismatch with HTTP 409, including on an already published replay. This prevents a shorter retry from publishing extra chunks left by an interrupted request. The Python client and JSON convenience uploads always supply this assertion. Custom importers should supply it too; omitting it explicitly publishes all staged blocks. `N` must be positive.

`POST /collections` is a convenience form of the same metadata plus either `counts` or `weights` as fixed-width binary-string keys. It accepts up to 100,000 entries. Larger inputs use binary uploads.

`GET /collections` returns paginated metadata, excluding superseded versions by default. Set include_superseded=true to inspect history; GET by ID always retains access to original versions. Filters: backend, circuit_hash, n_qubits, result_kind (`counts` means all integer count kinds), circuit_association, problem_id, execution_id, circuit_id, supersedes_id, qaoa_p, calibration_id, executed_after, executed_before. Unknown execution times are excluded by time filters. `GET /collections/{id}` includes source/parent links. `GET /collections/{id}/chunks/{index}` returns OSB1 bytes and X-Content-SHA256. Metadata contains entries, shots (null for weights), weight_sum (null for counts) and chunk count. Chunks are sorted and disjoint. Client readers verify checksums, shape/order and counts totals.

Small compatibility endpoints remain: `POST /shots`, `GET /shots`, `GET /shots/{id}`. Their timestamp is ingestion time. Use the collection API for large results and full provenance. `/shots` reads fail explicitly above the response budget instead of returning truncated histograms.

## Cache

`POST /cache/sample` accepts backend, circuit_hash, n_qubits, shots, plus optional executed_after/executed_before/calibration_id. It searches only confirmed raw hardware counts matching that exact backend/fingerprint/width. It samples exactly the requested number of observations without replacement across eligible historical runs. Runs remain separate in storage. It returns either `{"hit":false,"available_shots":...}` or hit=true, counts, shots, available_shots, collection_ids and shot_order="reconstructed_from_counts". The reconstructed order does not reproduce the hardware's original chronology.
