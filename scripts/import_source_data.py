#!/usr/bin/env python3
"""Import the audited result collections and their related artifacts, idempotently.

The audited file manifest is an allowlist and checksum guard. No excluded tree is
traversed. Default is a read-only plan; --apply writes through the OSR HTTP API.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from openshots import OSRClient
from openshots.binary import HEADER, MAX_ENTRIES, encode_blocks
from qiskit import qpy

from audit_source_circuits import CircuitUnpickler, locate_circuits
from audit_source_data import MetadataUnpickler, Opaque, circuit_record, in_scope

DOCS = Path(__file__).resolve().parents[1] / "docs"
POLICY = "clustering-for-qem-import-v1"


def read_json(path):
    return json.loads(
        gzip.decompress(path.read_bytes())
        if path.suffix == ".gz"
        else path.read_bytes()
    )


def safe_value(value):
    if isinstance(value, Opaque):
        return {
            "class": value.source_class,
            "arguments": safe_value(value.arguments),
            "state": safe_value(getattr(value, "state", None)),
        }
    if isinstance(value, dict):
        return {
            str(k): "<redacted>"
            if str(k).lower() in {"api_key", "token", "password", "secret"}
            else safe_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_value(x) for x in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_float": str(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported legacy metadata {type(value).__name__}")


def np_blocks(packed, width):
    """Aggregate big-endian left-aligned rows; emit OSB1 with no per-state Python objects."""
    packed = np.ascontiguousarray(packed, dtype=np.uint8)
    padding = packed.shape[1] * 8 - width
    if (
        not 1 <= width <= 256
        or packed.shape[1] != (width + 7) // 8
        or (padding and np.any(packed[:, -1] & ((1 << padding) - 1)))
    ):
        raise ValueError("invalid packed sample width/padding")
    unique, counts = np.unique(
        packed.view(np.dtype((np.void, packed.shape[1]))).ravel(), return_counts=True
    )
    if len(counts) == 0 or counts.max() > 0xFFFFFFFF:
        raise ValueError("empty samples or u32 count overflow")
    rows = unique.view(np.uint8).reshape(-1, packed.shape[1])
    counts = counts.astype("<u4")
    for start in range(0, len(rows), MAX_ENTRIES):
        states = rows[start : start + MAX_ENTRIES, ::-1].copy()
        if padding:
            shifted = states >> padding
            shifted[:, :-1] |= states[:, 1:] << (8 - padding)
            states = shifted
        records = np.empty((len(states), states.shape[1] + 4), dtype=np.uint8)
        records[:, : states.shape[1]] = states
        records[:, states.shape[1] :] = (
            counts[start : start + len(states)].view(np.uint8).reshape(-1, 4)
        )
        yield HEADER.pack(b"OSB1", width, 0, 0, len(states)) + records.tobytes()


def cycle_solutions(n, edges):
    """Exact weighted-cycle dynamic program; emit representatives, not a claim of exhaustive degeneracy."""
    adjacency = defaultdict(list)
    for a, b, w in edges:
        adjacency[a].append((b, w))
        adjacency[b].append((a, w))
    if set(adjacency) != set(range(n)) or any(len(v) != 2 for v in adjacency.values()):
        raise ValueError("not a cycle")
    order = [0]
    weights = []
    previous = -1
    current = 0
    while len(order) < n:
        nxt, w = next((b, w) for b, w in adjacency[current] if b != previous)
        if nxt in order:
            raise ValueError("disconnected cycle")
        weights.append(w)
        order.append(nxt)
        previous, current = current, nxt
    weights.append(next(w for b, w in adjacency[current] if b == 0))
    candidates = []
    for first in (0, 1):
        dp = {first: (0.0, first)}
        for i, w in enumerate(weights[:-1], 1):
            dp = {
                bit: max(
                    (value + (w if prev != bit else 0), state | (bit << order[i]))
                    for prev, (value, state) in dp.items()
                )
                for bit in (0, 1)
            }
        candidates.append(
            max(
                (value + (weights[-1] if last != first else 0), state)
                for last, (value, state) in dp.items()
            )
        )
    best = max(v for v, _ in candidates)
    return best, sorted({state for v, state in candidates if abs(v - best) < 1e-12})


class Importer:
    def __init__(self, root, client):
        self.root = root.resolve()
        self.client = client
        self.manifest = {
            r["path"]: r
            for r in read_json(DOCS / "source-data-inventory.json.gz")["files"]
        }
        self.catalog = list(csv.DictReader((DOCS / "source-shot-catalog.csv").open()))
        self.sources = {}
        self.artifacts = {}
        self.problems = {}
        self.circuits = {}
        self.circuit_archives = {}
        self.collections = {}
        self.problem_directories = {}
        self.lineage = list(
            csv.reader(io.StringIO(self.read("store/index.csv").decode()))
        )
        self.layouts = json.loads(self.read("notebooks/layouts.json"))
        tree = ast.parse(self.read("src/circuits/topology/hero.py").decode())
        self.hero = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "HERO_PATCH" for t in node.targets
            )
        )
        self.report = {
            "policy": POLICY,
            "collections": [],
            "related_artifacts": [],
            "unknowns": [],
            "skipped": [],
        }

    def read(self, relative):
        relative = str(relative)
        if (
            not in_scope(Path(relative))
            or relative not in self.manifest
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError(f"out-of-scope source: {relative}")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("source symlink escapes root")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != self.manifest[relative]["sha256"]:
            raise ValueError(
                f"source changed since audit: {relative}; refresh the scoped audit"
            )
        return payload

    def key(self, relative, locator=""):
        return f"{POLICY}:{self.manifest[str(relative)]['sha256']}:{relative}:{locator}"

    def source(self, relative, *, artifact=False, data=None):
        relative = str(relative)
        if relative not in self.sources:
            info = self.manifest[relative]
            a = self.artifact(relative) if artifact else None
            self.sources[relative] = self.client.create_resource(
                "sources",
                {
                    "uri": relative,
                    "sha256": info["sha256"],
                    "artifact_id": a["id"] if a else None,
                    "data": {"source_project": "clustering-for-qem", **(data or {})},
                },
                idempotency_key=self.key(relative, "source"),
            )
        return self.sources[relative]

    def artifact(self, relative):
        relative = str(relative)
        if relative not in self.artifacts:
            suffix = Path(relative).suffix.lstrip(".") or "text"
            self.artifacts[relative] = self.client.store_artifact(
                self.read(relative),
                format=suffix,
                media_type="application/octet-stream",
            )
            self.report["related_artifacts"].append(
                {"path": relative, **self.artifacts[relative]}
            )
        return self.artifacts[relative]

    def ancestors(self, cache):
        seen = set()
        todo = [cache]
        while todo:
            current = todo.pop()
            if current in seen or current == "no_previous_step":
                continue
            seen.add(current)
            todo.extend(r[2] for r in self.lineage if r[0] == current)
        return sorted(seen)

    def graph(self, directory):
        relative = f"{directory}/graph.json"
        if relative in self.problems:
            return self.problems[relative]
        graph = json.loads(self.read(relative))
        n = graph["n"]
        matrix = graph["weights"]
        if len(matrix) != n or any(len(row) != n for row in matrix):
            raise ValueError("invalid graph matrix")
        edges = [
            [a, b, matrix[a][b]]
            for a in range(n)
            for b in range(a + 1, n)
            if matrix[a][b] != 0
        ]
        related = [
            p
            for p in self.manifest
            if Path(p).parent == Path(directory)
            and Path(p).suffix in {".json", ".dat", ".mod", ".txt"}
        ]
        artifacts = [
            {"path": p, "artifact_id": self.artifact(p)["id"]} for p in sorted(related)
        ]
        problem = self.client.create_resource(
            "problems",
            {
                "kind": "mis" if "/mis/" in relative else "maxcut",
                "n_variables": n,
                "data": {
                    "edges": edges,
                    "vertex_order": list(range(n)),
                    "objective_sense": "maximize",
                    "graph_source": self.source(relative, artifact=True)["id"],
                    "original_graph": graph,
                    "related_artifacts": artifacts,
                    "forbidden_semantics": "additional source constraints; not observed results",
                    "matrix_convention": "upper_triangle",
                },
            },
            idempotency_key=self.key(relative, "problem"),
        )
        self.problems[relative] = problem
        self.problem_directories[directory] = problem
        for i, state in enumerate(graph.get("forbidden", [])):
            self.solution(
                problem,
                state[::-1],
                "forbidden",
                None,
                {"source": relative, "source_order": "variable zero first"},
                self.key(relative, f"forbidden:{i}"),
            )
        pool = f"{directory}/pool_solutions.json"
        if pool in self.manifest:
            for i, row in enumerate(json.loads(self.read(pool))):
                self.solution(
                    problem,
                    row["solution"][::-1],
                    "candidate",
                    row.get("objective"),
                    {
                        "source": pool,
                        "pool_id": row.get("pool_id"),
                        "source_order": "variable zero first",
                        "optimality": "not established by pool membership",
                    },
                    self.key(pool, f"solution:{i}"),
                )
        return problem

    def solution(self, problem, state, status, objective, data, key):
        return self.client.create_resource(
            "solutions",
            {
                "problem_id": problem["id"],
                "state": state,
                "status": status,
                "objective": objective,
                "data": data,
            },
            idempotency_key=key,
        )

    def ring_problem(self, relative, graph_path=None):
        if relative in self.problems:
            return self.problems[relative]
        if graph_path:
            graph = MetadataUnpickler(io.BytesIO(self.read(graph_path))).load().state
            nodes = graph["_node"]
            n = len(nodes)
            edges = [
                [int(a), int(b), attrs.get("weight", 1)]
                for a, neighbours in graph["_adj"].items()
                for b, attrs in neighbours.items()
                if int(a) < int(b)
            ]
            provenance = {"graph_source": self.source(graph_path, artifact=True)["id"]}
        else:
            n = 24
            edges = [[i, (i + 1) % n, 3 if i < 4 else -1] for i in range(n)]
            provenance = {
                "definition_source": self.source("src/benchmark/notebook.py")["id"],
                "definition": "problem(MAX_CUT_RING): signed 24-cycle, first four weights +3, others -1",
            }
        problem = self.client.create_resource(
            "problems",
            {
                "kind": "maxcut",
                "n_variables": n,
                "data": {
                    "edges": edges,
                    "vertex_order": list(range(n)),
                    "objective_sense": "maximize",
                    **provenance,
                },
            },
            idempotency_key=self.key(
                graph_path or "src/benchmark/notebook.py", f"problem:{relative}"
            ),
        )
        self.problems[relative] = problem
        objective, solutions = cycle_solutions(n, edges)
        for state in solutions:
            self.solution(
                problem,
                format(state, f"0{n}b"),
                "optimal",
                objective,
                {
                    "evidence": "exact weighted-cycle dynamic program",
                    "algorithm": POLICY,
                    "coverage": "representatives; possibly other equally optimal solutions",
                },
                self.key(
                    graph_path or "src/benchmark/notebook.py",
                    f"optimal:{relative}:{state}",
                ),
            )
        return problem

    def archive(self, relative, problem=None, p_values=None, only_index=None):
        """Preserve the submitted/archive bytes and explicit circuit index, with QASM3 fingerprints."""
        cache = (
            relative,
            problem["id"] if problem else None,
            tuple(p_values or []),
            only_index,
        )
        if cache in self.circuit_archives:
            return self.circuit_archives[cache]
        raw = self.read(relative)
        original = self.artifact(relative)
        source = self.source(relative, artifact=True)
        circuits = qpy.load(io.BytesIO(raw))
        records = []
        for i, qc in enumerate(circuits):
            if only_index is not None and i != only_index:
                continue
            p = p_values[i] if p_values and i < len(p_values) else None
            data = {
                "source_id": source["id"],
                "original_artifact_id": original["id"],
                "circuit_index": i,
                "archive_format": "qpy",
                "decoder_qiskit": __import__("qiskit").__version__,
                "circuit_structure": circuit_record(qc, i),
            }
            data["circuit_structure"].pop("parameters", None)
            record = self.client.store_circuit(
                qc,
                problem_id=problem["id"] if problem else None,
                qaoa_p=p,
                data=data,
                idempotency_key=self.key(relative, f"circuit:{cache[1]}:{i}:{p}"),
            )
            records.append(record)
        self.circuit_archives[cache] = records
        return records

    def layout(self, path):
        if path in self.layouts:
            return {
                "logical_to_physical": self.layouts[path],
                "evidence": "notebooks/layouts.json exact key",
                "confidence": "confirmed",
            }
        family = Path(path).parts[1]
        if family == "ring_swaps":
            return {
                "logical_to_physical": self.layouts["store/ring_swaps"],
                "evidence": "user confirmation: notebooks/layouts.json[store/ring_swaps]",
                "confidence": "confirmed",
            }
        if family in {"hex_shots", "hex_swaps_shots", "hex_swaps_mis_shots"}:
            return {
                "logical_to_physical": self.hero,
                "evidence": "src/benchmark/notebook.py and HERO_PATCH",
                "confidence": "analysis_mapping",
            }
        if family == "cplex_shots":
            key = next(k for k in self.layouts if path in k)
            return {
                "logical_to_physical": self.layouts[key],
                "evidence": "layouts.json joined April file key",
                "confidence": "confirmed",
            }
        peaked = next(k for k in self.layouts if "/49Q_shots/" in k)
        if family == "peaked_hard":
            return {
                "logical_to_physical": self.layouts[peaked][::-1],
                "analysis_alternative": self.layouts[peaked],
                "evidence": "benchmark loader reverses mapping; Peaked_circuits notebook uses unreversed mapping",
                "confidence": "analysis_mapping",
            }
        if family == "49Q_shots" and "202510" in path:
            return {
                "logical_to_physical": self.layouts[peaked],
                "evidence": "October 29 mapping reused for analysis; acquisition not established for Oct 30/31",
                "confidence": "inferred",
            }
        return {"logical_to_physical": None, "confidence": "unknown"}

    def association(self, row):
        path = row["path"]
        family = Path(path).parts[1]
        name = Path(path).name
        index = (
            int(row["index"])
            if row["index"]
            else int(row["key"].split("_")[-1])
            if row["key"].startswith("data_")
            else 0
        )
        problem = None
        p = None
        archive = None
        archive_index = 0
        ps = None
        extras = {}
        if family in {"100Q_max_cut", "24Q_max_cut"}:
            if "20260305" in path:
                p = [1, 5, 10, 15, 20, 25, 30][index]
                problem = self.ring_problem(path)
                archive = f"store/ring_circuits/ring_24q_p{p}.qpy"
                ps = [p]
            else:
                ps = [1, 2, 3, 4, 5, 7, 10, 15, 30]
                p = ps[index]
                stem = (
                    "ring_100q_p1to30_balanced"
                    if family == "100Q_max_cut"
                    else "ring_24q_p1to30_4solutions"
                )
                graph = (
                    "graph_100q_balanced"
                    if family == "100Q_max_cut"
                    else "graph_4solutions"
                )
                problem = self.ring_problem(path, f"store/ring_circuits/{graph}.pkl")
                archive = f"store/ring_circuits/{stem}.qpy"
                archive_index = index
        elif family == "cplex_shots":
            p = int(re.search(r"_P(\d+)_", name)[1])
            problem = self.graph("data/cplex/maxcut")
            extras["candidate_circuit_artifacts"] = [
                self.artifact(f)["id"]
                for f in sorted(self.manifest)
                if f.startswith("store/cplex_circuits/") and f.endswith(".qpy")
            ]
            # Multiple compilation variants: preserve each; do not choose a submitted version.
            for f in sorted(self.manifest):
                if f.startswith("store/cplex_circuits/") and f.endswith(".qpy"):
                    self.archive(f, problem)
        elif family == "hex_shots":
            idx = int(re.search(r"run(\d+)", name)[1])
            seed = 1 if "seed1" in name else idx + 1
            p = idx + 1 if "seed1" in name else 1
            directory = f"data/cplex/maxcut/hex/{seed}"
            problem = self.graph(directory)
            if "seed1" in name:
                archive = directory + "/hex_seed1_p1to10.qpy"
                ps = list(range(1, 11))
                archive_index = idx
            else:
                archive = "data/cplex/maxcut/hex/hex_circuits.qpy"
                archive_index = idx
                extras["seed"] = seed
        elif family == "hex_swaps_shots":
            if "single_graph" in name:
                idx = int(re.search(r"graph(\d+)_202", name)[1])
                seed = 2
                p = idx + 1
                archive = "data/cplex/maxcut/hex_swaps/hex_swaps_circuits.qpy"
                ps = list(range(1, 11))
                archive_index = idx
            else:
                idx = int(re.search(r"graphs(\d+)_202", name)[1])
                seed = [2, 4, 5, 8, 13, 16, 19, 22, 24, 26][idx]
                p = 1
                archive = "data/cplex/maxcut/hex_swaps/hex_swaps_10_graphs.qpy"
                archive_index = idx
            problem = self.graph(f"data/cplex/maxcut/hex_swaps/{seed}")
            extras["seed"] = seed
        elif family == "hex_swaps_mis_shots":
            idx = int(re.search(r"_(\d+)_202", name)[1])
            p = idx % 6 + 1
            problem = self.graph("data/cplex/mis/hex_swaps/1")
            archive = "data/cplex/mis/hex_swaps/1/cplex_49q_p1to6_6circuits.qpy"
            ps = list(range(1, 7))
            archive_index = p - 1
            extras["acquisition_repetition"] = idx // 6
        elif family == "ring_swaps":
            idx = int(re.search(r"circuits_(\d+)_", name)[1])
            seed = 1 + idx // 5
            p = 1 + idx % 5
            directory = f"data/cplex/maxcut/ring_swaps/{seed}"
            problem = self.graph(directory)
            archive = directory + "/cplex_100q_p1to5_5circuits.qpy"
            ps = list(range(1, 6))
            archive_index = p - 1
            extras["seed"] = seed
        elif family in {"peaked_hard", "49Q_shots"}:
            cz = (
                int(re.search(r"^(\d+)CZ", name)[1])
                if family == "peaked_hard"
                else (
                    [1644, 1860, 2076, 2940, 3156, 3372, 3588, 3804, 4020][index]
                    if "202510" in name
                    else 5072
                )
            )
            key = f"peaked:{cz}:{name.split('_')[-1].split('.')[0] if family == 'peaked_hard' else path}:{index}"
            problem = self.client.create_resource(
                "problems",
                {
                    "kind": "peaked",
                    "n_variables": int(row["bit_width"]),
                    "data": {
                        "cz_count": cz,
                        "source": self.source(path)["id"],
                        "graph": None,
                    },
                },
                idempotency_key=self.key(path, key),
            )
            extras["cz_count"] = cz
            if family == "peaked_hard":
                self.solution(
                    problem,
                    name.split("_")[-1].split(".")[0][::-1],
                    "target",
                    None,
                    {
                        "evidence": "filename target; reversed by benchmark loader",
                        "original_state": name.split("_")[-1].split(".")[0],
                    },
                    self.key(path, "target"),
                )
            extras["candidate_circuit_artifacts"] = [
                self.artifact(f)["id"]
                for f in sorted(self.manifest)
                if f.endswith(".qasm")
                and f.startswith("store/")
                and f"{cz}" in Path(f).name
            ]
        if problem and family not in {
            "49Q_shots",
            "peaked_hard",
            "24Q_max_cut",
            "100Q_max_cut",
            "cplex_shots",
        }:
            directory = next(
                (
                    d
                    for d, prob in self.problem_directories.items()
                    if prob["id"] == problem["id"]
                ),
                None,
            )
            if directory:
                extras["candidate_circuit_ids"] = [
                    r["id"]
                    for f in sorted(self.manifest)
                    if Path(f).parent == Path(directory) and f.endswith(".qpy")
                    for r in self.archive(f, problem)
                ]
        multi_graph = archive in {
            "data/cplex/maxcut/hex/hex_circuits.qpy",
            "data/cplex/maxcut/hex_swaps/hex_swaps_10_graphs.qpy",
        }
        circuit = (
            (
                self.archive(archive, problem, ps, only_index=archive_index)[0]
                if multi_graph
                else self.archive(archive, problem, ps)[archive_index]
            )
            if archive
            else None
        )
        if circuit and circuit["n_clbits"] != int(row["bit_width"]):
            raise ValueError(f"candidate circuit width differs: {path}")
        return problem, circuit, p, extras

    def legacy_context(self, row):
        path = row["path"]
        cache = Path(path).stem
        ancestors = self.ancestors(cache)
        settings = []
        evidence = []
        for ancestor in ancestors:
            relative = f"store/{ancestor}.pkl"
            stages = {r[1] for r in self.lineage if r[0] == ancestor}
            if relative not in self.manifest:
                evidence.append({"cache_id": ancestor, "missing": True})
                continue
            if "Settings" in stages:
                value = MetadataUnpickler(io.BytesIO(self.read(relative))).load()
                state = getattr(value, "state", {})
                settings.append(state)
                evidence.append(
                    {
                        "source_id": self.source(
                            relative, data={"redacted_settings": safe_value(value)}
                        )["id"],
                        "stage": "Settings",
                    }
                )
            elif "Circuit generation" not in stages:
                evidence.append(
                    {
                        "source_id": self.source(relative, artifact=True)["id"],
                        "stages": sorted(stages),
                    }
                )
        backend_names = {s["nisq"].state.get("backend_name") for s in settings}
        origins = {s["nisq"].state.get("simulate") for s in settings}
        backend = next(iter(backend_names)) if len(backend_names) == 1 else None
        kind = "simulation_counts" if origins == {True} else "unknown_counts"
        circuit_parent = next(
            (
                r[2]
                for r in self.lineage
                if r[0] == cache and r[1] == "Circuit execution"
            ),
            None,
        )
        problem_parent = next(
            (
                r[2]
                for r in self.lineage
                if r[0] == circuit_parent
                and r[1] == "Circuit generation"
                and f"store/{r[2]}.pkl" in self.manifest
            ),
            None,
        )
        problem = None
        if problem_parent:
            rel = f"store/{problem_parent}.pkl"
            if rel not in self.problems:
                value = MetadataUnpickler(io.BytesIO(self.read(rel))).load()
                edges, solutions = value
                candidates = sorted(
                    {s["problem"].state["problem"].arguments[0] for s in settings}
                )
                kind_problem = (
                    candidates[0] if len(candidates) == 1 else "legacy_optimization"
                )
                problem = self.client.create_resource(
                    "problems",
                    {
                        "kind": kind_problem,
                        "n_variables": int(row["bit_width"]),
                        "data": {
                            "edges": [
                                [a, b, attrs.get("weight", 1)] for a, b, attrs in edges
                            ],
                            "source_id": self.source(rel, artifact=True)["id"],
                            "candidate_problem_types": candidates,
                            "legacy_lineage": evidence,
                            "isolates_retained": True,
                        },
                    },
                    idempotency_key=self.key(rel, "problem"),
                )
                for state, weight in solutions.items():
                    self.solution(
                        problem,
                        format(state, f"0{int(row['bit_width'])}b"),
                        "candidate",
                        None,
                        {
                            "reference_weight": weight,
                            "source_id": self.sources[rel]["id"],
                            "optimality": "not established by reference membership",
                        },
                        self.key(rel, f"solution:{state}"),
                    )
                self.problems[rel] = problem
            problem = self.problems[rel]
        circuit = None
        if circuit_parent:
            rel = f"store/{circuit_parent}.pkl"
            cache_key = (rel, problem["id"] if problem else None)
            if cache_key not in self.circuits:
                value = CircuitUnpickler(io.BytesIO(self.read(rel))).load()
                found = locate_circuits(value)
                original = self.artifact(rel)
                self.circuits[cache_key] = [
                    self.client.store_circuit(
                        qc,
                        problem_id=problem["id"] if problem else None,
                        data={
                            "legacy_source": self.source(rel, artifact=True)["id"],
                            "original_artifact_id": original["id"],
                            "pickle_locator": loc,
                        },
                        idempotency_key=self.key(rel, f"circuit:{i}:{cache_key[1]}"),
                    )
                    for i, (loc, qc) in enumerate(found)
                ]
            circuit = self.circuits[cache_key][int(row["index"])]
        return (
            problem,
            circuit,
            backend,
            kind,
            {
                "noise_factor": float(row["noise_factor"]),
                "legacy_lineage": evidence,
                "origin_evidence": "linked retained simulation settings only; missing lineage preserved",
            },
        )

    def related_qasm(self):
        from qiskit import qasm2

        for path in sorted(self.manifest):
            if not path.startswith("store/49Q_shots/qasm/") or not path.endswith(
                ".qasm"
            ):
                continue
            original = self.artifact(path)
            qc = qasm2.loads(
                self.read(path).decode(),
                custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
            )
            self.client.store_circuit(
                qc,
                data={
                    "original_artifact_id": original["id"],
                    "source_id": self.source(path, artifact=True)["id"],
                    "role": "related peaked definition; acquisition measurement layer is unavailable",
                    "circuit_structure": circuit_record(qc, 0),
                },
                idempotency_key=self.key(path, "related-qasm-circuit"),
            )

    def calibration(self):
        for relative, kind, date in [
            (
                "store/24Q_max_cut/ibm_boston_2026-03-05_roerr_asymmetry.pkl",
                "asymmetric_readout_pairs",
                "2026-03-05",
            ),
            (
                "store/ring_circuits/ibm_boston_2026-03-20_eplg.pkl",
                "edge_eplg",
                "2026-03-20",
            ),
        ]:
            values = MetadataUnpickler(io.BytesIO(self.read(relative))).load()
            self.client.create_resource(
                "calibrations",
                {
                    "backend": "ibm_boston",
                    "measured_at": None,
                    "data": {
                        "kind": kind,
                        "date_label": date,
                        "source_id": self.source(relative, artifact=True)["id"],
                        "values": safe_value(values),
                        "convention": "readout pair index0 prep0→measure1, index1 prep1→measure0 in notebook; preserve original pair",
                        "scope": "partial historical calibration; not verified per execution",
                    },
                },
                idempotency_key=self.key(relative, "calibration"),
            )

    def import_row(self, row):
        path = row["path"]
        width = int(row["bit_width"])
        legacy = row["kind"] == "legacy_execution_counts"
        if legacy:
            problem, circuit, backend, kind, extras = self.legacy_context(row)
            p = None
        else:
            problem, circuit, p, extras = self.association(row)
            backend = "ibm_boston" if Path(path).suffix == ".npz" else None
            kind = "hardware_counts" if backend else "unknown_counts"
            extras["layout"] = self.layout(path)
        source = self.source(path)
        locator = {k: row[k] for k in ("key", "index", "noise_factor") if row[k] != ""}
        source_key = self.key(
            path, json.dumps(locator, sort_keys=True, separators=(",", ":"))
        )
        execution = self.client.create_resource(
            "executions",
            {
                "backend": backend,
                "circuit_id": circuit["id"] if circuit else None,
                "job_id": None,
                "executed_at": None,
                "data": {
                    "source_id": source["id"],
                    "locator": locator,
                    "date_label": re.findall(r"202\d[-]?\d{2}[-]?\d{2}", path),
                    "circuit_association": "confirmed"
                    if legacy and circuit
                    else "inferred"
                    if circuit
                    else "unknown",
                },
            },
            idempotency_key=source_key + ":execution",
        )
        metadata = {
            "result_kind": kind,
            "n_qubits": width,
            "backend": backend,
            "circuit_hash": circuit["circuit_hash"] if circuit else None,
            "circuit_id": circuit["id"] if circuit else None,
            "circuit_association": "confirmed"
            if legacy and circuit
            else "inferred"
            if circuit
            else "unknown",
            "problem_id": problem["id"] if problem else None,
            "execution_id": execution["id"],
            "qaoa_p": p,
            "metadata": {
                "source_path": path,
                "import_policy": POLICY,
                "source_bit_order": "integer keys"
                if legacy
                else "column/character zero is displayed MSB; canonical integer matches benchmark notebook",
                "shot_order": "discarded",
                "source_locator": locator,
                **extras,
            },
            "sources": [{"source_id": source["id"], "locator": locator}],
        }
        upload = self.client.start_upload(metadata, idempotency_key=source_key)
        if upload["completed"]:
            result = self.client.get_collection(upload["id"])
            nblocks = result.metadata["chunks"]
        else:
            if legacy:
                values = MetadataUnpickler(io.BytesIO(self.read(path))).load()[
                    int(row["index"])
                ][1]
                blocks = encode_blocks(sorted(values.items()), width)
            elif Path(path).suffix == ".npz":
                with np.load(
                    io.BytesIO(self.read(path)), allow_pickle=False
                ) as archive:
                    array = archive[row["key"]]
                    if row["index"] != "":
                        array = array[int(row["index"])]
                    if row["key"] == "packed":
                        packed = array
                    else:
                        if not np.all((array == 0) | (array == 1)):
                            raise ValueError("non-binary sample values")
                        packed = np.packbits(array, axis=1, bitorder="big")
                    blocks = np_blocks(packed, width)
            else:
                spec = importlib.util.spec_from_file_location(
                    "source_bsb", self.root / "src/bitstring_utils.py"
                )
                module = importlib.util.module_from_spec(spec)
                self.read("src/bitstring_utils.py")
                spec.loader.exec_module(module)
                self.read(path)
                strings = module.load_bsb(self.root / path)
                values = Counter(int(s, 2) for s in strings)
                blocks = encode_blocks(sorted(values.items()), width)
            nblocks = 0
            for nblocks, block in enumerate(blocks, 1):
                self.client.put_chunk(upload["id"], nblocks - 1, block)
            result = self.client.finalize_upload(upload["id"])
        if (
            result.metadata["shots"] != int(row["shots"])
            or result.metadata["entries"] != int(row["unique_samples"])
            or result.metadata["chunks"] != nblocks
        ):
            raise ValueError(f"import totals differ from audited payload: {path}")
        self.collections[path, locator.get("index", "")] = result
        record = {
            "id": result.id,
            "path": path,
            "locator": locator,
            "shots": result.metadata["shots"],
            "entries": result.metadata["entries"],
            "kind": kind,
            "circuit_association": metadata["circuit_association"],
        }
        self.report["collections"].append(record)
        print(json.dumps(record), flush=True)

    def derived(self):
        for cache in sorted(
            {r[0] for r in self.lineage if "reconstitution" in r[1].lower()}
        ):
            path = f"store/{cache}.pkl"
            if path not in self.manifest:
                continue
            ancestors = self.ancestors(cache)
            parents = [
                c for (p, _), c in self.collections.items() if Path(p).stem in ancestors
            ]
            if not parents:
                self.report["skipped"].append(
                    {
                        "path": path,
                        "reason": "standalone distribution has no retained result ancestor; outside initial result import",
                    }
                )
                continue
            widths = {p.metadata["n_qubits"] for p in parents}
            if len(widths) != 1:
                raise ValueError("ambiguous derived distribution width")
            values = MetadataUnpickler(io.BytesIO(self.read(path))).load()
            if not isinstance(values, dict) or not all(
                type(k) is int and type(v) in (int, float) for k, v in values.items()
            ):
                raise ValueError("unsupported derived distribution")
            total = sum(values.values())
            kind = (
                "quasi_probabilities"
                if any(v < 0 for v in values.values()) and abs(total - 1) < 1e-9
                else "probabilities"
                if abs(total - 1) < 1e-9
                else "weighted_samples"
            )
            source = self.source(path, artifact=True)
            metadata = {
                "result_kind": kind,
                "n_qubits": next(iter(widths)),
                "metadata": {
                    "stage": "Distribution reconstitution",
                    "legacy_cache_id": cache,
                    "parent_indices": "all retained noise-factor results from ancestor executions; exact contribution unknown",
                },
                "parents": [p.id for p in parents],
                "sources": [
                    {
                        "source_id": source["id"],
                        "locator": {"stage": "Distribution reconstitution"},
                    }
                ],
            }
            result = self.client.store_collection(
                values, metadata=metadata, idempotency_key=self.key(path, "derived")
            )
            self.report["collections"].append(
                {
                    "path": path,
                    "id": result.id,
                    "kind": kind,
                    "entries": len(values),
                    "shots": None,
                }
            )

    def run(self, rows, include_derived=True):
        self.related_qasm()
        self.calibration()
        for row in rows:
            self.import_row(row)
        if include_derived:
            self.derived()
        self.report["observed_collections"] = sum(
            r["shots"] is not None for r in self.report["collections"]
        )
        self.report["gross_observations"] = sum(
            r["shots"] or 0 for r in self.report["collections"]
        )
        self.report["all_scoped_counts_imported"] = len(rows) == len(self.catalog)
        self.report["unknowns"] = [
            {
                "path": r["path"],
                "reason": "exact submitted circuit or layout/calibration/time unavailable; see collection metadata",
            }
            for r in self.report["collections"]
            if r.get("circuit_association") != "confirmed"
            and r.get("shots") is not None
        ]
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--server", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path, default=Path("import-report.json"))
    parser.add_argument("--family")
    args = parser.parse_args()
    with OSRClient(args.server, timeout=120.0) as client:
        importer = Importer(args.root, client)
        rows = importer.catalog
        if args.family:
            rows = [
                r for r in rows if r["path"].startswith("store/" + args.family + "/")
            ]
        if args.limit is not None:
            rows = rows[: args.limit]
        if not args.apply:
            print(
                json.dumps(
                    {
                        "mode": "read_only_plan",
                        "collections": len(rows),
                        "observations": sum(int(r["shots"]) for r in rows),
                        "families": dict(
                            Counter(
                                Path(r["path"]).parts[1]
                                if len(Path(r["path"]).parts) > 2
                                else "legacy"
                                for r in rows
                            )
                        ),
                        "exclusions": [
                            "figures/",
                            "backups/",
                            "store/hex_swaps_mis_shots_bad/",
                            "data/ except data/cplex/",
                        ],
                        "rows": rows,
                    },
                    indent=2,
                )
            )
            return
        report = importer.run(
            rows, include_derived=not args.family and args.limit is None
        )
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k
                    in {
                        "observed_collections",
                        "gross_observations",
                        "all_scoped_counts_imported",
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
