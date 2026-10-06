#!/usr/bin/env python3
"""Read the v0.1 SQLite database without changing it; import new immutable UUIDv4 records."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from openshots import OSRClient


def migrate(path, client, apply=False):
    path = path.resolve()
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    rows = list(connection.execute("SELECT * FROM shots ORDER BY timestamp,id"))
    report = {
        "source": path.name,
        "sha256": checksum,
        "rows": len(rows),
        "mode": "apply" if apply else "read_only_plan",
        "collections": [],
    }
    source = None
    if apply:
        source = client.create_resource(
            "sources",
            {
                "uri": path.name,
                "sha256": checksum,
                "data": {
                    "format": "osr-sqlite-v0.1",
                    "original_ids": "retained in collection metadata; new IDs are UUIDv4",
                },
            },
            idempotency_key=f"sqlite:{checksum}:source",
        )
    for row in rows:
        counts = json.loads(row["counts"])
        tags = json.loads(row["tags"])
        fingerprint = row["circuit_hash"]
        valid = re.fullmatch("[0-9a-f]{64}", fingerprint) is not None
        metadata = {
            "result_kind": "unknown_counts",
            "backend": row["backend"],
            "n_qubits": row["n_qubits"],
            "circuit_hash": fingerprint if valid else None,
            "circuit_association": "inferred" if valid else "unknown",
            "metadata": {
                "legacy_id": row["id"],
                "legacy_ingested_at": row["timestamp"],
                "legacy_circuit_hash": fingerprint,
                "legacy_tags": tags,
                "migration": "osr-sqlite-v0.1; original format did not record result type, hash scheme or execution time",
            },
            "sources": [
                {
                    "source_id": source["id"],
                    "locator": {"table": "shots", "id": row["id"]},
                }
            ]
            if source
            else [],
        }
        if apply:
            collection = client.store_collection(
                counts,
                metadata=metadata,
                idempotency_key=f"sqlite:{checksum}:{row['id']}",
            )
            report["collections"].append(
                {
                    "legacy_id": row["id"],
                    "id": collection.id,
                    "shots": collection.metadata["shots"],
                }
            )
    connection.close()
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError("SQLite input changed during migration")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--server", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with OSRClient(args.server, timeout=120) as client:
        report = migrate(args.database, client, args.apply)
    text = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
