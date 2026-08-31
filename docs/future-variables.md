# Variable catalog: included and future

The complete inventory of candidate variables for this product: what v0.1.0
includes, and every deferred candidate with the decisions it still needs.
Machine-readable status lives in `src/usda_gnatsgo/config.py`
(`VariableSpec.status`); this document is the human companion and the starting
point for any future update. Column-level source detail for all 884 GeoPackage
columns is preserved in `docs/reference/` (`rasterization_column_catalog.csv`,
`sdv_rasterization_catalog.csv`, `horizon_raster_candidates.csv`,
`rasterization_table_plan.csv`, `focused_valu1_*.csv`).

## How to add a variable

1. Add a `VariableSpec` to `config.VARIABLES` with `status="included"` (name,
   group, dims, dtype, fill, units, source fields, algorithm id/version). If
   the aggregation is new, add the id to `config.KNOWN_ALGORITHMS`, implement
   it in `aggregate.py` (or the relevant derivation module), and write
   hand-computed unit fixtures.
2. Wire the derivation: horizon properties → `horizons.py`; component/map-unit
   properties → `mapunit.py`; Valu1-defined fields → `valu1.py`; extend
   `source.REQUIRED_COLUMNS` / `extract.TABLES` for any new source columns.
3. `make intermediates` (rebuilds the derived Parquet), then
   `usda-gnatsgo init-store …` (creates just the new empty arrays — additive,
   safe on the published store) and
   `usda-gnatsgo rasterize … --variables <name>`.
4. Update this document and `data-reference.md` §4.

Every entry below lists the **decisions required** before inclusion. Anything
without an approved aggregation operator must stay `deferred` (config enforces
`algorithm_id="unreviewed"` → not templatable).

---

## Included in v0.1.0 (41 scientific/identifier arrays + 2 diagnostics)

| Variable | Group | Source | Algorithm |
|---|---|---|---|
| `mukey` | map_unit | MURASTER | direct_raster |
| `aws`, `aws_contributing_thickness`, `musumcpcta` | soil / map_unit | component+chorizon | valu1_aws |
| `soc`, `soc_contributing_thickness`, `musumcpcts` | soil / map_unit | +chfrags | valu1_soc |
| `sand`, `silt`, `clay` (+ diagnostics pair) | soil | chorizon | composition_shared_mask_weighted_mean |
| `organic_matter`, `awc`, 4× bulk density, 4× water content, `cec_7`, `effective_cec`, `sum_of_bases`, `ph` | soil | chorizon | linear_overlap_component_weighted_mean |
| `ksat` | soil | chorizon | dominant_component_least_transmissive |
| `nccpi3corn/soy/cot/sg/all`, `pctearthmc` | map_unit | cointerp+component | valu1_nccpi / valu1_pctearthmc |
| `rootznemc`, `rootznaws`, `droughty` | map_unit | +corestrictions | valu1_rootzone / valu1_droughty |
| `pwsl1pomu` | map_unit | mapunit+component | valu1_pwsl |
| `musumcpct` | map_unit | component | valu1_musumcpct |
| `slope`, `slope_length`, `elevation`, `aspect` | map_unit | component | dominant_component |
| `texture_class` | map_unit | chtexturegrp (surface) | dominant_component_surface_texture |

---

## Deferred: quantitative chorizon families (representative estimates)

The 2026 `chorizon` table has 51 quantitative measurement families; 18 are
included above. The remaining 33, all natural `(depth_interval, y, x)` float32
arrays via the existing extraction + overlap machinery. **Default decision per
row**: confirm the linear thickness/component-weighted mean is scientifically
valid (it is for most concentrations/percents), or record a reviewed operator.

