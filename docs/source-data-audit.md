# Source data audit for OSR

Audit date: 2026-10-06. This completes item 1 (exploration). Database design, migration, imports, server changes and client changes have not started.

## Scope and decisions

Source root: `/media/malo/nixos/home/fluteur/Documents/IBM/clustering-for-qem`.

**Only `data/cplex/` is in scope inside `data/`. Every other `data/` directory is excluded, including `qopt-best-practices`.** The audit includes `store/`, `figures/`, `notebooks/`, backups, documentation, source code and tests. Initial import will cover result collections and their related artifacts, rather than every standalone problem or circuit definition.

Decisions supplied by the user:

- Store counts, without retaining shot order; retain source provenance.
- 256 measured bits is a firm limit. The proposed count representation is `[u64; 4]` with `u32` occurrences.
- Support hardware, simulation, mitigated/generated results, probabilities and quasi-probabilities, with explicit result types.
- Include problem solutions, their objective values and provenance. Distinguish candidate/feasible/reference solutions from solutions established as optimal.
- Public multi-user service, high performance, future Debian deployment; no login, API keys or rate limiting at this stage.
- Collections are immutable. Corrections produce new versions.
- Use UUIDv4 for unique IDs in the redesign.
- Preserve the Python client's README and high-level API.
- Main acquisition job IDs are unavailable. Job ID remains a valid optional field.
- For June ring-with-swaps shots, `notebooks/layouts.json["store/ring_swaps"]` is authoritative. The user's shorthand `"ring_swaps"` refers to this actual key.

Source commit: `b5dc41871609aa29f9311dc6c45e4d494b22fa71`, with existing local modifications. Client commit: `9d7d43a3f6a368d492145bb6ea6a3986349af193`. The audit did not modify either source data or client code. Existing source changes were preserved.

## Coverage and evidence

The scoped census contains **38,984 files, 3,654,914,049 bytes**. Each file has a relative path, size and SHA-256. Git internals, virtual environments, Rust build output, dependency trees and Python/tool caches are excluded. File counts include generated analysis artifacts and copies; they do not represent independent experiments.

| Area | Files | What it contains |
| --- | ---: | --- |
| `store/` | 521 | Main sample files, legacy stage caches, graph pickles, partial historical calibration, circuit archives |
| `data/cplex/` | 3,054 | Graph definitions, solver inputs/models/output, solution pools, circuit archives and seed/depth catalogs |
| `figures/` | 35,122 | Clustering outputs, generated samples, correlations, metrics, summaries, graph recovery versions and recent calibration/circuit artifacts |
| `notebooks/` | 13 | Ten notebooks, ordered layouts, standalone documentation and a duplicate sample file |
| `backups/` | 1 | Historical archive, inspected as a stream without extracting its paths |
| Other scoped files | 273 | Code, tests, documentation, settings and repository files |

Inspection covered:

- **11,107 NPZ files:** every array header, key, shape, dtype and object-dtype flag. All main sample payloads were decompressed and checked; derived array headers were inspected without pretending to validate every derived numerical value.
- **12,266 JSON files:** complete parse and recursive field/type paths, plus graph and calibration summaries where applicable.
- **641 QPY files, 4,016 circuits:** decoded with Qiskit 2.3.0. Circuit width, classical width, register names, active wires, measurements, depth, two-qubit depth, operations, gate pairs, parameters, metadata and layouts were inspected. No scoped QPY inspection failures remain.
- **8,411 pickle files:** opcode/global inspection and structural decoding using inert replacements for source classes. Six known Qiskit circuit caches were additionally decoded with packaged Qiskit classes, yielding twelve circuits. Source-project class code was not executed by unpickling. Credential fields in summaries are redacted.
- **58 BSB files:** headers and complete decoded sample strings, including multiplicities.
- **57 CSV files:** headers and dimensions; the three root cache CSVs are explicitly headerless.
- **21 QASM files:** source, register declarations and measurement counts.
- **603 CPLEX `.dat` inputs, four `.mod` models and 702 `.txt` outputs:** source/parser tracing and text/assignment-field inspection. Two additional current `.dat` graph artifacts and 200 `.stale` recovery files are also cataloged.
- **Ten notebooks:** all cell sources and textual outputs searched, with cell indices retained for navigation.
- **43 presentations and 46 PDFs:** slide text and PDF text extraction. The presentations contain 42 embedded workbooks, representing four unique files: small illustrative chart series and placeholder labels, with no acquisition records. Images and plot assets are inventoried and hashed; their pixels are not treated as machine-readable acquisition metadata.
- **7,039 regular backup members:** payload hashes compared with current files. Historical content differences were inspected separately from macOS metadata forks.

