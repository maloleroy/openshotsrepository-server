# Importing historical OSR data

All imports use the HTTP API and create UUIDv4 records. They are idempotent with explicit source/locator keys. They retain source SHA256, path and collection boundaries; they do not merge executions. Input files remain unchanged.

## clustering-for-qem

Install the isolated tooling environment; its editable client dependency points to the sibling `../client` repository:

```bash
uv sync --locked --project scripts/importing --extra test
uv run --locked --project scripts/importing python scripts/import_source_data.py \
  /path/to/clustering-for-qem
# The default only prints a plan. Import through a running server:
uv run --locked --project scripts/importing python scripts/import_source_data.py \
  /path/to/clustering-for-qem --apply --server http://localhost:8000 \
  --report import-report.json
```

`--family ring_swaps` or `--limit 1` selects a small test import. Remove those selectors for the complete retained source catalog. Rerunning the same command resumes unfinished uploads and reuses published records. Source hashes must match the committed audit allowlist; changed inputs require a refreshed scoped audit and deliberate new provenance. The importer does not traverse excluded trees.

The input scope is result collections and related artifacts. It excludes all `figures/`, all `backups/`, `store/hex_swaps_mis_shots_bad/`, and everything in `data/` except `data/cplex/`. No qopt-best-practices data is imported. Standalone benchmark libraries and definitions with no related result are outside the initial import. The notebook's duplicate March sample file is not a second acquisition.

### Conversion and historical evidence

- Boolean NPZ slices, packed NPZ and BSB observations become counts. Original shot order is discarded.
- NPZ rows are aggregated using NumPy and encoded directly into width-aware little-endian blocks, avoiding millions of Python string keys.
- Each array key, stacked-array slice and noise-factor histogram remains a separate collection/execution.
- CPLEX graph matrices retain n and isolated vertices, signed upper-triangle edges, original JSON/solver versions, solution pools and forbidden constraints. Solver pool solutions are candidates; a pool objective is retained without claiming optimality. CPLEX and peaked target strings use the explicit source reversal convention.
- Ring solutions use an exact weighted-cycle dynamic program, with evidence attached. Representatives are marked optimal without claiming to enumerate every degenerate optimum.
- Related bound QPY archives retain original bytes, archive index, quantum/classical widths, measurements, layouts and gate statistics. Parameters remain embedded in the circuit. Related peaked QASM definitions are retained; their absent measurement layer does not establish an exact submitted circuit.
- Graphs/circuits with ambiguous acquisition links are labeled inferred or unknown. They cannot enter the automatic hardware cache. Main imported NPZ associations remain conservative archive candidates; imported legacy circuit-parent relationships have direct cache-lineage evidence.
- Filename dates stay date labels. Exact execution time, missing job IDs and full execution calibration stay null. Two partial historical calibration records are preserved independently, without attaching them as verified per-run snapshots.
- Ring-with-swaps layout uses the user-confirmed `layouts.json["store/ring_swaps"]`. October 30/31 and peaked-hard mappings retain analysis/acquisition confidence labels. The December peaked acquisition layout remains unknown.
- The 58 BSB files do not establish a backend independently of later analysis settings; their origin is `unknown_counts`. All eleven retained legacy execution histograms have linked simulation settings and are `simulation_counts`. Redacted settings preserve source provenance without uploading credential-bearing settings pickles.
- Two root legacy reconstituted distributions are imported as probability results, with retained count ancestors. The standalone eight-entry demonstration distribution has no retained result ancestor and is outside this initial import.

### Completed local verification

The local PostgreSQL import contains **270 observed-count collections, 75,203,874 gross observations**, and **two derived probability distributions**. It is a local database, not a public deployment. Gross totals include possible source overlap; they are not a unique-acquisition estimate.

[Source import report](source-import-report.json) records collection IDs and 303 related original artifact paths/hashes. [Read-back verification](source-import-verification.json) covers all **1,324 blocks, 907,585,049 binary payload bytes**. Every block's SHA256, width, padding, strict numeric state order and totals was independently checked. All 303 retained original artifacts (121,171,179 bytes) also passed checksum verification. A complete import replay preserved every ID and total. The verification files describe the completed local import; they do not contain sample payloads. Rerunning the importer against this database preserves its IDs.

```bash
uv run --locked --project scripts/importing python scripts/verify_import.py \
  import-report.json --output import-verification.json
```

## Original SQLite database

```bash
# Read-only plan:
uv run --locked --project scripts/importing python scripts/migrate_sqlite.py osr.db
# Append into PostgreSQL through OSR:
uv run --locked --project scripts/importing python scripts/migrate_sqlite.py osr.db \
  --apply --report sqlite-migration.json
```

The SQLite connection uses URI `mode=ro` and `PRAGMA query_only`. The input SHA is checked before and after. Original IDs, ingestion timestamps, tags and circuit hashes remain provenance; new IDs are UUIDv4. Invalid/unknown legacy hash schemes never become confirmed cache keys. Original rows lacked a result-kind field, so migration labels them `unknown_counts`. The old timestamp is not treated as execution time.

The existing eight local SQLite records were migrated and an idempotent second run returned the same IDs. The source hash remained `f76c673e5d39978b3c451d820e3c4af59852b457d2ffe50caf9694da6a1f6525`.
