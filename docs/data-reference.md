# USDA gNATSGO Icechunk Zarr store: data reference

The authoritative specification of what this repository builds and why. The
measured source inventories it is built on live in `docs/reference/`. When code
and this document disagree, fix one of them — `src/usda_gnatsgo/config.py` is
the machine-readable version of everything structural here.

## 1. Product

One Icechunk-backed Zarr v3 repository per gNATSGO release, holding selected
soil properties rasterized onto each region's native 30 m map-unit grid:

- one snake-case zarr group per source region (12 regions), native grid and CRS
  preserved exactly — no reprojection, resampling, or mosaicking;
- per region, two independently openable subgroups:
  `soil_properties` (depth-indexed, `(depth_interval, y, x)`) and
  `map_unit_properties` (`(y, x)`), plus `soil_properties/diagnostics`;
- values derived from the release's relational tables by versioned, documented
  aggregation algorithms (per-variable attrs carry the algorithm id/version);
- published to Source Coop (`s3://chill/usda-gnatsgo/` via
  `https://data.source.coop`), committed directly through icechunk.

### Versioning model

`config.DATASET_VERSION` names the store path `v{x.y.z}.icechunk`. Each new
USDA release bumps **minor** and builds a **fresh store at a new path** (the
dataset has no time dimension; releases replace, not append). Breaking
structural changes (re-chunk, re-grid, changed semantics) bump **major**.
Additive changes (new variables filled into the existing store, see §9) bump
**patch** or nothing. Old store paths remain readable forever; the release date
is recorded in root attrs, per-variable attrs, commit metadata, and an
immutable icechunk tag (`2026-02-13`), with per-region tags
(`conus-2026-02-13`) created as regions complete.

## 2. Source release

Current input: the gNATSGO 2026-02-13 release —
`data/gNATSGO_gpkg_02_13_2026.7z` extracting to a 56.75 GB GeoPackage
(`gNATSGO_02_13_2026.gpkg`, SSURGO schema 2.3.3, 77 tables) plus 12 regional
MURASTER/SARASTER GeoTIFF pairs (25.2 billion cells total).

- The release date is parsed from the archive/GeoPackage filenames
  (`MM_DD_YYYY` → ISO). The extraction-directory name is NOT authoritative
  (the 2026 package extracts into `gNATSGO_gpkg_01_30_2026/`): a mismatching
  directory warns; conflicting archive/GeoPackage dates are a hard error; a
  date that differs from `config.RELEASE_DATE` refuses to run.
- `MURASTER` is the output-grid authority; every raster must match its
  `RegionSpec` (measured values in `docs/reference/raster_inventory.csv`)
  exactly. Raster value 0 is background, not a mukey.
- Data relationships: `MURASTER` cell → `mukey` → `mapunit`/`muaggatt` →
  `component` (`comppct_r`, `majcompflag`, terrain, drainage) → `chorizon`
  (depths + measurements) → child tables (`chfrags`, `chtexturegrp`,
  `corestrictions`, `cointerp`).
- The GeoPackage ships **no valu1 table**; the Valu1 parameters are
  reconstructed (§6). The 2020 Planetary Computer valu1 and `muaggatt.aws*wta`
  are validation comparators only and are never joined into output.

### Regions (all grids measured)

| Group | EPSG | Pixel | Shape (h×w) |
|---|---|---|---|
| `alaska` | 3338 | 30 m | 65,835 × 122,295 |
| `american_samoa` | 4326 | 0.00027131° | 805 × 5,264 |
| `conus` | 5070 | 30 m | 97,053 × 153,996 |
| `fed_states_micronesia` | 4326 | 0.00027131° | 17,599 × 92,073 |
| `guam` | 4326 | 0.00027131° | 1,549 × 1,249 |
| `hawaii` | 4326 | 0.00027131° | 12,252 × 18,360 |
| `marshall_is` | 4326 | 0.00027131° | 9,728 × 2,715 |
| `mexico` | 5070 | 30 m | 451 × 370 |
| `northern_mariana_is` | 4326 | 0.00027131° | 17,333 × 2,694 |
| `palau` | 4326 | 0.00027131° | 18,879 × 13,272 |
| `puerto_rico` | 32161 | 30 m | 2,721 × 9,978 |
| `virgin_is` | 32161 | 30 m | 3,104 × 2,280 |