| Source family | Units | Meaning | Decisions required |
|---|---|---|---|
| `caco3_r` | % | Calcium carbonate equivalent | linear mean OK; confirm |
| `gypsum_r` | % | Gypsum content | linear mean OK; confirm |
| `ec_r` | dS m⁻¹ | Electrical conductivity (saturated paste) | operator: linear vs dominant-condition (salinity is often class-based); root-zone calc already consumes it raw |
| `sar_r` | – | Sodium adsorption ratio | operator: ratio quantity — decide linear vs reviewed transform |
| `ph01mcacl2_r` | pH | pH, 0.01 M CaCl₂ | same decision as `ph` (linear mean of pH chosen 2026-08-18); name it `ph_cacl2` |
| `extracid_r` | meq/100 g | Extractable acidity | linear mean OK; confirm |
| `extral_r` | meq/100 g | Extractable aluminum | linear mean OK; confirm |
| `freeiron_r` | % | Free iron oxides | linear mean OK; confirm |
| `feoxalate_r`, `aloxalate_r`, `poxalate_r` | mg kg⁻¹ | Oxalate-extractable Fe/Al/P | linear mean OK; confirm; sparse coverage — check nonnull fraction first |
| `pbray1_r`, `ph2osoluble_r`, `ptotal_r` | mg kg⁻¹ | Phosphorus (Bray-1 / water-soluble / total) | linear mean OK; very sparse — decide whether coverage justifies publication |
| `lep_r` | % | Linear extensibility (shrink-swell) | linear mean OK; confirm |
| `ll_r` | % | Liquid limit | engineering property: linear vs dominant-condition |
| `pi_r` | % | Plasticity index | engineering property: linear vs dominant-condition |
| `aashind_r` | – | AASHTO group index | index value: linear vs dominant-condition |
| `sandvc_r`, `sandco_r`, `sandmed_r`, `sandfine_r`, `sandvf_r` | % | Sand sub-fractions | extend the shared particle-size mask? (compositional closure with `sand`) |
| `siltco_r`, `siltfine_r` | % | Silt sub-fractions | ditto |
| `claysizedcarb_r` | % | Clay-sized carbonate | linear mean OK; confirm |
| `fraggt10_r`, `frag3to10_r` | % | Rock fragments > 10 / 3–10 in | volume percents of whole soil: linear mean OK; relate to `chfrags` totals |
| `sieveno4_r`, `sieveno10_r`, `sieveno40_r`, `sieveno200_r` | % | Sieve passing percents | engineering: linear vs dominant-condition |
| `wtenthbar/wthirdbar/…` low/high | | see "estimates" below | |
| `partdensity` | g cm⁻³ | Particle density (single value, no l/r/h) | linear mean OK; confirm |
| `hzthk_r`, `hzdept_r`, `hzdepb_r` | cm | Horizon identity/depth | **excluded** — consumed by the machinery, not products |

Also in `chorizon`, categorical: `kwfact`, `kffact` (K factors — stored as
class strings like "0.32": decide numeric cast + operator, SDV uses
dominant condition), `excavdifcl`/`excavdifms` (excavation difficulty:
dominant condition + code dictionary).

## Deferred: low/high estimates

Every included and deferred `_r` family also has `_l`/`_h` columns.
**Decisions**: whether to publish ranges at all; if so, as an `estimate`
dimension (rejected for v0.1.0) or `_low`/`_high` array suffixes; and whether
aggregating l/h with the same weights as r is meaningful (it understates
uncertainty). Storage roughly triples per adopted family.

## Deferred: depth-indexed texture and other horizon categoricals

- `texture_class_by_depth(depth_interval, y, x)` — declared in config
  (`status="deferred"`). **Decision**: a dominant-condition rule for intervals
  spanning several horizons/components (SDV "dominant condition" weights by
  comppct × representative flag; thickness-dominant within the interval is the
  natural extension) — plus the same code dictionary as `texture_class`.
- `chtexture`/`chtexturemod` (texture sub-rows, modifiers): keep as Parquet
  sidecars; rasterizing arbitrary first rows discards information.
- `chaashto` (AASHTO class), `chunified` (Unified class): SDV themes exist for
  surface and selected depth; dominant condition + code dictionaries.
- `chstruct`, `chconsistence`, `chpores`, `chdesgnsuffix`: one-to-many
  qualitative — Parquet sidecar only unless a representative rule is defined.

## Deferred: component/site properties (2-D, component table)

All are `(y, x)` with either dominant-component (numeric) or dominant-condition
(categorical, integer-coded + dictionary) aggregation; the SDV catalog
(`sdv_rasterization_catalog.csv`) records USDA's own default algorithm and
tie-break per theme — mirror it unless reviewed otherwise.

Numeric: `tfact` (T factor), `wei` (wind erodibility index), `rsprod_l/r/h`
(range production), `airtempa_r` (mean annual air temp), `map_r` (mean annual
precip), `ffd_r` (frost-free days), `elev/slope/slopelenusle` low/high,
`initsub_r`/`totalsub_r` (subsidence), `albedodry_r`, `cropprodindex`,
`castorieindex`.

