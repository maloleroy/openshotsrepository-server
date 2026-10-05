#!/usr/bin/env python3
"""Read-only census of the clustering-for-qem data, without executing pickles.

Run with that project's existing environment:
  uv run --no-sync --project /path/to/clustering-for-qem python \
    scripts/audit_source_data.py /path/to/clustering-for-qem docs/source-data

Produces a gzip JSON file containing every file and inspected structure, and a
small summary JSON. Does not load notebook code, call providers, or write into
the source project. Counts and parameter values are summarized, not exported.
"""

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import pickletools
import re
import struct
import tarfile
import warnings
import zipfile

import numpy as np
import qiskit
from qiskit import qpy

EXCLUDED = {
    ".git", ".venv", "target", "__pycache__", ".ruff_cache", ".pytest_cache",
    "node_modules", ".mypy_cache",
}


def in_scope(relative):
    """The user explicitly limited data/ exploration to data/cplex/."""
    parts = Path(relative).parts
    return not parts or parts[0] != "data" or len(parts) > 1 and parts[1] == "cplex"


class Opaque:
    """Stand-in for legacy classes: retain state without importing their code."""

    def __new__(cls, *args, **kwargs):
        value = object.__new__(cls)
        value.arguments = args
        return value

    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        self.state = state


class MetadataUnpickler(pickle.Unpickler):
    def __init__(self, stream):
        super().__init__(stream)
        self.classes = set()

    def find_class(self, module, name):
        self.classes.add((module, name))
        if module in {"settings", "qtypes", "problem.dj", "stats.metrics", "stats.mis_metrics"}:
            return type(name, (Opaque,), {"source_class": module + "." + name})
        if module.startswith("networkx.classes."):
            return type(name, (Opaque,), {"source_class": module + "." + name})
        if module == "builtins" and name in {"set", "frozenset", "complex"}:
            return {"set": set, "frozenset": frozenset, "complex": complex}[name]
        raise pickle.UnpicklingError(f"unsupported class {module}.{name}")


def legacy_summary(value, depth=0):
    if depth > 8:
        return {"type": type(value).__name__}
    if isinstance(value, Opaque):
        return {"class": value.source_class,
                "arguments": legacy_summary(value.arguments, depth + 1),
                "state": legacy_summary(value.state, depth + 1) if "state" in vars(value) else None}
    if isinstance(value, dict):
        if value and all(isinstance(k, int) and isinstance(v, int) for k, v in value.items()):
            return {"type": "integer_map", "entries": len(value),
                    "key_min": min(value), "key_max_bits": max(value).bit_length(),
                    "value_min": min(value.values()), "value_max": max(value.values()),
                    "value_sum": sum(value.values())}
        return {"type": "dict", "entries": len(value), "fields": {
            str(k): "<redacted>" if str(k).lower() in {"api_key", "token", "password", "secret"}
            else legacy_summary(v, depth + 1)
            for k, v in value.items()
        }}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        if len(values) <= 12:
            return {"type": type(value).__name__, "length": len(values),
                    "items": [legacy_summary(v, depth + 1) for v in values]}
        return {"type": type(value).__name__, "length": len(values),
                "item_types": sorted({type(v).__name__ for v in values}),
                "first_item": legacy_summary(values[0], depth + 1)}
    if isinstance(value, str):
        # Only short enum names / backend names are needed; sample strings are not.
        return value if len(value) < 40 and not re.fullmatch(r"[01]+", value) else {"type": "str", "length": len(value)}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__}


def pickle_globals(path):
    """Symbolically follow the pickle stack to resolve memoized GLOBAL names."""
    unknown, marker = object(), object()
    stack, memo, globals_ = [], {}, set()
    string_count = 0
    for op, arg, _ in pickletools.genops(path.read_bytes()):
        name = op.name
        if name in {"UNICODE", "BINUNICODE", "SHORT_BINUNICODE", "BINUNICODE8", "STRING"}:
            stack.append(arg)
            string_count += 1
        elif name == "MARK":
            stack.append(marker)
        elif name in {"PUT", "BINPUT", "LONG_BINPUT"}:
            memo[int(arg)] = stack[-1]
        elif name == "MEMOIZE":
            memo[len(memo)] = stack[-1]
        elif name in {"GET", "BINGET", "LONG_BINGET"}:
            stack.append(memo[int(arg)])
        elif name == "GLOBAL":
            globals_.add(arg.replace(" ", ".", 1))
            stack.append(unknown)
        elif name == "STACK_GLOBAL":
            symbol, module = stack.pop(), stack.pop()
            globals_.add(f"{module}.{symbol}" if isinstance(module, str) and isinstance(symbol, str) else "<unresolved>")
            stack.append(unknown)
        elif name == "DUP":
            stack.append(stack[-1])
        else:
            before = [s.name for s in op.stack_before]
            if "mark" in before or "stackslice" in before:
                mark_index = max(i for i, item in enumerate(stack) if item is marker)
                fixed = before.index("mark") if "mark" in before else 0
                del stack[mark_index - fixed:]
            elif before:
                del stack[-len(before):]
            stack.extend(unknown for _ in op.stack_after)
    return {"globals": sorted(globals_), "unicode_string_count": string_count,
            "source_classes_executed": False}