Coordinates are `float64` pixel centres (`x` ascending, `y` descending); each
subgroup carries a scalar CF `spatial_ref` (WKT + GeoTransform). No 2-D lon/lat
arrays are stored; consumers transform query coordinates to the region's CRS
and use `.sel(x=..., y=..., method="nearest")`.

## 3. Depth coordinate

A string `depth_interval` coordinate with 11 entries mixing disjoint layers and
cumulative zones (the Valu1 convention), with auxiliary coordinates
`depth_top_cm`, `depth_bottom_cm` (NaN = open bottom), `depth_kind`,
`to_reported_profile_depth`, `source_suffix`:

| Index | Label | Top | Bottom | Kind | Source suffix |
|---:|---|---:|---:|---|---|
| 0 | `0_5` | 0 | 5 | layer_and_zone | `0_5` |
| 1 | `5_20` | 5 | 20 | layer | `5_20` |
| 2 | `20_50` | 20 | 50 | layer | `20_50` |
| 3 | `50_100` | 50 | 100 | layer | `50_100` |
| 4 | `100_150` | 100 | 150 | layer | `100_150` |
| 5 | `150_profile` | 150 | open | layer | `150_999` |
| 6 | `0_20` | 0 | 20 | zone | `0_20` |
| 7 | `0_30` | 0 | 30 | zone | `0_30` |
| 8 | `0_100` | 0 | 100 | zone | `0_100` |
| 9 | `0_150` | 0 | 150 | zone | `0_150` |
| 10 | `0_profile` | 0 | open | zone | `0_999` |

`_999` means "to the reported depth of the soil profile", never a literal
999 cm. Zones are always computed directly from horizons over the zone
interval — never by combining published layer values (eligibility populations
differ).

## 4. Variable inventory (v0.1.0: 41 scientific/identifier + 2 diagnostics)

The single source of truth is `config.VARIABLES` (a frozen `VariableSpec`
registry with `status: included | deferred`). Summary:

**`{region}/soil_properties`** — float32, NaN fill, `(depth_interval, y, x)`:
`aws` (mm), `soc` (g C m⁻²), `aws_contributing_thickness` (cm),
`soc_contributing_thickness` (cm), `sand`, `silt`, `clay`,
`organic_matter` (%), `bulk_density_tenth_bar`, `bulk_density_one_third_bar`,
`bulk_density_fifteen_bar`, `bulk_density_oven_dry` (g cm⁻³), `ksat` (µm s⁻¹),
`awc` (cm cm⁻¹), `water_content_tenth_bar`, `water_content_one_third_bar`,
`water_content_fifteen_bar`, `satiated_water_content` (%), `cec_7`,
`effective_cec`, `sum_of_bases` (meq/100 g), `ph` (1:1 water).

**`{region}/soil_properties/diagnostics`** — the shared particle-size
eligibility pair: `particle_size_contributing_thickness` (cm),
`particle_size_contributing_component_percent` (%). Per-property coverage for
every other horizon property lives in `horizon_diagnostics.parquet` (published
as an audit artifact, keyed by mukey — traceable through the `mukey` array).

**`{region}/map_unit_properties`** — `(y, x)`:
`mukey` (uint32, fill 0), `droughty` (uint8, fill 255),
`nccpi3corn|soy|cot|sg|all` (float32, NaN), `pctearthmc` (uint8, 255),
`rootznemc` (uint16 cm, 65535), `rootznaws` (uint16 mm, 65535), `pwsl1pomu`
(uint16, 65535; **999 = water body**, not a percent), `musumcpct`,
`musumcpcta`, `musumcpcts` (uint16, 65535, never clipped to 100), `slope` (%),
`slope_length` (m), `elevation` (m), `aspect` (° cw from north) (float32,
NaN, dominant component), `texture_class` (uint16, 65535, integer-coded; the
code → name/description mapping is in the array's `class_names` /
`class_descriptions` / `flag_values` attrs, built from the release's RV
`chtexturegrp` domain).

Integer sentinels are Zarr fill values only — **no CF `missing_value` /
`_FillValue` attrs are set** (they would make xarray mask sentinels and upcast
integers to float). Semantics are in `missing_value_semantics` attrs.

## 5. Aggregation algorithms

