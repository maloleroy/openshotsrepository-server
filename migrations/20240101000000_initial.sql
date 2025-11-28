-- Create shots table
CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY NOT NULL,
    counts TEXT NOT NULL,  -- JSON-encoded measurement outcomes
    backend TEXT NOT NULL,
    circuit_hash TEXT NOT NULL,
    n_qubits INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '{}'
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_shots_backend ON shots(backend);
CREATE INDEX IF NOT EXISTS idx_shots_circuit_hash ON shots(circuit_hash);
CREATE INDEX IF NOT EXISTS idx_shots_n_qubits ON shots(n_qubits);
CREATE INDEX IF NOT EXISTS idx_shots_timestamp ON shots(timestamp DESC);

-- Composite index for filtered queries
CREATE INDEX IF NOT EXISTS idx_shots_backend_circuit ON shots(backend, circuit_hash);