Categorical (dominant condition + dictionary): `drainagecl`, `hydgrp`
(hydrologic soil group), `hydricrating`, `weg` (wind erodibility group),
`frostact`, `corcon`/`corsteel` (corrosion), `erocl`, `runoff`,
`nirrcapcl/scl/unit` + irrigated variants (capability classes),
`constreeshrubgrp`, `foragesuitgrpid`, `soilslippot`, taxonomy
(`taxorder/suborder/grtgroup/subgrp/partsize/temp regime/…` — 7+ themes),
`earthcovkind1/2`, wildlife suitability columns (`wl*`).

**Decisions per adopted theme**: dominant component vs dominant condition
(match the SDV default), component-percent cutoff, tie-break rule, code
dictionary source (`mdstatdom*` domains), fill/dtype.

## Deferred: muaggatt map-unit summaries

`muaggatt` ships ~40 pre-aggregated map-unit columns (`slopegraddcp`,
`slopegradwta`, `brockdepmin`, `wtdepannmin`, `flodfreqdcd`, `pondfreqprs`,
`aws025wta/aws0100wta/…`, `drclassdcd`, `hydgrpdcd`, `iccdcd`, `niccdcd`,
`hydclprs`, `musumcpct*`…). Direct per-mukey rasters, no aggregation needed.
**Decisions**: which to publish (several duplicate our derived arrays — e.g.
`aws*wta` vs reconstructed `aws`; keep as validation comparators instead?), and
code dictionaries for the categorical ones. `slopegraddcp`/`slopegradwta`
remain validation comparators for `slope`, not products (see
[data-reference.md](data-reference.md) §5-6).

## Deferred: monthly variables (new `month` dimension)

`comonth` (flooding/ponding frequency classes), `cosoilmoist` (water table /
moisture status depths), `cosoiltemp` (soil temperature). Natural shape
`(month, y, x)` in a new `monthly_properties` subgroup. **Decisions**: month
dimension encoding (1–12 int coordinate), per-theme aggregation across
components within each month (SDV: dominant condition per month), class
dictionaries, and chunking for the new dimension (chunk all 12 months
together — same trade-off analysis as depth).

## Deferred: dimensioned crop/forest/ecology/restriction properties

- `cocropyld` (irrigated/non-irrigated crop yields): needs a `crop` dimension
  or per-crop arrays; unit varies per crop.
- `coforprod` / `coforprodo` (forest productivity, site index): `tree`
  dimension + site-index base decisions.
- `coecoclass` (ecological site ID/name): dominant condition + a large string
  dictionary (IDs are stable; names change) — 4 SDV themes.
- `corestrictions` as products (`restriction_depth(kind, y, x)`): kind
  dimension + shallowest-vs-dominant decision (the root-zone calc already
  consumes the shallowest of the root-limiting kinds).
- `copmgrp`/`copm` (parent material): dominant condition + dictionary.

## Deferred: SDV interpretation themes

The 532 `cointerp` interpretation themes (suitabilities/limitations, e.g.
"Dwellings With Basements"). Deliberately outside this product's scope: they
belong to a future `interpretations` product with their own
rating-class + fuzzy-value model, aggregation, and versioning. NCCPI (included)
is the only interpretation consumed here.

## Deferred: infrastructure-level extensions

- **SARASTER / survey-area metadata** as a 2-D array (survey area key per
  pixel): direct raster; decide dtype per region (source dtypes vary
  uint16/int32) and whether a per-region domain table suffices.
- **Multiscale overview pyramids** (zarr multiscales, parent/child layout,
  factor-named levels): retrofit is purely additive; categorical arrays need
  mode resampling (always from native), continuous can use mean. Follow the
  zarr-conventions/multiscales spec (parent/child layout, `layout`-ordered
  factor levels) and Earthmover's icechunk-multiscales reference
  implementation.
- **Merged/reprojected national grid**: explicitly out of scope for this
  product line (native grids are the contract).

## Explicitly excluded

- `OBJECTID` everywhere: unstable ESRI row identifier.
- Two-dimensional longitude/latitude coordinate arrays (use `spatial_ref`).
- Joining 2020 Planetary Computer valu1 values into any output (validation
  comparator only).