Implemented as a versioned registry (`config.KNOWN_ALGORITHMS`, implementations
in `aggregate.py`); every variable attr carries `aggregation_algorithm_id` and
`aggregation_algorithm_version`. Missing values are never zero-filled;
numerators/denominators are explicit and renormalized over contributing
components, with the denominator published as coverage diagnostics.

- `linear_overlap_component_weighted_mean` — default for intensive horizon
  properties (16 of the 18, including `ph`): per component, overlap-thickness-
  weighted mean of finite horizon values; per map unit, `comppct_r`-weighted
  mean over components with a value. Chosen for `ph` (arithmetic mean of pH
  values, matching USDA SDV weighted-average practice).
- `composition_shared_mask_weighted_mean` — sand/silt/clay under ONE shared
  eligibility mask (all three finite) so closure ≈ 100% holds where coverage is
  complete; produces the published diagnostics pair.
- `dominant_component_least_transmissive` — **ksat**: minimum finite `ksat_r`
  among the dominant component's horizons overlapping the interval (the least
  transmissive layer controls). This is a deliberate product decision (recorded
  2026-08-18): NOT a weighted mean, geometric or otherwise.
- `dominant_component` — terrain (`slope`, `slope_length`, `elevation`,
  `aspect`) and surface texture. Deterministic selection: major components
  first, then descending `comppct_r`, ties by ascending `cokey`; the selected
  cokey is recorded in `terrain_audit.parquet`. Aspect is never an arithmetic
  mean of angles.
- `dominant_component_surface_texture` — the dominant component's surface
  horizon (lowest `hzdept_r`, ties by ascending `chkey`), RV `chtexturegrp` row
  (`rvindicator = 'Yes'`, ties by ascending `chtgkey`).
- Profile-ended intervals substitute each component's reported profile bottom
  (max `hzdepb_r`) for the open bottom.

**Coverage policy**: no minimum-coverage masking (decision 2026-08-18). Every
computable value is published together with its coverage diagnostics; consumers
filter to their own tolerance. A threshold can be added later without changing
semantics.

## 6. Valu1 reconstruction (`valu1.py`)

All 57 retained Valu1 parameters, one row per mukey, reimplemented from the
USDA column descriptions (`docs/reference/gssurgo-valu1-column-descriptions.pdf`).
Documented interpretation choices (the descriptions are prose, not SQL):

- **AWS** (`aws{suffix}`, `tk{suffix}a`, `musumcpcta`): per horizon
  `awc_r × overlap_cm × 10` (mm); per component summed over horizons with
  finite `awc_r`; per map unit the `comppct_r`-weighted mean over components
  with any eligible horizon in the interval, renormalized by the contributing
  percent. `musumcpcta` = summed `comppct_r` of components with any eligible
  horizon anywhere in the profile.
- **SOC** (`soc{suffix}`, `tk{suffix}s`, `musumcpcts`): per horizon
  `overlap_cm × dbthirdbar_r × (om_r / 1.724 / 100) × (1 − fragvol/100) ×
  10000` g C m⁻² (Van Bemmelen 1.724; `fragvol` = summed `chfrags.fragvol_r`,
  missing = 0). SOC is a stock, not a concentration. Eligibility requires
  finite `om_r` AND `dbthirdbar_r`.
- **Root zone** (`rootznemc`, `rootznaws`): major earthy components only
  (`majcompflag = 'Yes'`, compkind non-null and ≠ 'Miscellaneous area'). Depth
  = min(150 cm default/cap; shallowest `corestrictions` among Lithic/Paralithic/
  Densic bedrock, Densic material, Fragipan, Duripan, Sulfuric; top of first
  horizon with `ph1to1h2o_r < 3.5` or `ec_r > 12`). `rootznaws` = AWS over
  [0, depth]. Both `comppct_r`-weighted over major earthy components.
- **droughty**: 1 where map-unit `rootznaws ≤ 152 mm`, 0 otherwise, missing
  where no valid root-zone result (miscellaneous areas, water).
- **NCCPI** (`nccpi3corn/soy/cot/sg/all`, `pctearthmc`): `cointerp` filtered to
  `mrulename LIKE 'NCCPI%'` at extraction. In the 2026 release each crop
  submodel is its own main rule (`ruledepth = 0`) and crop submodels cover only
  ~17% of components, while the overall non-irrigated NCCPI rule covers every
  component and equals max(crops) in ~97% of dual-rated components (measured
  2026-08-18). Crops matched by rulename substring (Irrigated variant
  excluded); `nccpi3all` = the overall rule's rating per component, falling
  back to the crop maximum; weighted average over major earthy components,
  renormalized over rated components. `pctearthmc` = summed major-earthy
  `comppct_r`.