### Audit artifacts

| Artifact | Purpose |
| --- | --- |
| [source-data-summary.json](source-data-summary.json) | Scoped census totals and decoder versions |
| [source-data-inventory.json.gz](source-data-inventory.json.gz) | Full file manifest and format inspections |
| [source-shot-catalog.csv](source-shot-catalog.csv) | 276 main/legacy observed-count collections, with per-collection statistics |
| [source-data-payloads.json.gz](source-data-payloads.json.gz) | Main sample statistics and backup member/version evidence |
| [source-data-legacy-counts.json](source-data-legacy-counts.json) | Eleven legacy execution histograms and their noise factors |
| [source-data-pickle-circuits.json](source-data-pickle-circuits.json) | Twelve circuits contained in six legacy caches |
| [source-circuit-parameters.json.gz](source-circuit-parameters.json.gz) | Parameter value vectors grouped by operation and multiplicity for every scoped QPY circuit |
| [source-data-auxiliary.json.gz](source-data-auxiliary.json.gz) | Solver artifact fields and historical graph recovery files |
| [source-data-notebooks.json](source-data-notebooks.json) | Notebook cell indices and data-related terms |
| [source-data-documents.json](source-data-documents.json) | Document and embedded-file inventory |
| [source-data-embedded-workbooks.json](source-data-embedded-workbooks.json) | Embedded workbook cells, types, formulas and hashes |
| [source-data-verification.json](source-data-verification.json) | Independent total checks, payload validation and source hash verification |

The inventories summarize evidence and retain source hashes. They do not copy all original circuits, numerical arrays or sample strings into this repository.

### Availability of the requested fields

| Field | Evidence found | Limitation |
| --- | --- | --- |
| Samples and occurrences | Main NPZ/BSB payloads and legacy histograms | Copies/overlapping collections require provenance |
| Corresponding circuits | QPY/QASM and legacy circuit caches | Exact submitted version is sometimes ambiguous |
| Problem graph and type | Matrix JSON, NetworkX caches, loader/settings and solver models | Type/seed often comes from path or code |
| Problem solutions | Solver pools with objectives, forbidden/reference strings, ring solutions, peaked targets and legacy caches | Candidate status and optimality evidence differ |
| Layout and used qubits | Ordered catalog mappings, HERO_PATCH, circuit measurement/layout records | December peaked acquisition layout is missing |
| Readout/gate error rates | Two historical partial pickles and three recent full snapshots | Most acquisition-time full snapshots are missing |
| Circuit parameters and p | Bound gate values, loader/master ordering and cached settings | Historical optimizer/binding settings are not uniformly retained |
| Backend, execution options and time | Filenames, settings and some circuit/provenance artifacts | Exact submission/execution metadata and main job IDs are unavailable |

## Main observed samples

There are **207 collections in 161 `store/` NPZ files**, plus **58 BSB collections**: **265 collections with 76,466,445 observations**. Eleven legacy execution histograms add 251,024 observations, producing **276 collections and 76,717,469 gross observations**.

These are gross source totals. Copies, overlapping BSB subsets, repeated executions and reused samples must be resolved through provenance before reporting a unique-acquisition total. The standalone notebook NPZ is an exact copy of the March 5 ring sample file and is excluded from these collection totals.

