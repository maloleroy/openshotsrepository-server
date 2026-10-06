# Running and deploying OSR

OSR 0.2 requires PostgreSQL 15+ and a Rust toolchain supporting edition 2024. Builds use the committed Cargo.lock. The Python client and import tools use their committed uv.lock files. No credentials, login system or rate limiter is part of the OSR API.

## Local development

```bash
cp .env.example .env
# Replace the password in both OSR_DB_PASSWORD and DATABASE_URL.
docker compose --env-file .env up -d
# Export DATABASE_URL and the other server settings from .env into your shell.
cargo run --locked
```

The PostgreSQL volume survives container restarts. PostgreSQL and OSR listen on localhost by default. The server runs forward migrations at startup, checks the database in `/health`, and drains requests on SIGTERM/Ctrl-C. It never opens `osr.db`. Database URLs are not logged.

| Setting | Default | Meaning |
| --- | --- | --- |
| DATABASE_URL | required | PostgreSQL URL |
| OSR_BIND_ADDR | 127.0.0.1:8000 | HTTP listen address |
| OSR_DB_CONNECTIONS | 16 | Connection pool maximum |
| RUST_LOG | osr_server=info,tower_http=info | Logging |

Requests are bounded at 64 MiB; OSB1 blocks contain at most 65,536 entries. JSON convenience reads/uploads are limited to 100,000 entries. These are data/memory bounds. Large datasets use the binary collection API. Cache requests permit up to one million observations.

## Debian deployment

Deployment has not been performed by this makeover. The following files prepare it:

- `deploy/osr.service`: systemd service and process isolation.
- `deploy/server.env.example`: PostgreSQL connection and binding settings.
- `deploy/Caddyfile.example`: optional HTTPS reverse proxy.

Install PostgreSQL and create a dedicated database/role that owns OSR's schema. Keep the database private. Build with `cargo build --release --locked`, install the binary as `/opt/osr/bin/osr-server`, and create an `osr` system user. Copy the environment file to `/etc/osr/server.env`, replacing the password, with root ownership and mode 0600. Install `deploy/osr.service` in `/etc/systemd/system/`, then run `systemctl daemon-reload` and `systemctl enable --now osr`. Configure the reverse proxy hostname and TLS if exposing the public service. For direct HTTP binding, set OSR_BIND_ADDR explicitly.

Only one startup instance should run migrations during a deployment; SQLx serializes migration execution. Published data is immutable, including through SQL triggers. A correction is an appended version. Use a separate administrative role and an explicit backup/restoration procedure for exceptional database maintenance.

## Backup and restore

Use `pg_dump --format=custom` for a consistent PostgreSQL backup, retaining artifacts and chunks with metadata. Test restoration into a separate database with `pg_restore`, then run the metadata/chunk integrity verifier. Restore the entire graph of records together. Do not copy container files while PostgreSQL is writing. Pending upload state is not a published result; it is safe to retain it in backups for resume.

## Verification

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
# Requires a disposable PostgreSQL instance and a role allowed to create test databases:
DATABASE_URL=postgres://postgres:...@localhost:5432/postgres \
  cargo test --locked --test repository -- --ignored
cd ../client
uv sync --locked --all-extras
uv run --locked pytest
OSR_INTEGRATION_TESTS=1 uv run --locked pytest tests/test_integration.py
```

SQLx integration tests create isolated temporary databases. They do not truncate the running repository. CI provides PostgreSQL for the server tests; client transport tests run independently, while the cross-repository integration suite needs a running OSR server.