- **pwsl1pomu**: components tagged when `hydricrating = 'Yes'`; 'Unranked'
  resolved via drainage class (Poorly/Very poorly drained) or
  drained/undrained/channeled/protected/ponded/flooded phrases in
  `localphase`/`otherph`, falling back to those phrases in `muname`. Value =
  summed tagged `comppct_r`. **999** when `muname` contains "water(s)" as a
  word or components named 'Water' sum to ≥ 80%.
- **musumcpct**: summed `comppct_r` of all listed components, never clipped.

**Verification status (2026-08-18, against the 2026-02-13 release)**:

- `aws0_100` matches USDA's own `muaggatt.aws0100wta` (published in cm; ×10)
  with correlation 1.0000, median |diff| 0.013 mm, p95 0.046 mm over 318,658
  map units — the AWS eligibility/renormalization recipe reproduces USDA's
  calculation.
- `slope` equals `muaggatt.slopegraddcp` exactly for 99.98% of 320,450 map
  units — the dominant-component selector reproduces USDA behavior.
- NCCPI: the 2026 `cointerp` publishes crop submodels sparsely; see §6 NCCPI
  note for the measured overall-rule equivalence.

**Still open before a “USDA-equivalent valu1” claim**: SOC, root-zone,
droughty, and pwsl have no in-release comparator; compare CONUS distributions
per column against the 2020 Planetary Computer valu1 (schema reference in
`docs/reference/focused_valu1_schema.csv`) and record findings here.

## 7. Encoding

Zarr v3 sharding, Zstd level 3 (`config.EncodingSpec`):

| Array class | Inner chunks | Shards |
|---|---|---|
| `(depth_interval, y, x)` | `(1, 128, 128)` | `(1, 4096, 4096)` |
| `(y, x)` | `(128, 128)` | `(4096, 4096)` |

Chosen for the primary access pattern (~40×40 px windows, one depth at a time —
decision 2026-08-18): such a query touches ~1.7 inner chunks of 64 KiB raw
float32, behind a 16 KiB shard index. Depth is chunked at 1 so each depth slice
is independently readable; a full 11-depth profile query costs ~11 shard-index
reads + ~19 range requests per variable (accepted trade-off; a future major
version could chunk depth together if profile queries dominate). All-fill inner
chunks are omitted, so background costs nothing.

The shard is also the write unit, because rasterize windows are shard-aligned
(§8.5). At 4096² a shard object is 64 MiB raw float32, which bounds what a
failed upload retries and what an interrupted backfill can lose, and keeps a
rasterize worker's peak at ~128 MiB. The cost is object count: 912 shard objects
per CONUS 2-D variable, 1588 across all regions, ~449k for the whole store
before all-background skipping (measured 2026-08-21; the earlier 8192² layout
was ~119k). Changing chunking/sharding rewrites all arrays → new major version,
and `rasterize.verify_array_encoding` refuses to write shard-aligned windows
into arrays created under a different `EncodingSpec`.

## 8. Pipeline

```
inspect-release → extract → build-intermediates → init-store → rasterize → validate → release
```

1. **inspect-release** (`catalog.py`, `source.py`): release identity, artifact
   inventory + sha256 (`work/{release}/source_manifest.parquet`), grid
   verification against RegionSpecs, GeoPackage schema check
   (`schema_report.json`). Fails before any expensive work.
2. **extract** (`extract.py`): bounded SQLite→Parquet streaming of the 8
   required tables (`cointerp` pre-filtered to NCCPI). Atomic writes + JSON
   sidecars with a source fingerprint; re-runs reuse current extracts.
3. **build-intermediates** (`intermediates.py`): `valu1.parquet` (PK mukey),
   `soil_properties.parquet` (PK mukey, depth_interval),
   `map_unit_properties.parquet` (PK mukey), `horizon_diagnostics.parquet`,
   `terrain_audit.parquet`, `texture_classes.json`, plus
   `scientific_report.json` gates (uniqueness, domains, non-negativity,
   particle-size closure) that must pass before raster writes.