def inspect_pickle(path):
    result = pickle_globals(path)
    with path.open("rb") as stream:
        reader = MetadataUnpickler(stream)
        try:
            result["legacy_summary"] = legacy_summary(reader.load())
        except pickle.UnpicklingError as error:
            result["metadata_decode_limit"] = str(error)
    return result


def json_structure(value):
    """Collect all structural paths; collapse dynamic bitstring/integer keys."""
    result = defaultdict(set)

    def visit(item, path):
        result[path].add(type(item).__name__)
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = "<bitstring>" if re.fullmatch(r"[01]{8,}", key) else key
                if re.fullmatch(r"[0-9]+", normalized):
                    normalized = "<integer>"
                visit(child, path + "." + normalized)
        elif isinstance(item, list):
            for child in item:
                visit(child, path + "[]")

    visit(value, "$")
    return {key: sorted(types) for key, types in sorted(result.items())}


def scalar_summary(value):
    if isinstance(value, np.ndarray):
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return str(value)


def circuit_record(circuit, index):
    layout = circuit.layout
    measurements = []
    active = set()
    gate_pairs = Counter()
    for instruction in circuit.data:
        qubits = tuple(circuit.find_bit(q).index for q in instruction.qubits)
        if instruction.operation.name != "barrier":
            active.update(qubits)
        if len(qubits) == 2:
            gate_pairs[(instruction.operation.name, *qubits)] += 1
        if instruction.operation.name == "measure":
            measurements.append([
                qubits[0], circuit.find_bit(instruction.clbits[0]).index,
            ])
    return {
        "index": index, "name": circuit.name,
        "num_qubits": circuit.num_qubits, "num_clbits": circuit.num_clbits,
        "active_qubits": sorted(active), "measurements": measurements,
        "qregs": [[r.name, r.size] for r in circuit.qregs],
        "cregs": [[r.name, r.size] for r in circuit.cregs],
        "depth": circuit.depth(),
        "two_qubit_depth": circuit.depth(lambda i: len(i.qubits) == 2),
        "operation_counts": dict(circuit.count_ops()),
        "two_qubit_gate_counts": [[*key, n] for key, n in sorted(gate_pairs.items())],
        "parameters": [str(p) for p in circuit.parameters],
        "metadata": circuit.metadata,
        "global_phase": scalar_summary(circuit.global_phase),
        "initial_layout": layout.initial_index_layout() if layout else None,
        "final_layout": layout.final_index_layout() if layout else None,
    }


def inspect_npz(path, raw):
    arrays = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            with archive.open(name) as stream:
                version = np.lib.format.read_magic(stream)
                shape, fortran, dtype = np.lib.format._read_array_header(stream, version)
            record = {
                "key": name.removesuffix(".npy"), "shape": list(shape),
                "dtype": str(dtype), "fortran_order": fortran,
                "uncompressed_bytes": archive.getinfo(name).file_size,
                "object_dtype": dtype.hasobject,
            }
            arrays.append(record)
    if raw:
        with np.load(path, allow_pickle=False) as archive:
            for record in arrays:
                if record["object_dtype"]:
                    continue
                a = archive[record["key"]]
                if a.ndim == 0:
                    record["scalar"] = a.item()
                elif a.dtype.kind in "buif":
                    record["min"] = a.min().item() if a.size else None
                    record["max"] = a.max().item() if a.size else None
                    record["nonfinite"] = int(np.count_nonzero(~np.isfinite(a)))
                    if a.dtype.kind == "b" or (a.size and a.min() >= 0 and a.max() <= 1):
                        record["binary_values_validated"] = True
    return {"arrays": arrays, "payload_checked": raw}


