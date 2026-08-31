# USDA NRCS gNATSGO Soil Properties

Soil properties for the United States and its territories, produced by the USDA Natural Resources Conservation Service (NRCS). This product takes the USDA NRCS [gridded National Soil Survey Geographic Database (gNATSGO)](https://www.nrcs.usda.gov/resources/data-and-reports/gridded-national-soil-survey-geographic-database-gnatsgo) — a relational GeoPackage plus regional map-unit rasters — and reformats it into a cloud-optimized, version-controlled [Icechunk](https://icechunk.io) Zarr store, where soil properties
can be sliced by location and depth, no database joins required.

## Contents

One Icechunk repository at `v0.1.0.icechunk/` with one group per source region,
each on its native grid and CRS (no reprojection or mosaicking):

| group | source region | CRS | pixel size | grid (y × x) |
|---|---|---|---|---|
| `alaska` | Alaska | EPSG:3338 | 30 m | 65,835 × 122,295 |
| `american_samoa` | American Samoa | EPSG:4326 | 0.00027131° (~30 m) | 805 × 5,264 |
| `conus` | CONUS | EPSG:5070 | 30 m | 97,053 × 153,996 |
| `fed_states_micronesia` | Fed. States of Micronesia | EPSG:4326 | 0.00027131° (~30 m) | 17,599 × 92,073 |
| `guam` | Guam | EPSG:4326 | 0.00027131° (~30 m) | 1,549 × 1,249 |
| `hawaii` | Hawaii | EPSG:4326 | 0.00027131° (~30 m) | 12,252 × 18,360 |
| `marshall_is` | Marshall Islands | EPSG:4326 | 0.00027131° (~30 m) | 9,728 × 2,715 |
| `mexico` | Mexico (border tile) | EPSG:5070 | 30 m | 451 × 370 |
| `northern_mariana_is` | Northern Mariana Islands | EPSG:4326 | 0.00027131° (~30 m) | 17,333 × 2,694 |
| `palau` | Palau | EPSG:4326 | 0.00027131° (~30 m) | 18,879 × 13,272 |
| `puerto_rico` | Puerto Rico | EPSG:32161 | 30 m | 2,721 × 9,978 |
| `virgin_is` | U.S. Virgin Islands | EPSG:32161 | 30 m | 3,104 × 2,280 |

Each region group holds two independently openable subgroups (plus a
diagnostics subgroup), all at the region's native resolution:

| subgroup | dimensions | resolution | contents |
|---|---|---|---|
| `{region}/soil_properties` | `(depth_interval, y, x)` | 30 m | 22 depth-indexed arrays: water storage and retention, organic carbon, particle-size fractions, bulk density, chemistry |
| `{region}/soil_properties/diagnostics` | `(depth_interval, y, x)` | 30 m | sand/silt/clay coverage pair |
| `{region}/map_unit_properties` | `(y, x)` | 30 m | 19 arrays: `mukey`, crop productivity, root zone, wetland percent, terrain, surface texture |

- **CRS**: coordinates are pixel centres (metres or degrees per the table
  above); a CF `spatial_ref` variable carries the WKT and GeoTransform, so
  `rioxarray` and GIS tools georeference the arrays directly.
- **Storage layout**: zarr v3 sharded arrays, Zstd-compressed, with
  `(1, 128, 128)` inner chunks (64 KiB raw float32) grouped into
  `(1, 4096, 4096)` shard objects (64 MiB raw). Inner chunks are addressed by
  range request, so a field-scale or point query fetches ~tens of KB per
  variable and depth: a 40×40-pixel window reads that shard's 16 KiB index plus
  1–4 inner chunks, ~3–10 KiB compressed each (measured on `puerto_rico`).
- **Metadata**: every array's attributes record its source table and fields,
  units, missing-value semantics, and the exact aggregation algorithm and
  version that produced it.

### Depth intervals

The `depth_interval` coordinate of `soil_properties` mixes six standard layers
and five cumulative zones; auxiliary coordinates (`depth_top_cm`,
`depth_bottom_cm`, `depth_kind`, `to_reported_profile_depth`) carry the same
information numerically.

| `depth_interval` | top (cm) | bottom (cm) | kind |
|---|---:|---:|---|
| `0_5` | 0 | 5 | layer and zone |
| `5_20` | 5 | 20 | layer |
| `20_50` | 20 | 50 | layer |
| `50_100` | 50 | 100 | layer |
| `100_150` | 100 | 150 | layer |
| `150_profile` | 150 | reported profile depth | layer |
| `0_20` | 0 | 20 | zone |
| `0_30` | 0 | 30 | zone |
| `0_100` | 0 | 100 | zone |
| `0_150` | 0 | 150 | zone |
| `0_profile` | 0 | reported profile depth | zone |

`profile` means the interval extends to the reported depth of the soil
profile, which varies by soil component — not to a fixed depth.

## Versioning

The store path carries the version; each USDA release is published as a new
store path alongside the old ones, so a pinned path or tag always returns the
same data. This table grows with each release:

| store path | gNATSGO release | icechunk tag |
|---|---|---|
| `usda-gnatsgo/v0.1.0.icechunk` | 2026-02-13 | `2026-02-13` |

Minor versions are new USDA data releases; a major version bump marks a
breaking structural change (re-chunking, re-gridding, changed variable
semantics). New variables may be added to the current store additively
without a version change.

## Included parameters

### `soil_properties` — by depth interval

All float32, `(depth_interval, y, x)`, NaN where no data are available.

| Array | Units | Description |
|---|---|---|
| `aws` | mm | Available water storage in the interval |
| `soc` | g C m⁻² | Soil organic carbon stock in the interval |
| `aws_contributing_thickness` | cm | Soil thickness that actually contributed to `aws` |
| `soc_contributing_thickness` | cm | Soil thickness that actually contributed to `soc` |
| `sand` | % | Total sand (0.05–2.0 mm), weight percent of fine earth |
| `silt` | % | Total silt (0.002–0.05 mm) |
| `clay` | % | Total clay (< 0.002 mm) |
| `organic_matter` | % | Organic matter, weight percent |
| `bulk_density_tenth_bar` | g cm⁻³ | Bulk density at 1/10 bar water tension |
| `bulk_density_one_third_bar` | g cm⁻³ | Bulk density at 1/3 bar |
| `bulk_density_fifteen_bar` | g cm⁻³ | Bulk density at 15 bar |
| `bulk_density_oven_dry` | g cm⁻³ | Oven-dry bulk density |
| `ksat` | µm s⁻¹ | Saturated hydraulic conductivity — the **least transmissive** horizon of the dominant component (not a mean) |
| `awc` | cm cm⁻¹ | Available water capacity |
| `water_content_tenth_bar` | vol % | Water content at 1/10 bar |
| `water_content_one_third_bar` | vol % | Water content at 1/3 bar |
| `water_content_fifteen_bar` | vol % | Water content at 15 bar (wilting point) |
| `satiated_water_content` | vol % | Satiated water content |
| `cec_7` | meq / 100 g | Cation-exchange capacity at pH 7 |
| `effective_cec` | meq / 100 g | Effective cation-exchange capacity |
| `sum_of_bases` | meq / 100 g | Sum of extractable bases |
| `ph` | pH | Soil reaction, 1:1 water |

The `diagnostics` subgroup carries the shared sand/silt/clay coverage pair
(`particle_size_contributing_thickness` in cm,
`particle_size_contributing_component_percent` in %) for filtering partially
covered map units.

### `map_unit_properties` — one value per pixel

| Array | Type / missing | Units | Description |
|---|---|---|---|
| `mukey` | uint32 / 0 | – | Map unit key (0 = outside the surveyed area); joins to SSURGO/gNATSGO tables |
| `nccpi3corn` | float32 / NaN | 0–1 | National Commodity Crop Productivity Index v3, corn |
| `nccpi3soy` | float32 / NaN | 0–1 | NCCPI, soybeans |
| `nccpi3cot` | float32 / NaN | 0–1 | NCCPI, cotton |
| `nccpi3sg` | float32 / NaN | 0–1 | NCCPI, small grains |
| `nccpi3all` | float32 / NaN | 0–1 | NCCPI, best-performing crop |
| `rootznemc` | uint16 / 65535 | cm | Root zone depth for commodity crops (capped at 150) |
| `rootznaws` | uint16 / 65535 | mm | Available water storage within the root zone |
| `droughty` | uint8 / 255 | 0 or 1 | 1 where root-zone AWS ≤ 152 mm (drought-vulnerable) |
| `pwsl1pomu` | uint16 / 65535 | % | Potential wetland soil landscapes, percent of map unit; **999 = water body** |
| `pctearthmc` | uint8 / 255 | % | Percent of the map unit that is major earthy components |
| `musumcpct` | uint16 / 65535 | % | Sum of all component percentages (not clipped to 100) |
| `musumcpcta` | uint16 / 65535 | % | Component percent admitted to the AWS calculation |
| `musumcpcts` | uint16 / 65535 | % | Component percent admitted to the SOC calculation |
| `slope` | float32 / NaN | % | Representative slope gradient of the dominant component |
| `slope_length` | float32 / NaN | m | Representative USLE slope length |
| `elevation` | float32 / NaN | m | Representative elevation |
| `aspect` | float32 / NaN | ° | Representative aspect, clockwise from north |
| `texture_class` | uint16 / 65535 | code | Surface texture group, integer-coded — decode with the array's `class_names` attr |

Integer arrays keep their sentinel values when opened with xarray (no NaN
masking / float upcasting); each sentinel's meaning is in the array's
`missing_value_semantics` attribute.

## Reading the data

Anonymous read access, no credentials needed:

```python
import icechunk
import xarray as xr

storage = icechunk.s3_storage(
    bucket="chill",
    prefix="usda-gnatsgo/v0.1.0.icechunk",
    endpoint_url="https://data.source.coop",
    region="us-east-1",
    anonymous=True,
    force_path_style=True,
)
repo = icechunk.Repository.open(storage)
session = repo.readonly_session("main")  # or tag="2026-02-13" to pin the release

soil = xr.open_zarr(session.store, group="conus/soil_properties")
mapunit = xr.open_zarr(session.store, group="conus/map_unit_properties")

# top-metre available water storage
aws = soil["aws"].sel(depth_interval="0_100")

# a point profile: transform lon/lat to the region's CRS (read from the
# dataset's spatial_ref variable), then nearest-select
from pyproj import CRS, Transformer

crs = CRS.from_wkt(soil["spatial_ref"].attrs["crs_wkt"])
x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(-93.62, 41.99)
profile = soil[["aws", "soc", "organic_matter", "ph"]].sel(x=x, y=y, method="nearest")

# decode the surface texture class
tex = int(mapunit["texture_class"].sel(x=x, y=y, method="nearest"))
name = mapunit["texture_class"].attrs["class_names"][str(tex)]
```

## Processing

gNATSGO does not ship these rasters. The source package contains one raster
per region whose pixel values are map unit keys (`mukey`), plus relational
tables describing each map unit: its **components** (soil series with a
percent-of-map-unit weight) and their **horizons** (layers with top/bottom
depths and measured properties). This product flattens that hierarchy into
rasters in four steps:

1. **Extract** — the required tables (`component`, `chorizon`, `chfrags`,
   `corestrictions`, `cointerp`, `chtexturegrp`, `mapunit`, `muaggatt`) are
   streamed out of the GeoPackage; every source artifact is checksummed.
2. **Aggregate to one value per map unit** — per property, depth interval,
   and map unit:
   - *Horizon properties* (sand … ph): each horizon is clipped to the depth
     interval; horizons combine within a component as a thickness-weighted
     mean, and components combine as a component-percent-weighted mean.
     Missing values are never treated as zero — the value renormalizes over
     the data that exist, with the contributing thickness / component percent
     published as diagnostics. Sand, silt, and clay share one eligibility mask
     so they still sum to ~100% where coverage is complete. Exceptions:
     `ksat` takes the least transmissive (minimum) horizon of the dominant
     component; terrain and `texture_class` take the dominant component's
     representative value directly.
   - *Valu1 parameters* (`aws`, `soc`, NCCPI, root zone, `droughty`,
     `pwsl1pomu`, component sums): the 2026 release no longer includes USDA's
     precomputed "valu1" table, so these are reconstructed from the relational
     tables following the USDA column definitions (AWS as awc × thickness; SOC
     as a carbon stock corrected for rock fragments; root-zone depth from
     restriction layers with a 150 cm cap; the documented hydric/drainage
     criteria for wetland percent). Where the release provides comparators,
     the reconstruction matches: AWS agrees with `muaggatt` to within 0.05 mm
     and dominant-component slope matches exactly for 99.98% of map units.
3. **Rasterize** — each map unit's values are painted onto the region's native
   map-unit raster grid: a pure lookup with no resampling, so every pixel is
   exactly its map unit's value and `mukey` is a byte-for-byte copy of the
   source raster.
4. **Validate** — sampled pixels are compared back against the source raster
   and the derived tables before a release is tagged.

Each aggregation choice is recorded per-array in the `aggregation_method` /
`aggregation_algorithm_id` attributes. Full documentation of the
transformations:

- [data-reference.md](https://github.com/colinahill/usda_gnatsgo_data/blob/main/docs/data-reference.md)
  — the complete specification: aggregation algorithms (§5), the Valu1
  reconstruction recipes and their verification status (§6), encoding (§7),
  and the pipeline (§8).
- [future-variables.md](https://github.com/colinahill/usda_gnatsgo_data/blob/main/docs/future-variables.md)
  — every candidate variable not yet included and the decisions each needs.

Processing code (MIT licensed): https://github.com/colinahill/usda_gnatsgo_data

## Provenance & license

- **Source**: USDA NRCS gNATSGO, 2026-02-13 release (7.9 GB archive: 56.75 GB
  GeoPackage, SSURGO schema 2.3.3, plus 12 regional 30 m map-unit rasters,
  25.2 billion cells). Retrieved from the
  [USDA NRCS gNATSGO page](https://www.nrcs.usda.gov/resources/data-and-reports/gridded-national-soil-survey-geographic-database-gnatsgo).
- **Audit artifacts**: the source manifest (with SHA-256 checksums),
  validation reports, and per-property coverage tables are published under
  `audit/2026-02-13/` in this product.
- **Caveat**: the Valu1-family arrays are a reconstruction, not USDA's own
  published table (which no longer exists for this release); see the
  processing repository for the verification status of each family.
- **License**: US Public Domain. USDA NRCS soil survey data carry no copyright
  restrictions and are free to redistribute; USDA NRCS asks for
  acknowledgement when the data are used. This README and the store metadata
  are provided under the same terms.
- **Citation**: Soil Survey Staff. Gridded National Soil Survey Geographic
  (gNATSGO) Database for the United States of America and the Territories,
  2026 release. United States Department of Agriculture, Natural Resources
  Conservation Service.
