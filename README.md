# USDA gNATSGO to Icechunk

End-to-end pipeline transforming each USDA NRCS [gridded National Soil Survey
Geographic Database (gNATSGO)](https://www.nrcs.usda.gov/resources/data-and-reports/gridded-national-soil-survey-geographic-database-gnatsgo)
release into an analysis-ready Icechunk Zarr v3 store on
[Source Cooperative](https://source.coop): one snake-case zarr group per source
region on its native 30 m grid, with depth-indexed soil properties
(`soil_properties`) and 2-D map-unit properties (`map_unit_properties`)
derived from the release's relational tables by versioned, documented
aggregation algorithms.

- **`docs/data-reference.md`** — the authoritative data/release specification
  (variables, depth model, algorithms, encoding, pipeline, versioning).
- **`docs/future-variables.md`** — the full catalog of candidate variables:
  what is included, what is deferred, and the decisions each deferred variable
  still needs.
- **`docs/reference/`** — measured source inventories (raster grids, table
  schemas, Valu1 column descriptions PDF) the specs are built on.
- **`product/README.md`** — the Source Coop landing page, with an anonymous
  consumer snippet.

## Setup

```bash
uv sync        # install the locked environment
make test      # run the full test suite (synthetic fixtures; no source data needed)
```

Place the extracted gNATSGO release under `data/` (gitignored). The audited
archive is `gNATSGO_gpkg_02_13_2026.7z`, ISO release date `2026-02-13`; the
release date parsed from the source filenames must match
`config.RELEASE_DATE`. Pipeline intermediates and reports land under `work/`
(gitignored).

## Pipeline

The pipeline is seven ordered commands. Phases 1–3 read the source package
and produce local Parquet intermediates; phases 4–7 write and publish the
icechunk store. Every command is idempotent: re-running skips work that is
already done and current.

Each `make` target wraps the `usda-gnatsgo` CLI (`uv run usda-gnatsgo --help`
for full options). All store-writing targets default to the local dev store
(`STORE=./gnatsgo_store_local`); set `ACCOUNT=chill` to target the published
Source Coop product instead — a bare `make rasterize` can never touch the
published store.

### 1. `make inspect-release`

Locates the release under `data/`, parses and reconciles the release date
from the archive/GeoPackage filenames (a conflicting pair is a hard error; a
misleading extraction-directory name only warns), verifies that every
regional MURASTER matches its configured grid exactly (CRS, shape, transform)
and that each SARASTER pairs with it, and checks that every relational column
the pipeline reads exists in the GeoPackage. Writes
`work/{release}/source_manifest.parquet` (every artifact with size and
SHA-256; `--no-checksums` to skip hashing the 57 GB GeoPackage) and
`schema_report.json`. Nothing downstream should run until this passes.

### 2. `make extract`

Streams the eight required tables (`mapunit`, `muaggatt`, `component`,
`chorizon`, `chfrags`, `corestrictions`, `cointerp` — pre-filtered to the
NCCPI rules — and `chtexturegrp`) out of the GeoPackage's SQLite in bounded
batches to typed Parquet under `work/{release}/extracted/`. Each file is
written atomically with a JSON sidecar recording the source fingerprint and
row counts; a re-run reuses any extract whose fingerprint still matches
(`--force` to redo). Key uniqueness is asserted where expected. Takes a few
minutes total; `cointerp` (a scan of the largest table) dominates.

### 3. `make intermediates`

The scientific step: turns the extracted tables into one value per map unit
(and per depth interval) for every variable. Reconstructs all 57 Valu1
parameters (AWS, SOC, NCCPI, root zone, droughty, PWSL, component sums),
computes the 18 horizon properties on the 11-interval depth coordinate, and
selects dominant-component terrain and surface texture. Outputs under
`work/{release}/derived/`: `valu1.parquet`, `soil_properties.parquet`
(keyed mukey × depth_interval), `map_unit_properties.parquet` (keyed mukey),
`horizon_diagnostics.parquet` and `terrain_audit.parquet` (audit artifacts),
and `texture_classes.json` (the integer-code → texture-name dictionary).
Finishes by running the scientific gates (key uniqueness, value domains,
non-negativity, sand+silt+clay closure) and refuses to leave failing
intermediates in place. ~1–2 minutes for the full national release.

### 4. `make init-store`

Creates the icechunk repository at the versioned path
(`v{DATASET_VERSION}.icechunk`) and writes the complete empty structure from
config: all 12 region groups, coordinates and CRS metadata, and one empty
sharded array per included variable (metadata only — empty chunks occupy no
storage). The encoding comes from `config.EncodingSpec`: `(1, 128, 128)` inner
chunks in `(1, 4096, 4096)` shards, so one storage object is 64 MiB of raw
float32. Idempotent **and additive**: on an existing store it creates only
whatever is missing, which is also how a newly included variable is added to a
published store without rebuilding anything. One commit. It refuses outright if
an existing array was created under a different `EncodingSpec` — rasterize
windows are shard-aligned, so mixing shard grids in one store would let two
windows write the same object.

### 5. `make rasterize [REGIONS=…] [VARIABLES=…] [WORKERS=n] [COMMIT_EVERY=n]`

Fills the arrays. The unit of work is one **(region, variable)** pair with its
own icechunk commit. Empty `REGIONS`/`VARIABLES` means everything; already-done
pairs (recorded in each region's `rasterized_variables` provenance attrs) are
skipped unless `OVERWRITE=1`. Within a pair, the region's MURASTER is read in
shard-aligned 4096² windows through a thread pool, each window's map-unit keys
are mapped to values via a sorted lookup against the derived Parquet
(all-background windows skipped, unmatched keys reported), and each region gets a
`{region}-{release}` tag when its last variable completes. Both window writes and
checkpoint commits are retried with exponential backoff: S3-compatible gateways
return responses the AWS SDK cannot classify as retryable, so neither it nor
icechunk retries them. Expired credentials are the exception: they would fail
every remaining window too, so the run stops at its last checkpoint and tells you
to `source-coop login` and re-run. Local: seconds–minutes per small region; CONUS
over the network is the long pole.

Long uploads are **checkpointed** so they are never lost: every `COMMIT_EVERY`
windows (default 8, one 64 MiB shard object each) the batch is committed and the
region's `rasterize_progress` attrs record how far the pair got. A killed run
resumes from its last checkpoint rather than re-uploading the variable, and
produces a byte-identical array. The pair's first checkpoint commits and later
ones amend it, so finishing a pair still leaves exactly one snapshot in the
history — at the cost of orphaned snapshots and manifests that
`make garbage-collect` reclaims. Run one writer per store at a time.

### 6. `make status` / `make validate [REGIONS=…]`

`status` prints the region × variable completion matrix — what a resumed
backfill still owes, including the window progress of any checkpointed pair.
`validate` verifies the store: structural checks
(shapes, dtypes, coordinate alignment, subgroup-coordinate identity, required
attrs), sampled windows of the `mukey` array compared byte-for-byte against
the source MURASTER, sampled scientific pixels compared against the derived
table row for their map unit, and value-domain spot checks. Run it per region
after rasterizing; it needs `data/` and `work/` locally for the source
comparisons (structure-only otherwise). Results stream as each check finishes.

The scientific samples are points scattered across the grid, so every one is a
fresh round trip to the store — they are issued concurrently, and `WORKERS`
matters far more than CPU count against S3. Sampling knobs:

```bash
make validate ACCOUNT=chill REGIONS=conus \
    SAMPLES=8 WINDOW=512 \   # random mukey windows compared to the MURASTER
    VALUE_SAMPLES=200 \      # random pixels checked against the derived tables
    WORKERS=32 SEED=7         # concurrent point reads; SEED reproduces a run
```

`VALUE_SAMPLES` counts pixels *drawn*; background ones are skipped, so CONUS
checks roughly 60% of them against all 42 derived variables (~4,800 point
reads, about two minutes). Turn it down for a quick smoke check, up before a
release. `SEED` makes a failing run reproducible.

### 7. `make release`

The publication gate: refuses while any (region, included-variable) pair is
missing, then creates the immutable release tag (`2026-02-13`), reopens the
store through the tag, and re-reads every region as a final check. Tags are
never deleted or reused — a corrected re-release gets a `-r2` suffix.

### Publishing extras

```bash
make publish-readme ACCOUNT=chill   # upload product/README.md as the landing page
make upload-audit ACCOUNT=chill     # upload manifests/reports/diagnostics to audit/{release}/
make info                           # show store structure, tags, recent snapshots
make garbage-collect                # reclaim objects orphaned by checkpoint amends
make clean-local-store              # rm -rf the local dev store
make clean-remote-store             # DESTRUCTIVE wipe of the published store; confirms twice
```

`make help` lists every target and variable (`REGIONS=`, `VARIABLES=`,
`WORKERS=`, `COMMIT_EVERY=`, `OVERWRITE=1`, `STORE=`, `ACCOUNT=`,
`CREDS_FILE=`, `GC_HOURS=`). Source Coop
credentials come from the `source-coop` CLI's cached login (`source-coop
login`) or a JSON export passed via `CREDS_FILE=`.

## Updating for a new USDA release

Update `config.RELEASE_DATE`, bump the minor `DATASET_VERSION` (new store
path), place the new source under `data/`, and run the pipeline top to
bottom. Adding a variable to the current release is lighter: one
`VariableSpec` entry in `config.py` plus its derivation, then `make
init-store` (creates just the new arrays) and `make rasterize
VARIABLES=<name>` — see `docs/future-variables.md`.

Changing `config.EncodingSpec` (the chunk or shard shape) is a different kind of
change: it rewrites every array, so it needs a fresh store path and a full
re-rasterize, not an additive `init-store`. Bump the major `DATASET_VERSION` to
publish alongside the old layout, or wipe the store path to replace it — a store
that mixes shard grids is refused rather than silently written.