| Family under `store/` | Collections | Bits | Observations | Main association |
| --- | ---: | ---: | ---: | --- |
| `49Q_shots` | 28 | 49 | 32,000,000 | Three October 2025 batches with nine peaked circuits each; one December batch |
| `24Q_max_cut` | 16 | 24 | 2,324,475 | March 5 seven-depth ring and March 20 nine-depth four-solution ring |
| `100Q_max_cut` | 9 | 100 | 2,931,445 | March 20 balanced ring, nine depths |
| `cplex_shots` | 3 | 49 | 8,000,000 | April 10 weighted MaxCut, separate P1/P2/P3 files |
| `hex_shots` | 19 | 49 | 1,260,106 | May 22: ten seeds at p=1 and seed 1 at p=2…10 |
| `hex_swaps_shots` | 14 | 49 | 1,441,187 | Ten graphs at p=1; August 6 single graph at p=1…4 |
| `hex_swaps_mis_shots` | 12 | 49 | 3,009,320 | June 17 MIS, two acquisitions at each p=1…6 |
| `hex_swaps_mis_shots_bad` | 6 | 49 | 1,513,595 | Earlier June 12 MIS variant, stored separately as bad data |
| `ring_swaps` | 100 | 100 | 12,514,740 | June 28: twenty graph seeds, five depths each |
| `peaked_hard` | 58 | 49 | 11,471,577 | Packed string files with CZ/target/sample labels |
| Root legacy execution caches | 11 | 4 or 52 | 251,024 | Integer count histograms, some with noise factors 1/3/5/7 |

All main unpacked samples contain binary values. Known observed-result widths fit the requested 256-bit limit. The largest observed occurrence count is 22,784 in a legacy histogram; the largest main NPZ occurrence count is 1,014. Many large-width collections have almost entirely unique samples, so histogram compression is not automatically substantial.

### Encodings and collection boundaries

1. **Boolean/integer matrices:** `(shots, bits)` under `data`, `data_0`…`data_8`, or `nors_tw_bare1`. Multiple numbered keys represent separate circuit/depth collections.
2. **Stacked matrix:** `arr_0` with `(9, 1_000_000, 49)` in each October peaked file. First-axis slices are separate collections.
3. **Packed NPZ:** `packed: uint8[shots, ceil(L/8)]`, `L: uint16`. Strings are left-aligned within big-endian bytes and padded on the right. Width cannot be inferred from the byte count alone.
4. **BSB:** `BSB4` header, flags, codec, bit width, original sample count and unique-string header count; compressed payload may store a unique-string table and sequence indices. Actual counts come from decoded payload/header, not the filename's `Nsamples` label. Observed file lengths range from five to 4,852,241 samples.
5. **Legacy histograms:** lists of `(noise_factor, dict[int, int])`. Integer keys alone do not establish width because leading zeros disappear.

No conversion or import has been performed in this phase.

## Linking shots to circuits, graphs, p and layouts

The central association sources are `src/benchmark/notebook.py`, `rust/cli.py`, `notebooks/layouts.json`, notebook cells and the compiler/topology modules. Associations are not uniformly embedded in sample files.

### Family associations