def inspect_file(path, relative):
    suffix = path.suffix.lower()
    if suffix == ".npz":
        return inspect_npz(path, relative.parts[0] in ("store", "notebooks"))
    if suffix == ".json":
        data = json.loads(path.read_text())
        result = {"structure": json_structure(data)}
        if isinstance(data, dict):
            result["keys"] = sorted(data)
            if "weights" in data and "n" in data:
                weights = data["weights"]
                result["graph"] = {
                    "n": data["n"], "matrix_rows": len(weights),
                    "row_widths": sorted({len(row) for row in weights}),
                    "edge_count_upper_triangle": sum(
                        w != 0 for i, row in enumerate(weights) for w in row[i + 1:]
                    ),
                    "weight_values": sorted({w for row in weights for w in row}),
                    "metadata": {k: v for k, v in data.items() if k != "weights" and k != "forbidden"},
                    "forbidden_count": len(data.get("forbidden", [])),
                }
            if "properties" in data:
                props = data["properties"]
                result["calibration"] = {
                    "backend_name": props.get("backend_name"),
                    "last_update_date": props.get("last_update_date"),
                    "qubit_count": len(props.get("qubits", [])),
                    "gate_count": len(props.get("gates", [])),
                    "qubit_parameter_names": sorted({p["name"] for q in props.get("qubits", []) for p in q}),
                    "gate_names": sorted({g["gate"] for g in props.get("gates", [])}),
                    "gate_parameter_names": sorted({p["name"] for g in props.get("gates", []) for p in g["parameters"]}),
                }
        return result
    if suffix == ".csv":
        with path.open(newline="") as stream:
            reader = csv.reader(stream)
            if str(relative) in {"store/index.csv", "store/time.csv", "store/clustering_patches.csv"}:
                widths = Counter(len(row) for row in reader)
                return {"header": None, "row_count": sum(widths.values()), "row_widths": dict(widths)}
            header = next(reader, [])
            widths = Counter(len(row) for row in reader)
        return {"header": header, "row_count": sum(widths.values()), "row_widths": dict(widths)}
    if suffix == ".bsb":
        with path.open("rb") as stream:
            magic = stream.read(4).decode("ascii")
            flags, codec, width, shots, unique = struct.unpack("<BBHQQ", stream.read(20))
        return {"magic": magic, "flags": flags, "codec": codec, "bit_width": width,
                "shots": shots, "unique_header": unique, "payload_checked": False}
    if suffix == ".qpy":
        with warnings.catch_warnings(record=True) as observed, path.open("rb") as stream:
            circuits = qpy.load(stream)
            result = {"circuits": [circuit_record(c, i) for i, c in enumerate(circuits)]}
            result["warnings"] = sorted({str(w.message) for w in observed})
        return result
    if suffix == ".pkl":
        return inspect_pickle(path)
    if suffix in (".qasm", ".qasm3"):
        source = path.read_text()
        return {"version": re.findall(r"OPENQASM\s+([^;]+)", source),
                "register_declarations": re.findall(r"(?:qreg|creg|qubit|bit)\b[^;]*;", source),
                "measurement_count": len(re.findall(r"\bmeasure\b", source))}
    if suffix == ".ipynb":
        nb = json.loads(path.read_text())
        return {"cell_count": len(nb["cells"]),
                "cell_types": dict(Counter(c["cell_type"] for c in nb["cells"])),
                "code_cell_indices": [i for i,c in enumerate(nb["cells"]) if c["cell_type"] == "code"]}
    if suffix == ".gph":
        lines = path.read_text().splitlines()
        return {"headers": [s for s in lines if s.startswith("p ")],
                "edge_lines": sum(s.startswith("e ") for s in lines)}
    if str(relative).startswith("backups/") and str(path).endswith(".tar.gz"):
        with tarfile.open(path, "r|gz") as archive:
            members = [{"path": m.name, "bytes": m.size, "type": m.type.decode("ascii")}
                       for m in archive]
        return {"members": members, "payload_checked": False}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output_prefix", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    files = []
    errors = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        if Path(base) == root / "data":
            dirs[:] = [d for d in dirs if d == "cplex"]
            names = []
        for name in sorted(names):
            path = Path(base) / name
            relative = path.relative_to(root)
            record = {"path": str(relative), "bytes": path.stat().st_size,
                      "extension": path.suffix.lower() or "<none>"}
            with path.open("rb") as stream:
                record["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            try:
                inspected = inspect_file(path, relative)
                if inspected is not None:
                    record["inspection"] = inspected
            except Exception as error:
                # A census must report every failed artifact and continue.
                record["inspection_error"] = f"{type(error).__name__}: {error}"
                errors.append({"path": str(relative), "error": record["inspection_error"]})
            files.append(record)
            if len(files) % 2000 == 0:
                print(f"Inspected {len(files)} files; {len(errors)} errors", flush=True)
    groups = {}
    for group in sorted({Path(f["path"]).parts[0] for f in files}):
        subset = [f for f in files if Path(f["path"]).parts[0] == group]
        groups[group] = {"file_count": len(subset), "bytes": sum(f["bytes"] for f in subset),
                         "extensions": dict(sorted(Counter(f["extension"] for f in subset).items()))}
    summary = {"root": str(root), "excluded_directory_names": sorted(EXCLUDED),
               "data_directory_scope": "data/cplex only; all other data/ content excluded by user",
               "numpy_version": np.__version__, "qiskit_version": qiskit.__version__,
               "file_count": len(files), "bytes": sum(f["bytes"] for f in files),
               "groups": groups, "inspection_errors": errors,
               "qpy_file_count": sum(f["extension"] == ".qpy" for f in files),
               "qpy_circuit_count": sum(len(f.get("inspection", {}).get("circuits", [])) for f in files)}
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(str(args.output_prefix) + "-inventory.json.gz", "wb", mtime=0) as stream:
        stream.write(json.dumps({"summary": summary, "files": files}, default=scalar_summary,
                                sort_keys=True, separators=(",", ":")).encode())
    Path(str(args.output_prefix) + "-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k:v for k,v in summary.items() if k != "groups"}, indent=2))


if __name__ == "__main__":
    main()
