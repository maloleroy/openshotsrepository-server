#!/usr/bin/env python3
"""Supplement the scoped source census with observed sample statistics.

Uses the source project's existing NumPy environment. Writes only to the output
directory. Excludes figures/, backups/ and store/hex_swaps_mis_shots_bad/.
"""

import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

from audit_source_data import scalar_summary, source_files


def sample_stats(array):
    packed = np.ascontiguousarray(array, dtype=np.uint8)
    rows = packed.view(np.dtype((np.void, packed.shape[1]))).ravel()
    _, counts = np.unique(rows, return_counts=True)
    return {"shots": len(rows), "unique_samples": len(counts),
            "max_occurrences": int(counts.max()) if len(counts) else 0,
            "ordered_packed_sha256": hashlib.sha256(packed.tobytes()).hexdigest()}


def scan_samples(root):
    records = []
    store_files = [p for p in source_files(root) if p.relative_to(root).parts[0] == "store"]
    for path in sorted(p for p in store_files if p.suffix == ".npz"):
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
    for path in sorted(p for p in store_files if p.suffix == ".bsb"):
        strings = module.load_bsb(path)
        counts = Counter(strings)
        records.append({"path": str(path.relative_to(root)), "key": None, "index": None,
                        "bit_width": len(strings[0]), "shots": len(strings),
                        "unique_samples": len(counts), "max_occurrences": max(counts.values()),
                        "binary_values_validated": all(set(s) <= {"0", "1"} and len(s) == len(strings[0]) for s in strings),
                        "payload_decoded": True})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    samples = scan_samples(root)
    result = {"samples": samples}
    with gzip.GzipFile(str(args.output), "wb", mtime=0) as stream:
        stream.write(json.dumps(result, default=scalar_summary, sort_keys=True, separators=(",", ":")).encode())
    print(json.dumps({"sample_collections": len(samples), "gross_observations": sum(r["shots"] for r in samples)}))


if __name__ == "__main__":
    main()
