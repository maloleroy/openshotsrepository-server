-- PostgreSQL 15+. UUIDs are generated as v4 in Rust, never from content hashes.
CREATE TABLE artifacts (
    id UUID PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    format TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content BYTEA NOT NULL,
    size BIGINT NOT NULL CHECK (size >= 0 AND size = octet_length(content)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE sources (
    id UUID PRIMARY KEY,
    uri TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    artifact_id UUID REFERENCES artifacts(id),
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX sources_hash ON sources(sha256);

CREATE TABLE problems (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL,
    n_variables INTEGER NOT NULL CHECK (n_variables BETWEEN 1 AND 256),
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX problems_kind_size ON problems(kind, n_variables);

CREATE TABLE solutions (
    id UUID PRIMARY KEY,
    problem_id UUID NOT NULL REFERENCES problems(id),
    state BYTEA NOT NULL CHECK (octet_length(state) = 32),
    status TEXT NOT NULL CHECK (status IN ('candidate','feasible','optimal','target','forbidden')),
    objective DOUBLE PRECISION,
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX solutions_problem_status ON solutions(problem_id, status, created_at DESC, id DESC);

CREATE TABLE circuits (
    id UUID PRIMARY KEY,
    circuit_hash TEXT NOT NULL CHECK (circuit_hash ~ '^[0-9a-f]{64}$'),
    fingerprint_scheme TEXT NOT NULL,
    fingerprint_artifact_id UUID NOT NULL REFERENCES artifacts(id),
    n_qubits INTEGER NOT NULL CHECK (n_qubits > 0),
    n_clbits INTEGER NOT NULL CHECK (n_clbits BETWEEN 0 AND 256),
    problem_id UUID REFERENCES problems(id),
    qaoa_p INTEGER CHECK (qaoa_p > 0),
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX circuits_fingerprint ON circuits(circuit_hash, fingerprint_scheme);
CREATE INDEX circuits_problem_p ON circuits(problem_id, qaoa_p);

CREATE TABLE calibrations (
    id UUID PRIMARY KEY,
    backend TEXT NOT NULL,
    measured_at TIMESTAMPTZ,
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX calibrations_backend_time ON calibrations(backend, measured_at DESC, id DESC);

CREATE TABLE executions (
    id UUID PRIMARY KEY,
    backend TEXT,
    circuit_id UUID REFERENCES circuits(id),
    calibration_id UUID REFERENCES calibrations(id),
    job_id TEXT,
    executed_at TIMESTAMPTZ,
    data JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX executions_job ON executions(backend, job_id);

CREATE TABLE uploads (
    id UUID PRIMARY KEY,
    metadata JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE upload_chunks (
    upload_id UUID NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    payload BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    entries INTEGER NOT NULL CHECK (entries BETWEEN 1 AND 65536),
    shots BIGINT CHECK (shots > 0),
    weight_sum DOUBLE PRECISION,
    first_state BYTEA NOT NULL CHECK (octet_length(first_state) = 32),
    last_state BYTEA NOT NULL CHECK (octet_length(last_state) = 32),
    PRIMARY KEY (upload_id, chunk_index)
);

CREATE TABLE collections (
    id UUID PRIMARY KEY,
    result_kind TEXT NOT NULL CHECK (result_kind IN (
        'hardware_counts','simulation_counts','mitigated_counts','generated_counts','unknown_counts',
        'probabilities','quasi_probabilities','weighted_samples'
    )),
    n_qubits INTEGER NOT NULL CHECK (n_qubits BETWEEN 1 AND 256),
    backend TEXT,
    circuit_hash TEXT,
    circuit_association TEXT NOT NULL CHECK (circuit_association IN ('confirmed','inferred','unknown')),
    circuit_id UUID REFERENCES circuits(id),
    problem_id UUID REFERENCES problems(id),
    execution_id UUID REFERENCES executions(id),
    calibration_id UUID REFERENCES calibrations(id),
    qaoa_p INTEGER CHECK (qaoa_p > 0),
    executed_at TIMESTAMPTZ,
    supersedes_id UUID REFERENCES collections(id),
    idempotency_key TEXT UNIQUE,
    metadata JSONB NOT NULL,
    entries BIGINT NOT NULL CHECK (entries > 0),
    shots BIGINT CHECK (shots > 0),
    weight_sum DOUBLE PRECISION,
    chunks INTEGER NOT NULL CHECK (chunks > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((result_kind LIKE '%_counts') = (shots IS NOT NULL))
);
CREATE INDEX collections_cursor ON collections(created_at DESC, id DESC);
CREATE INDEX collections_cache ON collections(backend, circuit_hash, n_qubits, created_at DESC, id DESC)
    WHERE result_kind = 'hardware_counts' AND circuit_association = 'confirmed';
CREATE INDEX collections_problem_p ON collections(problem_id, qaoa_p, created_at DESC, id DESC);
CREATE INDEX collections_calibration_time ON collections(calibration_id, executed_at, created_at DESC, id DESC);
CREATE INDEX collections_kind_width ON collections(result_kind, n_qubits, created_at DESC, id DESC);

CREATE TABLE collection_chunks (
    collection_id UUID NOT NULL REFERENCES collections(id),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    payload BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    entries INTEGER NOT NULL CHECK (entries BETWEEN 1 AND 65536),
    shots BIGINT CHECK (shots > 0),
    weight_sum DOUBLE PRECISION,
    PRIMARY KEY (collection_id, chunk_index)
);
CREATE TABLE collection_sources (
    collection_id UUID NOT NULL REFERENCES collections(id),
    source_id UUID NOT NULL REFERENCES sources(id),
    locator JSONB NOT NULL,
    PRIMARY KEY (collection_id, source_id)
);
CREATE TABLE collection_parents (
    collection_id UUID NOT NULL REFERENCES collections(id),
    parent_id UUID NOT NULL REFERENCES collections(id),
    PRIMARY KEY (collection_id, parent_id),
    CHECK (collection_id <> parent_id)
);

-- Published records cannot be edited accidentally, even through a different app.
CREATE FUNCTION osr_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'OSR published records are immutable; create a new version' USING ERRCODE = '55000';
END;
$$;
DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'artifacts','sources','problems','solutions','circuits','calibrations','executions',
        'collections','collection_chunks','collection_sources','collection_parents'
    ] LOOP
        EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION osr_reject_mutation()', table_name);
    END LOOP;
END;
$$;