- **October peaked:** `20251029_boston_49q_1644_1860_2076_2940_3156_3372_3588_3804_4020.npz` names nine CZ counts. October 30 and 31 have the same nine-count pattern. The explicit layout catalog key covers October 29. `Peaked_circuits.ipynb`, cell 1, uses `layout(notebook.PEAKED_CIRCUIT)` while analyzing a `PEAKED_HARD` file. This establishes its analysis convention, but does not independently document every acquisition's submitted circuit/layout.
- **December peaked:** `data_peaked_circuit_heavy_hex_5072_ibm_boston_20251218.npz`, key `nors_tw_bare1`, contains five million 49-bit samples. Neither the benchmark loader nor `Peaked_circuits.ipynb` explicitly associates this file with a layout. Its acquisition layout remains unknown.
- **Peaked hard:** filenames encode CZ count, qubit count, sample label and target bitstring. Twenty-one QASM files are in `store/`. Similar peaked labels are not proof of an exact circuit-to-acquisition match. The loader deliberately reverses the October peaked layout for `PEAKED_HARD`; target strings also undergo a reversal in solution handling. The current notebook uses the unreversed October layout directly, so preserve the notebook convention separately from the generic loader convention.
- **March 5 ring:** `qaoa_p` maps seven slices to `[1,5,10,15,20,25,30]`. The graph is constructed in code as a signed weighted 24-cycle, including a four-edge heavy zone. Circuit archives live in `store/ring_circuits/`. The separate nine-circuit archive must not be identified with the seven measured slices solely from its name.
- **March 20 rings:** both nine-depth lists are `[1,2,3,4,5,7,10,15,30]`. Graph pickles are `graph_4solutions.pkl` and `graph_100q_balanced.pkl`; matching circuit batches are `ring_24q_p1to30_4solutions.qpy` and `ring_100q_p1to30_balanced.qpy`. Ordered layouts have exact sample-path keys.
- **April CPLEX:** loader maps the three files to p=1,2,3, despite filenames mentioning `p1to5_5circuits`. Problem/solutions are `data/cplex/maxcut/{graph.json,pool_solutions.json}`. `store/cplex_circuits/` has p1…10, p1…6, p1…5 without SABRE, and p1-only archives. Selecting the actual submitted version requires evidence beyond a compatible p.
- **Heavy hex:** `hex_circuits_run{i}` maps to seed `i+1`, p=1. `hex_seed1_p1to10_run{i}` maps to p=`i+1`. Graphs and per-seed circuits are in `data/cplex/maxcut/hex/{seed}/`; `hex_circuits.qpy` is the ten-circuit master. Ordered physical mapping is `HERO_PATCH` from `src/circuits/topology/hero.py`.
- **Hex with swaps, MaxCut:** the ten-graph master uses seeds `[2,4,5,8,13,16,19,22,24,26]`. For the August single-graph files, index `i` maps to p=`i+1`, with graph `data/cplex/maxcut/hex_swaps/2/graph.json`. The directory has both master and per-seed circuit archives; circuit-version identity must survive import. `HERO_PATCH` is the analysis layout.
- **Hex with swaps, MIS:** graph `data/cplex/mis/hex_swaps/1/graph.json`, per-instance six-circuit archive. Files with indices `i` and `i+6` are concatenated by the loader for each p=`i+1`, i=0…5. Retain the two source acquisitions even if presenting aggregate counts. The earlier bad-sign/variant family is separately stored; its status must remain visible.
- **Ring with swaps:** file index `i` maps to seed `1+i//5`, p=`1+i%5`. Twenty measured seeds link to `data/cplex/maxcut/ring_swaps/{seed}/graph.json`; five circuits per seed. The 100-circuit master agrees with the corresponding local per-seed circuits in the existing integrity audit. Graphs have 100 nodes, 200 edges and signed weights. The authoritative acquisition layout is the user-confirmed `layouts.json["store/ring_swaps"]`.

### Layout evidence and limitations

`notebooks/layouts.json` has six entries: October peaked, March 5 ring, both March 20 rings, ring-with-swaps, and the comma-joined April CPLEX sample paths. Arrays are ordered mappings, not sets of qubits. `src/circuits/layout.py` explains the logical-to-physical mapping semantics; the family-specific choices are made by `benchmark/notebook.py` and topology/compiler code.

The compiler's utility-scale ring mapping and the catalog's ring-with-swaps mapping overlap on only 67 of 100 physical qubits. The user's confirmation resolves which layout to attach to the June acquisition. Keep the compiler artifact's own mapping/provenance when it differs; do not overwrite it to imply that the archive independently proves the submitted physical mapping.

Circuit wire count, measured width, active physical wires and backend capacity are distinct. For example, many QPY files have 156 quantum wires and 49 measured bits. Measured classical widths across the scoped QPY corpus are 9,24,49,50,56,100. Measurement `(quantum wire, classical bit)` pairs and initial/final layouts are available in the inventory. Logical graph numbering must be linked through these mappings.

Four recent 56-bit circuits in `figures/hero_patch_selection/` explicitly contain `physical_qubits`, `logical_qubits` and `patch_calibration` metadata. Their full circuit width is 156; p=1…4 circuits have no observed shots identified here. These metadata-rich recent artifacts should not be confused with older acquisitions.

## Bit order is an explicit part of the source data

Several source helpers implement different conventions:

| Source/helper | Interpretation |
| --- | --- |
| `qtypes` integer bit operations | Integer bit i represents logical qubit i; displayed binary strings have most significant bit on the left |
| `benchmark.notebook.convert_to_bitstring` | Array column 0 becomes the most significant used integer bit |
| Rust sample loader | Matches the notebook's MSB-first array convention; word 0 contains the low 64 integer bits |
| `bitstring_npz.load_bitstrings_npz` | Recovers the original left-aligned string, truncating byte padding using L |
| `bitstring_npz.npz_to_counts` | Reverses the recovered string before integer conversion, so column/character 0 becomes integer bit 0 |
| CPLEX solution/forbidden readers | Reverse solver solution strings before converting to integers |
| Peaked-hard handling | Includes target/layout reversals described above |

