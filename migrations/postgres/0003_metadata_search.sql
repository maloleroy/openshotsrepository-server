-- Explicit searches also cover inferred/unknown circuit associations and derived results.
CREATE INDEX collections_fingerprint ON collections(circuit_hash,n_qubits,created_at DESC,id DESC)
    WHERE circuit_hash IS NOT NULL;
CREATE INDEX collections_backend ON collections(backend,result_kind,created_at DESC,id DESC)
    WHERE backend IS NOT NULL;
CREATE INDEX collections_p ON collections(qaoa_p,created_at DESC,id DESC)
    WHERE qaoa_p IS NOT NULL;
DROP INDEX collections_versions;
CREATE INDEX collections_versions ON collections(supersedes_id,created_at DESC,id DESC)
    WHERE supersedes_id IS NOT NULL;
