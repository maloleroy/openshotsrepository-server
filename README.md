# Open Shots Repository Server

REST API server for the Open Shots Repository - a shared dynamic database of quantum computing circuit shots.

## Tech Stack

- **Rust** with **Axum** web framework for high performance
- **SQLite** for portable, zero-config database storage
- **SQLx** for compile-time checked SQL queries

## Quick Start

```bash
# Build and run
cargo run

# Run with custom settings
DATABASE_URL=sqlite:custom.db?mode=rwc OSR_BIND_ADDR=127.0.0.1:3000 cargo run
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/shots` | List shots (with filters) |
| POST | `/shots` | Create a new shot |
| GET | `/shots/{id}` | Get a specific shot |
| DELETE | `/shots/{id}` | Delete a shot |

### Query Parameters for `/shots`

- `backend` - Filter by backend name
- `circuit_hash` - Filter by circuit hash
- `n_qubits` - Filter by number of qubits
- `limit` - Maximum results (default: 100)

### Example Request

```bash
# Create a shot
curl -X POST http://localhost:8000/shots \
  -H "Content-Type: application/json" \
  -d '{"counts": {"00": 500, "11": 500}, "backend": "ibm_aachen", "circuit_hash": "abc123", "n_qubits": 2}'

# Query shots
curl "http://localhost:8000/shots?backend=ibm_aachen&n_qubits=2"
```

## Development

```bash
# Run tests
cargo test

# Run with logging
RUST_LOG=debug cargo run
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:osr.db?mode=rwc` | SQLite database URL |
| `OSR_BIND_ADDR` | `0.0.0.0:8000` | Server bind address |
| `RUST_LOG` | `osr_server=debug` | Log level |