Consequently, a single blanket `int(bitstring, 2)` applied to every format would misassociate some logical variables, layouts and error rates. Width, source ordering, measurement registers and graph-variable ordering are necessary conversion evidence. Binary strings with identical numeric values but different widths also have different meanings.

## Problem definitions and circuit parameters

### Graphs and solver data

There are **605 matrix graph JSON documents** across scoped artifacts: 603 CPLEX graphs and two recent graph artifacts. Their fields include `n`, `weights`, `edge_prob`, `n_forbidden`, `forbidden`, commonly `weight_min`/`weight_max`, and for some regular graphs `hero_mapping`/`sat_min_k`. Recent graphs additionally include physical patch/calibration/swap-layer metadata.

Weights can be signed; zero denotes an absent edge in the current reader. Graphs use explicit logical vertex indices. Preserve n even if some vertices are isolated. Upper-triangle and symmetric matrix conventions cannot be discarded without checking the source; `load_edges` reads the upper triangle.

Problem type often comes from directory/loader/settings context, rather than an explicit JSON field. MaxCut and MIS have different objectives, feasible-set rules and symmetry handling. The source's MaxCut solution loader additionally inserts complemented solutions.

Solution pools contain records such as `pool_id`, `objective`, `solution`. They are solver candidates/reference solutions, not observed shot counts. `forbidden` strings represent additional problem constraints/reference handling. `.dat` and `.json` versions are not automatically equivalent: the April `graph.dat` has an empty `forbidden` list while its JSON lists four strings. Solver models, logs, solution pools and graph versions all carry distinct provenance.

NetworkX graph pickles also contain node/edge/graph attributes. Legacy problem caches cover QAOA MaxCut, several MIS variants, Bernstein–Vazirani, adder, Deutsch–Jozsa and Grover. Their parameters include graph generation/seed, MIS penalty, fixed QAOA angles, oracle mask/offset/constant flag, Grover iterations and reference solutions. These are found in scoped `store/` caches and code, without importing an external benchmark library.

### QAOA and compilation evidence

All 4,016 scoped QPY circuits have bound parameters (`circuit.parameters` is empty). Their numeric gate parameters remain present. The parameter supplement records value vectors by operation and multiplicity; original instruction order and exact serialization remain in the source QPY file identified by its hash. Bound RX/RZ angles alone should not be labeled as gamma/beta without the generator's binding convention.

Compiler code obtains `optimized_qaoa_angles` from the trainer and binds them with `assign_parameters`. The current TQA schedule has a default timestep and a deterministic grid, but recomputing the current generator does not prove a historical acquisition used those defaults. p can be recovered from explicit loader/master ordering as documented above; CZ count or circuit depth is not interchangeable with p.

Other relevant fields present in source/settings/artifacts include circuit name/version, global phase, gate set, transpilation seed/optimization options, SWAP strategy/layers, topology, SAT mapping, graph seed, noise factors, folding and Pauli twirling settings. Their presence varies by family.

MIS penalty defaults differ by code path (6 in compiler/operator helpers and 8 in settings). Objective sign/penalty and generator version therefore need source-specific evidence. No universal penalty is established by this corpus.

## Calibration and error rates

### Partial historical calibration

- `store/24Q_max_cut/ibm_boston_2026-03-05_roerr_asymmetry.pkl`: 156 physical-qubit keys, each with two asymmetric readout rates. The notebook interprets index 0 as prep0→measure1 and index 1 as prep1→measure0. Other settings code uses named backend property extraction; preserve the pair's original convention and validate the named association during conversion. The pickle does not retain individual property timestamps/units or a complete backend snapshot.
- `store/ring_circuits/ibm_boston_2026-03-20_eplg.pkl`: 178 edge keys of the form `lower_higher`, with floating rates. The loader forms path estimates using `1-product(1-error)`. Such combined path estimates are derived values, not direct per-gate calibration records.

These filenames provide date labels, not exact execution timestamps. Their correspondence to every subsequent Boston acquisition is not established.

### Complete recent snapshots

