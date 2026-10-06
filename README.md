# Open Shots Repository server

Rust/Axum service for public quantum-computing results, backed by PostgreSQL. Results are immutable; corrections create new versions. Resource IDs are UUIDv4.

OSR stores counts for up to 256 measured bits using `[u64; 4]` states and `u32` occurrences. Large collections use sorted binary blocks instead of JSON histograms. Circuits, problem graphs and solutions, execution details, layouts, calibration and source provenance have separate linked records. Hardware, simulations and derived distributions are labeled explicitly.

## Run

```bash
# Start PostgreSQL using compose.yaml, after configuring .env (see docs/deployment.md).
DATABASE_URL=postgres://osr:password@127.0.0.1:5432/osr cargo run --locked
```

The server defaults to `127.0.0.1:8000`, runs PostgreSQL migrations and shuts down gracefully. No login, API key or rate limiter is introduced. The old SQLite file is preserved as migration input.

## Documentation

- [Database design and binary encoding](docs/database-design.md)
- [HTTP API](docs/api.md)
- [Deployment, backups and tests](docs/deployment.md)
- [Scoped source audit](docs/source-data-audit.md)
- [Import and SQLite migration](docs/importing.md)
- Python client: `../client`; its README and high-level API are preserved.

## Verify

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
# Isolated PostgreSQL integration tests; test role needs CREATE DATABASE:
DATABASE_URL=postgres://postgres:password@localhost:5432/postgres \
  cargo test --locked --test repository -- --ignored
```
