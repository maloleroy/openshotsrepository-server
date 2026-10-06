CREATE INDEX collections_execution ON collections(execution_id,created_at DESC,id DESC);
CREATE INDEX collections_circuit ON collections(circuit_id,created_at DESC,id DESC);
CREATE INDEX collections_versions ON collections(supersedes_id,created_at DESC,id DESC);