| Snapshot | Backend | Qubits | Gate records | Backend properties update time |
| --- | --- | ---: | ---: | --- |
| `figures/calibration/ibm_boston_20261004T171225Z/snapshot.json` | ibm_boston | 156 | 2,576 | 2026-10-04T16:39:37Z |
| `figures/hero_patch_selection/calibration.json` | ibm_boston | 156 | 2,576 | 2026-10-04T21:35:07Z |
| `figures/hero_patch_selection/calibration/ibm_boston_20261004T215216Z/snapshot.json` | ibm_boston | 156 | 2,576 | 2026-10-04T21:35:07Z |

Snapshot fields include source, retrieval time, region, properties and configuration. Configuration includes backend identity/version, coupling map, basis gates and capacities.

Qubit properties include T1, T2, initialization error, both asymmetric readout probabilities, readout error and readout length. Individual property records contain `name`, `value`, `date`, `unit`. Gate records retain gate/name, ordered physical qubits and parameter records for gate error, gate length and threshold. Gates include CZ, RX/RZ/RZZ/SX/X, identity, measurement/reset variants and Xslow. Units and individual property dates must remain attached to values.

`device_rates.py` exports reduced CSV views and derived usability/patch costs. Direction/orientation matters; selecting the worse orientation and excluding sentinel unusable errors are analysis choices. Patch cost uses negative-log combinations of selected readout/CZ rates and is not measured circuit fidelity.

**Execution-time full calibration is unavailable for most older sample collections.** These October snapshots cannot be assigned as contemporaneous calibration for 2025 or earlier-2026 runs. `common_settings` selects a Marrakesh backend model and reuses March readout data for analysis. Those are analysis assumptions, not proof of the hardware configuration at acquisition. The optional `error_rates.json` path supported by settings has no corresponding file in the scoped census.

## Derived results and legacy caches

### Analysis artifacts under `figures/`

The corpus includes substantially more than raw shot data:

- Clustering centroids: 8,062 NPZ files with `centroids: uint64[N,4]`, `weights: uint64[N]`, `qubits: uint16`; another 2,625 add floating `trace_objectives`. Centroid weights are assigned cluster mass. A generated centroid's weight is not evidence that the hardware observed that bitstring that many times.
- 157 correlation cache NPZ files, with Pearson, Spearman, mutual-information and recovered absolute-correlation matrices, alongside width/shot metadata.
- Score/objective/optimum arrays, propagation/restart/random-control outputs, representations and two binary kernel benchmark assets. Shapes/dtypes/keys are in the manifest.
- Generated sample JSON, raw/random objective histograms and saved algorithm outputs. Multiplicity of algorithm-produced samples has a different meaning from acquisition counts.
- Manifests linking instance/p/source path/key/index/packed flags to runs, seeds, shot subsampling, objective edges and hardware edges.
- Summaries and CSV tables containing clustering settings, k, distances, majority/propagation strategy, radius, maximum iterations, annealing/temperature/cooling, penalties, timings, objective/solution-distance metrics and fitted settings. Some circuit feature tables use family/p fallbacks; those rows are not proof of an exact submitted circuit match.
- Recovery and integrity reports, including the ring-with-swaps mapping discrepancy and graph-version corrections.

Floating distribution values and algorithm weights need their own semantics. They must not be silently rounded into hardware occurrence counts. The requested future support for probabilities/quasi-probabilities remains a requirement even where no original shot total is recoverable.

### Legacy root cache

`store/index.csv` is a headerless lineage table `(cache ID, stage, previous cache ID)` with 1,824 rows. It references 1,669 identifiers including the `no_previous_step` sentinel; 241 named cache files are present and 1,427 referenced non-sentinel IDs have no local pickle. The index is therefore incomplete provenance.

`store/time.csv` links cache IDs to durations. `store/clustering_patches.csv` records clustering method/criterion associations. Stage names include settings, problem/circuit generation, execution, clustering, matching, centroid discovery, distribution reconstitution and metrics.

Twenty-seven distinct cached settings objects occur in the ninety settings-stage rows. Their fields cover problem configuration, p/circuit/angles, graph seed, backend/simulation, shots/qubits, error assumptions, folding/twirling/noise factors, clustering, matching and plotting. Cached simulation settings are evidence for their linked runs only; do not assign a global hardware/simulation label to every legacy cache from those settings alone.