4. **init-store** (`template.py`): groups, coords, attrs, and empty sharded
   arrays (metadata only, never full allocations). **Idempotent and additive**:
   re-running creates only missing arrays, which is also how new variables
   reach an existing published store.
5. **rasterize** (`rasterize.py`, `lookup.py`): the work unit is one
   **(region, variable)** pair — one icechunk commit each.
   `--regions`/`--variables` select work; done pairs are skipped via the
   `rasterized_variables` provenance record in each region's group attrs
   (`--overwrite` to redo). Within a pair: shard-aligned 4096² windows through a
   thread pool (thread-local rasterio handles; single writer per storage
   object), all-background windows skipped, sorted-mukey `searchsorted`
   expansion (out-of-bounds guarded, integer sentinels without promotion, NaN
   floats), unmatched nonzero mukeys collected and reported, each window write
   and each checkpoint commit retried ×5 with exponential backoff (a store call
   is the only layer that retries gateway responses the AWS SDK cannot classify),
   except expired credentials, which stop the run at its last checkpoint with a
   re-login message rather than burning the remaining windows on doomed attempts.
   Memory floor per in-flight window ≈ 64 MiB
   (mukey) + one output slice; default 8 workers. A region completing all
   included variables gets a `{region}-{release}` tag.

   Windows are **checkpointed** every `--commit-every` (default 8): the batch is
   committed and the region's `rasterize_progress` attr records how many leading
   windows are durable, so an interrupted upload resumes from its last
   checkpoint instead of rewriting the variable. The pair's first checkpoint
   commits; later ones `amend` it, so a finished pair is still exactly one
   snapshot however many checkpoints or resumed runs it took. Amending is
   restricted to the pair's own untagged in-progress commit, and a checkpoint
   record outranks provenance (an interrupted `--overwrite` still owes windows).
   Amends orphan the snapshots and manifests they replace — `garbage-collect`
   reclaims them, and must never run while a backfill is writing. One writer per
   branch at a time.
6. **status**: the region × variable completion matrix, plus any checkpointed
   pair's window progress.
7. **validate** (`validate.py`): structure (shapes, dtypes, coord alignment
   and subgroup-coord identity, required attrs), sampled mukey windows
   byte-equal to the MURASTER, sampled scientific pixels equal to the
   intermediate row for their mukey, plus domain spot checks.
8. **release**: refuses while any (region, included-variable) pair is missing;
   creates the immutable `{release}` tag; reopens through the tag and re-reads
   every region. Tags are never deleted (icechunk burns deleted names); re-runs
   suffix `-r2`, `-r3`, ….
9. **publish-readme / upload-audit** (`remote.py`): the product landing page
   and `audit/{release}/` artifacts live outside the store prefix (plain S3,
   per-key deletes — the endpoint has no batch delete).

## 9. Extending the product

Adding a variable = one `VariableSpec` entry in `config.py` (+ a registry
operator and unit fixtures if the aggregation is new) + derivation wiring in
`horizons.py`/`mapunit.py`/`valu1.py` as appropriate. Then:

```
usda-gnatsgo init-store …      # creates just the new empty arrays (additive commit)
usda-gnatsgo rasterize … --variables new_var
```

on the **current published store** — existing data and consumers are
untouched. Version bumps are reserved for new USDA releases (minor) and
breaking changes (major). Candidates and their required decisions are cataloged
in [future-variables.md](future-variables.md).

## 10. Testing and verification

- `make test`: unit fixtures with hand-computed answers for every operator and
  Valu1 family, plus a synthetic end-to-end (100×80 px region on the real
  CONUS lattice, 16-px chunks / 32-px shards): intermediates → init →
  rasterize all variables → xarray read-back equality, additive init,
  resume/provenance, retry behavior (windows, checkpoint commits, and the
  expired-credential stop), corruption
  caught by validate, and checkpointing (a run interrupted mid-variable resumes
  to a byte-identical array under a single commit, and a foreign shard grid is
  refused).
- Real-data verification before publishing: `inspect-release` against the
  local release; sampled `validate` for CONUS plus at least one projected and
  one geographic island region; a semantically known location (an Iowa
  mollisol: high AWS/SOC, silt loam / silty clay loam surface texture);
  benchmarks of the 40×40 access pattern against local and Source Coop stores.
