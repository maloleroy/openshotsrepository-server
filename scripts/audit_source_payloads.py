#!/usr/bin/env python3
"""Supplement the source census with sample statistics and backup versions.

Uses the source project's existing NumPy environment. Writes only to the output
directory and temporary files. Pickle inspection uses symbolic class stand-ins.
"""

import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile

import numpy as np

from audit_source_data import in_scope, inspect_file, scalar_summary


def sample_stats(array):
    packed = np.ascontiguousarray(array, dtype=np.uint8)
    rows = packed.view(np.dtype((np.void, packed.shape[1]))).ravel()
    _, counts = np.unique(rows, return_counts=True)
    return {"shots": len(rows), "unique_samples": len(counts),
            "max_occurrences": int(counts.max()) if len(counts) else 0,
            "ordered_packed_sha256": hashlib.sha256(packed.tobytes()).hexdigest()}


def scan_samples(root):
    records = []
    for path in sorted((root / "store").rglob("*.npz")):
        with np.load(path, allow_pickle=False) as archive:
            for key in archive.files:
                array = archive[key]
                if array.ndim not in (2, 3):
                    continue
                slices = enumerate(array) if array.ndim == 3 else [(None, array)]
                for index, samples in slices:
                    width = int(archive["L"]) if key == "packed" else samples.shape[1]
                    valid = bool(np.all((samples == 0) | (samples == 1))) if key != "packed" else True
                    packed = samples if key == "packed" else np.packbits(samples, axis=1, bitorder="big")
                    padding = packed.shape[1] * 8 - width
                    padding_zero = bool(np.all((packed[:, -1] & ((1 << padding) - 1)) == 0)) if padding else True
                    records.append({"path": str(path.relative_to(root)), "key": key,
                                    "index": index, "bit_width": width,
                                    "padding_bits_zero": padding_zero,
                                    "binary_values_validated": valid, **sample_stats(packed)})
        print(f"Samples: {path.relative_to(root)}", flush=True)
    module_path = root / "src/bitstring_utils.py"
    spec = importlib.util.spec_from_file_location("source_bitstring_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in sorted((root / "store").rglob("*.bsb")):
        strings = module.load_bsb(path)
        counts = Counter(strings)
        records.append({"path": str(path.relative_to(root)), "key": None, "index": None,
                        "bit_width": len(strings[0]), "shots": len(strings),
                        "unique_samples": len(counts), "max_occurrences": max(counts.values()),
                        "binary_values_validated": all(set(s) <= {"0", "1"} and len(s) == len(strings[0]) for s in strings),
                        "payload_decoded": True})
    return records


def scan_backups(root):
    backups = []
    for path in sorted((root / "backups").glob("*.tar.gz")):
        records = []
        with tempfile.TemporaryDirectory(prefix="osr-backup-audit-") as temporary:
            with tarfile.open(path, "r|gz") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    relative = Path(member.name)
                    if not in_scope(relative):
                        continue
                    stream = archive.extractfile(member)
                    data = stream.read()
                    digest = hashlib.sha256(data).hexdigest()
                    current = root / relative
                    identical = False
                    if current.is_file():
                        with current.open("rb") as live:
                            identical = hashlib.file_digest(live, "sha256").hexdigest() == digest
                    record = {"path": member.name, "bytes": member.size, "sha256": digest,
                              "current_path_exists": current.is_file(), "identical_to_current": identical}
                    if relative.name.startswith("._"):
                        record["kind"] = "appledouble_metadata"
                    elif not identical:
                        # Use a generated temporary filename; never extract archive paths.
                        staged = Path(temporary) / ("artifact" + relative.suffix)
                        staged.write_bytes(data)
                        try:
                            record["inspection"] = inspect_file(staged, relative)
                        except Exception as error:
                            record["inspection_error"] = f"{type(error).__name__}: {error}"
                        staged.unlink()
                    records.append(record)
                    if len(records) % 250 == 0:
                        print(f"Backup: {len(records)} files", flush=True)
        backups.append({"path": str(path.relative_to(root)), "files": records})
    return backups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    samples = scan_samples(root)
    backups = scan_backups(root)
    result = {"samples": samples, "backups": backups}
    with gzip.GzipFile(str(args.output), "wb", mtime=0) as stream:
        stream.write(json.dumps(result, default=scalar_summary, sort_keys=True, separators=(",", ":")).encode())
    print(json.dumps({"sample_collections": len(samples), "backup_files": sum(len(b["files"]) for b in backups)}))


if __name__ == "__main__":
    main()