Legacy execution histograms are in five files: `36c31f817ef1a4e9.pkl`, `45002481120aeab0.pkl`, `48837100bddef0b7.pkl`, `6724b1c7092465c0.pkl`, `6af659c4996faf4f.pkl`. Four-bit runs have 25,000 observations per noise factor; the 52-bit run has 1,024. Width is confirmed using related circuit evidence, rather than the maximum integer key alone.

Other caches contain weighted clusters, centroids/member sets, discovered solution sets, reconstituted floating distributions, Hamming/information/variation-distance metrics and MIS valid/mean/best metrics. These outputs should retain their stage and parents even when some parents are missing.

## Copies, historical versions and missing evidence

The archive `backup_20260727_160555.tar.gz` contains 7,039 regular files:

- 3,829 AppleDouble `._...` metadata forks;
- 3,008 actual files byte-identical to current files;
- 202 actual files with different content: one hundred hex-with-swaps graph JSON files, their one hundred `.dat` inputs, and the root index/time CSVs.

No actual backup-only data files were identified. The archive name predates some contained file labels, so its filename must not establish acquisition chronology. Historical graph versions need their archive/member hash, rather than silently replacing current graph identity.

`figures/data_recovery_backup_20260825/` contains another 200 `.stale` graph/solver-input files. They document previous graph versions. Importing current graphs against historical circuits without checking these versions could attach the wrong optimization problem to shots.

Remaining uncertainties:

1. **December peaked acquisition layout**, and explicit acquisition-level confirmation for the October 30/31 layout. Current notebook analysis uses the October 29 mapping; the benchmark loader has no December entry.
2. **Exact submitted circuit version** for some families, particularly the April CPLEX variants and peaked QASM associations. Compatible graph/p/depth does not establish submission identity.
3. **Exact execution timestamps, Runtime job IDs and submitted execution options** for main `store/` acquisitions. Filenames retain labels and dates; the user confirmed job IDs are unavailable.
4. **Contemporaneous full calibration** for older runs. Keep missing values unknown; retain partial snapshots and later analysis proxies with their own dates.
5. **Incomplete legacy lineage and overlapping/copy relationships.** Identical bytes establish an artifact copy; matching histograms alone do not prove the same execution.

These gaps do not prevent designing a schema with optional metadata and explicit provenance. They do prevent filling those metadata fields with unsupported historical claims.

## Existing OSR/client constraints found during exploration

Current server records contain an ID, JSON counts, backend, circuit hash, width, ingestion timestamp and tags. They do not model acquisition time, circuits/parameters, problems, measurement/layout mappings, calibration, source versions or result kind. The timestamp is generated on ingestion, so it cannot serve as execution time. JSON decode failures currently fall back to empty values.

The client exposes string/int count views, filtering, concatenation and a sampler cache. Circuit hashing uses QASM3 with a repr fallback; that fallback does not establish a reproducible circuit identity. The cache has incomplete batch/autostore behavior, and probability normalization uses the requested shot count rather than necessarily the stored total. Its existing result shape uses quasi-distributions. These findings are recorded for the later makeover; no API or implementation changes were made here.

## Reproducing the audit

Run from the OSR server checkout. The source project already supplies NumPy/Qiskit through its uv environment. `--no-sync` avoids changing source dependencies; `pdftotext` is needed for document extraction.

```bash
SOURCE_ROOT=/media/malo/nixos/home/fluteur/Documents/IBM/clustering-for-qem
uv run --no-sync --project "$SOURCE_ROOT" python scripts/audit_source_data.py "$SOURCE_ROOT" docs/source-data
uv run --no-sync --project "$SOURCE_ROOT" python scripts/audit_source_payloads.py "$SOURCE_ROOT" docs/source-data-payloads.json.gz
uv run --no-sync --project "$SOURCE_ROOT" python scripts/audit_source_circuits.py parameters "$SOURCE_ROOT" docs/source-circuit-parameters.json.gz
uv run --no-sync --project "$SOURCE_ROOT" python scripts/audit_source_circuits.py pickles "$SOURCE_ROOT" docs/source-data-pickle-circuits.json
uv run --no-sync --project "$SOURCE_ROOT" python scripts/audit_source_supplements.py "$SOURCE_ROOT" docs
```

The scanners explicitly prune `data/` to `data/cplex/`. They write only OSR audit outputs and generated temporary files. They do not import data into OSR. Source changes after this audit will naturally change the manifest and totals.
