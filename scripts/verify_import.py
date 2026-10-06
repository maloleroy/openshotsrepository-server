#!/usr/bin/env python3
"""Verify imported metadata and every published chunk without expanding state strings."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from openshots import OSRClient
from openshots.binary import HEADER


def verify_block(payload, width, weighted, checksum, previous=None):
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("chunk checksum mismatch")
    magic, actual, encoding, reserved, n = HEADER.unpack_from(payload)
    if (
        magic != b"OSB1"
        or actual != width
        or encoding != int(weighted)
        or reserved
        or not 1 <= n <= 65536
    ):
        raise ValueError("invalid chunk header")
    w = (width + 7) // 8
    dtype = np.dtype([("state", "u1", (w,)), ("value", "<f8" if weighted else "<u4")])
    if len(payload) != 16 + n * dtype.itemsize:
        raise ValueError("chunk length mismatch")
    rows = np.frombuffer(payload, offset=16, dtype=dtype, count=n)
    states = rows["state"]
    values = rows["value"]
    if width % 8 and np.any(states[:, -1] >> (width % 8)):
        raise ValueError("nonzero state padding")
    comparison = np.zeros(n - 1, dtype=np.int8)
    for i in range(w - 1, -1, -1):
        diff = (states[1:, i] > states[:-1, i]).astype(np.int8) - (
            states[1:, i] < states[:-1, i]
        ).astype(np.int8)
        comparison = np.where(comparison == 0, diff, comparison)
    if np.any(comparison != 1):
        raise ValueError("unordered or duplicate states")
    first = int.from_bytes(states[0].tobytes(), "little")
    last = int.from_bytes(states[-1].tobytes(), "little")
    if previous is not None and first <= previous:
        raise ValueError("overlapping blocks")
    if weighted:
        if not np.all(np.isfinite(values)):
            raise ValueError("nonfinite weights")
        total = float(values.sum())
    else:
        if np.any(values == 0):
            raise ValueError("zero counts")
        total = int(values.sum(dtype=np.uint64))
    return int(n), total, last


def verify(report, client):
    records = []
    blocks = 0
    size = 0
    observations = 0
    for record in report["collections"]:
        collection = client.get_collection(record["id"])
        meta = collection.metadata
        entries = 0
        total = 0
        last = None
        for index in range(meta["chunks"]):
            response = client.client.get(f"/collections/{collection.id}/chunks/{index}")
            response.raise_for_status()
            n, value, last = verify_block(
                response.content,
                meta["n_qubits"],
                collection.weighted,
                response.headers["X-Content-SHA256"],
                last,
            )
            entries += n
            total += value
            blocks += 1
            size += len(response.content)
        if entries != meta["entries"]:
            raise ValueError("entry total mismatch")
        expected = meta["weight_sum"] if collection.weighted else meta["shots"]
        if not (
            math.isclose(total, expected, rel_tol=1e-12, abs_tol=1e-9)
            if collection.weighted
            else total == expected
        ):
            raise ValueError("value total mismatch")
        if not collection.weighted:
            observations += total
        records.append({"id": collection.id, "entries": entries, "total": total})
        print(
            f"verified {len(records)}: {record.get('path', collection.id)}", flush=True
        )
    artifact_ids = set()
    artifact_bytes = 0
    for artifact in report.get("related_artifacts", []):
        if artifact["id"] in artifact_ids:
            continue
        payload = client.get_artifact(artifact["id"])
        if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
            raise ValueError("artifact differs from import report")
        artifact_ids.add(artifact["id"])
        artifact_bytes += len(payload)
    return {
        "collections": len(records),
        "blocks": blocks,
        "payload_bytes": size,
        "gross_observations": observations,
        "every_block_checksum_and_order_verified": True,
        "original_artifacts": len(artifact_ids),
        "artifact_bytes": artifact_bytes,
        "all_original_artifact_checksums_verified": True,
        "records": records,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path)
    p.add_argument("--server", default=None)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    with OSRClient(a.server, timeout=120.0) as client:
        result = verify(json.loads(a.report.read_text()), client)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))


if __name__ == "__main__":
    main()
