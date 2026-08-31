"""Dataset structure as frozen, reviewable configuration.

Every structural fact about the published store lives here: region grids,
depth intervals, variable inventory, encodings, and attribute constants. Any
structural change shows up as a code diff (pattern borrowed from
dynamical-org/reformatters). Ingest order can never
influence the result because structure is decided once, from this module.

Grid numbers are MEASURED from the 2026-02-13 release rasters (see
docs/reference/raster_inventory.csv), never inferred from documentation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Release identity and store versioning
# ---------------------------------------------------------------------------

# Bumped for every published store: a new USDA release bumps minor and builds a
# fresh store at the new v{DATASET_VERSION}.icechunk path (this dataset has no
# time dimension; releases replace, not append). Breaking structural changes
# (re-chunk, re-grid, semantics) bump major. Old store paths remain readable.
DATASET_VERSION = "0.1.0"

# The authoritative USDA release this DATASET_VERSION was built from. catalog.py
# parses the date from the source archive/GeoPackage filenames and refuses to
# run when it does not equal this value.
RELEASE_DATE = "2026-02-13"

NODATA_MUKEY = 0  # background in MURASTER; also the zarr fill for mukey

DEPTH_DIM = "depth_interval"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# Regions (native grids; never reprojected, resampled, or mosaicked)
# ---------------------------------------------------------------------------


class RegionSpec(FrozenModel):
    """Exact grid of one regional MURASTER (the output-grid authority).

    Arrays are created at the source raster's exact shape: source.py asserts
    CRS/transform/shape equality and refuses loudly on any mismatch.
    """

    name: str  # snake_case zarr group name
    original_token: str  # region token as it appears in source filenames
    muraster_filename: str
    saraster_filename: str
    epsg: int
    pixel_size: float  # metres or degrees, square pixels, north-up
    width: int
    height: int
    x_min: float  # upper-left corner x (edge, not pixel centre)
    y_max: float  # upper-left corner y

    def x_coords(self):
        """Pixel-centre x coordinates, ascending."""
        import numpy as np

        return self.x_min + (np.arange(self.width) + 0.5) * self.pixel_size

    def y_coords(self):
        """Pixel-centre y coordinates, descending (north to south)."""
        import numpy as np

        return self.y_max - (np.arange(self.height) + 0.5) * self.pixel_size

    @property
    def geotransform(self) -> str:
        """GDAL GeoTransform string for the spatial_ref attr."""
        return f"{self.x_min} {self.pixel_size} 0.0 {self.y_max} 0.0 {-self.pixel_size}"

    @property
    def units(self) -> str:
        return "degree" if self.epsg == 4326 else "m"


# Measured 2026-02-13 grids (docs/reference/raster_inventory.csv).
REGIONS: dict[str, RegionSpec] = {
    spec.name: spec
    for spec in [
        RegionSpec(
            name="alaska",
            original_token="Alaska",
            muraster_filename="muraster_30m_Alaska.tif",
            saraster_filename="SARASTER_30m_AK_2026.tif",
            epsg=3338,
            pixel_size=30.0,
            width=122_295,
            height=65_835,
            x_min=-2_175_735.0,
            y_max=2_383_905.0,
        ),
        RegionSpec(
            name="american_samoa",
            original_token="American_Samoa",
            muraster_filename="muraster_30m_American_Samoa.tif",
            saraster_filename="SARASTER_30m_American_Samoa_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=5_264,
            height=805,
            x_min=-170.846755755,
            y_max=-14.155463595,
        ),
        RegionSpec(
            name="conus",
            original_token="CONUS",
            muraster_filename="MURASTER_30m_CONUS_2026.tif",
            saraster_filename="SARASTER_30m_CONUS_2026.tif",
            epsg=5070,
            pixel_size=30.0,
            width=153_996,
            height=97_053,
            x_min=-2_356_125.0,
            y_max=3_172_575.0,
        ),
        RegionSpec(
            name="fed_states_micronesia",
            original_token="Fed_States_Micronesia",
            muraster_filename="muraster_30m_Fed_States_Micronesia_2026.tif",
            saraster_filename="SARASTER_30m_Fed_States_Micronesia_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=92_073,
            height=17_599,
            x_min=138.054872605,
            y_max=10.033722075,
        ),
        RegionSpec(
            name="guam",
            original_token="Guam",
            muraster_filename="muraster_30m_Guam.tif",
            saraster_filename="SARASTER_30m_Guam_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=1_249,
            height=1_549,
            x_min=144.618132815,
            y_max=13.654354025,
        ),
        RegionSpec(
            name="hawaii",
            original_token="Hawaii",
            muraster_filename="muraster_30m_Hawaii.tif",
            saraster_filename="SARASTER_30m_HI_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=18_360,
            height=12_252,
            x_min=-159.788160155,
            y_max=22.235075395,
        ),
        RegionSpec(
            name="marshall_is",
            original_token="Marshall_Is",
            muraster_filename="muraster_30m_Marshall_Is_2026.tif",
            saraster_filename="SARASTER_30m_MH_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=2_715,
            height=9_728,
            x_min=171.027176905,
            y_max=8.712442375,
        ),
        RegionSpec(
            name="mexico",
            original_token="Mexico",
            muraster_filename="muraster_30m_Mexico.tif",
            saraster_filename="SARASTER_30m_MX_2026.tif",
            epsg=5070,
            pixel_size=30.0,
            width=370,
            height=451,
            x_min=-486_045.0,
            y_max=471_885.0,
        ),
        RegionSpec(
            name="northern_mariana_is",
            original_token="Northern_Mariana_Is",
            muraster_filename="muraster_30m_Northern_Mariana_Is_2026.tif",
            saraster_filename="SARASTER_30m_MP_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=2_694,
            height=17_333,
            x_min=145.121412865,
            y_max=18.813313675,
        ),
        RegionSpec(
            name="palau",
            original_token="Palau",
            muraster_filename="muraster_30m_Palau_2026.tif",
            saraster_filename="SARASTER_30m_PW_2026.tif",
            epsg=4326,
            pixel_size=0.00027131,
            width=13_272,
            height=18_879,
            x_min=131.120189005,
            y_max=8.094126885,
        ),
        RegionSpec(
            name="puerto_rico",
            original_token="Puerto_Rico",
            muraster_filename="muraster_30m_Puerto_Rico_2026.tif",
            saraster_filename="SARASTER_30m_PR_2026.tif",
            epsg=32161,
            pixel_size=30.0,
            width=9_978,
            height=2_721,
            x_min=34_335.0,
            y_max=281_445.0,
        ),
        RegionSpec(
            name="virgin_is",
            original_token="Virgin_Is",
            muraster_filename="muraster_30m_Virgin_Is_2026.tif",
            saraster_filename="SARASTER_30m_VI_2026.tif",
            epsg=32161,
            pixel_size=30.0,
            width=2_280,
            height=3_104,
            x_min=335_265.0,
            y_max=270_585.0,
        ),
    ]
}


# ---------------------------------------------------------------------------
# Depth intervals
# ---------------------------------------------------------------------------


class DepthInterval(FrozenModel):
    """One entry of the string depth_interval coordinate.

    The 11 intervals mix disjoint layers and cumulative zones (the Valu1
    convention). `_999` in a source suffix means "to the reported depth of the
    soil profile", not a literal 999 cm bottom.
    """

    label: str
    top_cm: int
    bottom_cm: int | None  # None = open bottom (to reported profile depth)
    kind: Literal["layer", "zone", "layer_and_zone"]
    source_suffix: str

    @property
    def to_reported_profile_depth(self) -> bool:
        return self.bottom_cm is None


DEPTH_INTERVALS: list[DepthInterval] = [
    DepthInterval(label="0_5", top_cm=0, bottom_cm=5, kind="layer_and_zone", source_suffix="0_5"),
    DepthInterval(label="5_20", top_cm=5, bottom_cm=20, kind="layer", source_suffix="5_20"),
    DepthInterval(label="20_50", top_cm=20, bottom_cm=50, kind="layer", source_suffix="20_50"),
    DepthInterval(label="50_100", top_cm=50, bottom_cm=100, kind="layer", source_suffix="50_100"),
    DepthInterval(label="100_150", top_cm=100, bottom_cm=150, kind="layer", source_suffix="100_150"),
    DepthInterval(label="150_profile", top_cm=150, bottom_cm=None, kind="layer", source_suffix="150_999"),
    DepthInterval(label="0_20", top_cm=0, bottom_cm=20, kind="zone", source_suffix="0_20"),
    DepthInterval(label="0_30", top_cm=0, bottom_cm=30, kind="zone", source_suffix="0_30"),
    DepthInterval(label="0_100", top_cm=0, bottom_cm=100, kind="zone", source_suffix="0_100"),
    DepthInterval(label="0_150", top_cm=0, bottom_cm=150, kind="zone", source_suffix="0_150"),
    DepthInterval(label="0_profile", top_cm=0, bottom_cm=None, kind="zone", source_suffix="0_999"),
]

DEPTH_LABELS: list[str] = [d.label for d in DEPTH_INTERVALS]


# ---------------------------------------------------------------------------
# Encoding (zarr v3 sharding, zstd)
# ---------------------------------------------------------------------------


class EncodingSpec(FrozenModel):
    """Sharded zarr v3 encoding.

    128x128 inner chunks keep the primary access pattern cheap (a ~40x40 px
    window in one depth slice touches ~1.7 chunks of 64 KiB raw float32).
    4096x4096 shards (1024 inner chunks, 64 MiB raw float32) are the storage
    object: that size bounds the write unit, so an interrupted backfill retries
    or re-uploads at most 64 MiB per object, a rasterize worker peaks at ~128
    MiB, and the 16 KiB shard index keeps point reads cheap. Object counts stay
    moderate (912 shard objects per CONUS 2-D variable, 1588 across all
    regions). Depth is chunked at 1 so a depth slice is an independent object
    (chosen for single-depth-at-a-time access).
    """

    chunk_y: int = 128
    chunk_x: int = 128
    shard_y: int = 4096
    shard_x: int = 4096
    zstd_level: int = 3

    def chunks(self, ndim: int) -> tuple[int, ...]:
        return (1, self.chunk_y, self.chunk_x) if ndim == 3 else (self.chunk_y, self.chunk_x)

    def shards(self, ndim: int) -> tuple[int, ...]:
        return (1, self.shard_y, self.shard_x) if ndim == 3 else (self.shard_y, self.shard_x)


ENCODING = EncodingSpec()


# ---------------------------------------------------------------------------
# Aggregation algorithm registry (IDs only; implementations in aggregate.py /
# valu1.py / mapunit.py). A VariableSpec may not reference an unknown ID.
# ---------------------------------------------------------------------------

KNOWN_ALGORITHMS: dict[str, str] = {
    "direct_raster": "Direct copy of the MURASTER map-unit key; 0 is background.",
    "linear_overlap_component_weighted_mean": (
        "Per component: overlap-thickness-weighted mean of finite horizon values; per map unit: "
        "comppct_r-weighted mean over components with a finite component value, renormalized by the "
        "contributing component percent. Missing values are never replaced with zero."
    ),
    "composition_shared_mask_weighted_mean": (
        "linear_overlap_component_weighted_mean computed under ONE shared eligibility mask for sand, "
        "silt, and clay (a horizon is eligible only when all three are finite) so compositional "
        "closure near 100 percent is retained where coverage is complete."
    ),
    "dominant_component_least_transmissive": (
        "Select the dominant component (highest comppct_r; ties broken by ascending cokey), then take "
        "the minimum finite ksat_r among its horizons overlapping the interval (the least transmissive "
        "layer controls). Missing when the dominant component has no finite ksat in the interval."
    ),
    "dominant_component": (
        "Deterministic dominant-component value: major components preferred, then highest comppct_r, "
        "ties broken by ascending cokey; the component's representative value is used unchanged."
    ),
    "dominant_component_surface_texture": (
        "Dominant component's surface horizon (lowest hzdept_r), RV chtexturegrp row "
        "(rvindicator = 'Yes'); the texture group string is dictionary-encoded to a stable integer code."
    ),
    "valu1_aws": (
        "USDA Valu1 available water storage reconstruction: per component, sum of awc_r x overlap_cm "
        "x 10 (mm) over horizons with finite awc_r; map unit value is the comppct_r-weighted mean over "
        "components with any eligible horizon, renormalized by the admitted component percent "
        "(musumcpcta). Contributing thickness is the weighted mean of eligible overlap thickness."
    ),
    "valu1_soc": (
        "USDA Valu1 soil organic carbon stock reconstruction: per horizon, overlap_cm x dbthirdbar_r "
        "x (om_r / 1.724 / 100) x (1 - fragvol/100) x 10000 g C m-2, with fragvol the summed chfrags "
        "fragvol_r for the horizon; per component summed over eligible horizons; map unit value "
        "comppct_r-weighted and renormalized by the admitted component percent (musumcpcts)."
    ),
    "valu1_nccpi": (
        "NCCPI v3 ratings from cointerp: per-crop submodel interphr (Irrigated variant excluded), "
        "comppct_r-weighted average over major earthy components, renormalized over rated "
        "components; nccpi3all is the overall non-irrigated NCCPI rule's rating per component "
        "(equal to the highest crop value where crops are rated), falling back to the crop maximum."
    ),
    "valu1_pctearthmc": ("Sum of comppct_r over major earthy components (majcompflag = 'Yes', non-miscellaneous)."),
    "valu1_rootzone": (
        "Root zone depth per major earthy component: shallowest of 150 cm, root-limiting restrictions "
        "(corestrictions lithic/paralithic/densic bedrock, fragipan, duripan, sulfuric), and horizon "
        "criteria (ph1to1h2o_r < 3.5 or ec_r > 12); rootznaws is the AWS summed over [0, root zone "
        "depth]; both comppct_r-weighted over major earthy components."
    ),
    "valu1_droughty": (
        "1 where the map unit rootznaws <= 152 mm, 0 otherwise; missing for map units with no valid root-zone result."
    ),
    "valu1_pwsl": (
        "Potential Wetland Soil Landscapes v1: components tagged by hydricrating = 'Yes', or "
        "'Unranked' with poorly/very-poorly drained class or drained/undrained/channeled/protected/"
        "ponded/flooded phrases in localphase/otherph (map-unit-name phrase fallback); pwsl1pomu is "
        "the summed comppct_r of tagged components; 999 marks water bodies (map unit named as water "
        "or components named 'Water' summing to >= 80 percent)."
    ),
    "valu1_musumcpct": "Sum of comppct_r over all listed components; never clipped to 100.",
    # Placeholder for declared-but-deferred variables whose operator has not
    # been reviewed. Only valid while status == 'deferred'.
    "unreviewed": "No approved aggregation operator yet; see docs/future-variables.md.",
}


# ---------------------------------------------------------------------------
# Variable inventory
# ---------------------------------------------------------------------------

GroupName = Literal["soil_properties", "map_unit_properties", "soil_properties/diagnostics"]

DIMS_3D = (DEPTH_DIM, "y", "x")
DIMS_2D = ("y", "x")


class VariableSpec(FrozenModel):
    """One output array: identity, encoding, source, and algorithm.

    Adding a future variable is one entry here (status='included'), plus a new
    KNOWN_ALGORITHMS id and its implementation if the operator is new. Deferred
    entries document intent without being templated or rasterized.
    """

    name: str
    group: GroupName
    dims: tuple[str, ...]
    dtype: str
    fill_value: float | int  # zarr fill; float('nan') for floats
    units: str
    long_name: str
    source_table: str
    source_fields: tuple[str, ...]
    algorithm_id: str
    algorithm_version: str = "1.0"
    component_scope: str = "all components"
    valid_range: tuple[float, float] | None = None
    missing_value_semantics: str = "zarr fill value; no CF _FillValue attr is set"
    status: Literal["included", "deferred"] = "included"
    comment: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.algorithm_id not in KNOWN_ALGORITHMS:
            raise ValueError(f"{self.name}: unknown algorithm_id {self.algorithm_id!r}")
        if self.status == "included" and self.algorithm_id == "unreviewed":
            raise ValueError(f"{self.name}: an included variable needs a reviewed algorithm")
        if self.dims not in (DIMS_3D, DIMS_2D):
            raise ValueError(f"{self.name}: unsupported dims {self.dims}")
        return self

    @property
    def array_path(self) -> str:
        return f"{self.group}/{self.name}"


def _soil(name: str, source_field: str, units: str, long_name: str, **kw) -> VariableSpec:
    """A float32 (depth_interval, y, x) horizon property in soil_properties."""
    return VariableSpec(
        name=name,
        group="soil_properties",
        dims=DIMS_3D,
        dtype="float32",
        fill_value=float("nan"),
        units=units,
        long_name=long_name,
        source_table=kw.pop("source_table", "chorizon"),
        source_fields=kw.pop("source_fields", (source_field,)),
        algorithm_id=kw.pop("algorithm_id", "linear_overlap_component_weighted_mean"),
        missing_value_semantics="NaN where no eligible horizon/component data exist",
        **kw,
    )


VARIABLES: list[VariableSpec] = [
    # --- Valu1 depth families (soil_properties) ---
    _soil(
        "aws",
        "awc_r",
        "mm",
        "Available water storage (Valu1 reconstruction)",
        source_table="component, chorizon",
        algorithm_id="valu1_aws",
        component_scope="components admitted to the AWS calculation (musumcpcta)",
        valid_range=(0.0, 5000.0),
    ),
    _soil(
        "soc",
        "om_r",
        "g m-2",
        "Soil organic carbon stock (Valu1 reconstruction)",
        source_table="component, chorizon, chfrags",
        source_fields=("om_r", "dbthirdbar_r", "fragvol_r"),
        algorithm_id="valu1_soc",
        component_scope="components admitted to the SOC calculation (musumcpcts)",
        valid_range=(0.0, 1_000_000.0),
    ),
    _soil(
        "aws_contributing_thickness",
        "awc_r",
        "cm",
        "Thickness of soil actually contributing to the AWS calculation",
        source_table="component, chorizon",
        algorithm_id="valu1_aws",
        component_scope="components admitted to the AWS calculation (musumcpcta)",
        valid_range=(0.0, 1000.0),
    ),
    _soil(
        "soc_contributing_thickness",
        "om_r",
        "cm",
        "Thickness of soil actually contributing to the SOC calculation",
        source_table="component, chorizon, chfrags",
        source_fields=("om_r", "dbthirdbar_r"),
        algorithm_id="valu1_soc",
        component_scope="components admitted to the SOC calculation (musumcpcts)",
        valid_range=(0.0, 1000.0),
    ),
    # --- Selected horizon properties (representative estimates only) ---
    _soil(
        "sand",
        "sandtotal_r",
        "percent",
        "Total sand (0.05-2.0 mm), weight percent of the fine-earth fraction",
        algorithm_id="composition_shared_mask_weighted_mean",
        valid_range=(0.0, 100.0),
    ),
    _soil(
        "silt",
        "silttotal_r",
        "percent",
        "Total silt (0.002-0.05 mm), weight percent of the fine-earth fraction",
        algorithm_id="composition_shared_mask_weighted_mean",
        valid_range=(0.0, 100.0),
    ),
    _soil(
        "clay",
        "claytotal_r",
        "percent",
        "Total clay (< 0.002 mm), weight percent of the fine-earth fraction",
        algorithm_id="composition_shared_mask_weighted_mean",
        valid_range=(0.0, 100.0),
    ),
    _soil("organic_matter", "om_r", "percent", "Organic matter, weight percent", valid_range=(0.0, 100.0)),
    _soil(
        "bulk_density_tenth_bar",
        "dbtenthbar_r",
        "g cm-3",
        "Bulk density at 1/10 bar water tension",
        valid_range=(0.0, 2.6),
    ),
    _soil(
        "bulk_density_one_third_bar",
        "dbthirdbar_r",
        "g cm-3",
        "Bulk density at 1/3 bar water tension",
        valid_range=(0.0, 2.6),
    ),
    _soil(
        "bulk_density_fifteen_bar",
        "dbfifteenbar_r",
        "g cm-3",
        "Bulk density at 15 bar water tension",
        valid_range=(0.0, 2.6),
    ),
    _soil("bulk_density_oven_dry", "dbovendry_r", "g cm-3", "Oven-dry bulk density", valid_range=(0.0, 2.6)),
    _soil(
        "ksat",
        "ksat_r",
        "um s-1",
        "Saturated hydraulic conductivity: least transmissive horizon of the dominant component",
        algorithm_id="dominant_component_least_transmissive",
        component_scope="dominant component",
        valid_range=(0.0, 1000.0),
        comment=(
            "NOT a weighted mean: the minimum representative ksat among the dominant component's "
            "horizons overlapping the interval (the least transmissive layer controls water movement)."
        ),
    ),
    _soil("awc", "awc_r", "cm cm-1", "Available water capacity", valid_range=(0.0, 1.0)),
    _soil(
        "water_content_tenth_bar",
        "wtenthbar_r",
        "percent",
        "Volumetric water content at 1/10 bar",
        valid_range=(0.0, 100.0),
    ),
    _soil(
        "water_content_one_third_bar",
        "wthirdbar_r",
        "percent",
        "Volumetric water content at 1/3 bar",
        valid_range=(0.0, 100.0),
    ),
    _soil(
        "water_content_fifteen_bar",
        "wfifteenbar_r",
        "percent",
        "Volumetric water content at 15 bar",
        valid_range=(0.0, 100.0),
    ),
    _soil(
        "satiated_water_content",
        "wsatiated_r",
        "percent",
        "Satiated volumetric water content",
        valid_range=(0.0, 100.0),
    ),
    _soil("cec_7", "cec7_r", "meq per 100 g", "Cation-exchange capacity at pH 7", valid_range=(0.0, 500.0)),
    _soil("effective_cec", "ecec_r", "meq per 100 g", "Effective cation-exchange capacity", valid_range=(0.0, 500.0)),
    _soil("sum_of_bases", "sumbases_r", "meq per 100 g", "Sum of extractable bases", valid_range=(0.0, 500.0)),
    _soil(
        "ph",
        "ph1to1h2o_r",
        "pH",
        "Soil reaction, 1:1 water",
        valid_range=(1.8, 11.0),
        comment="Thickness/component-weighted arithmetic mean of pH values (matches SDV practice).",
    ),
    # --- Shared particle-size coverage diagnostics ---
    VariableSpec(
        name="particle_size_contributing_thickness",
        group="soil_properties/diagnostics",
        dims=DIMS_3D,
        dtype="float32",
        fill_value=float("nan"),
        units="cm",
        long_name="Thickness contributing to the shared sand/silt/clay eligibility mask",
        source_table="chorizon",
        source_fields=("sandtotal_r", "silttotal_r", "claytotal_r", "hzdept_r", "hzdepb_r"),
        algorithm_id="composition_shared_mask_weighted_mean",
        missing_value_semantics="NaN where no eligible horizon/component data exist",
        valid_range=(0.0, 1000.0),
    ),
    VariableSpec(
        name="particle_size_contributing_component_percent",
        group="soil_properties/diagnostics",
        dims=DIMS_3D,
        dtype="float32",
        fill_value=float("nan"),
        units="percent",
        long_name="Component percent contributing to the shared sand/silt/clay eligibility mask",
        source_table="component, chorizon",
        source_fields=("comppct_r",),
        algorithm_id="composition_shared_mask_weighted_mean",
        missing_value_semantics="NaN where no eligible horizon/component data exist",
        valid_range=(0.0, 200.0),
    ),
    # --- Map-unit properties (2-D) ---
    VariableSpec(
        name="mukey",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint32",
        fill_value=NODATA_MUKEY,
        units="1",
        long_name="Map unit key (join to SSURGO/gNATSGO relational tables)",
        source_table="MURASTER",
        source_fields=("mukey",),
        algorithm_id="direct_raster",
        missing_value_semantics="0 is background (outside the surveyed area)",
    ),
    VariableSpec(
        name="droughty",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint8",
        fill_value=255,
        units="1",
        long_name="Drought-vulnerable soil landscape (root-zone AWS <= 152 mm)",
        source_table="component, chorizon, corestrictions",
        source_fields=("comppct_r", "awc_r"),
        algorithm_id="valu1_droughty",
        component_scope="major earthy components",
        valid_range=(0, 1),
        missing_value_semantics="255 = missing (includes miscellaneous areas and water bodies)",
    ),
    *[
        VariableSpec(
            name=f"nccpi3{crop}",
            group="map_unit_properties",
            dims=DIMS_2D,
            dtype="float32",
            fill_value=float("nan"),
            units="1",
            long_name=long_name,
            source_table="component, cointerp",
            source_fields=("interphr", "comppct_r", "majcompflag"),
            algorithm_id="valu1_nccpi",
            component_scope="major earthy components",
            valid_range=(0.0, 1.0),
            missing_value_semantics="NaN where data are incomplete or not available",
        )
        for crop, long_name in [
            ("corn", "National Commodity Crop Productivity Index v3, corn"),
            ("soy", "National Commodity Crop Productivity Index v3, soybeans"),
            ("cot", "National Commodity Crop Productivity Index v3, cotton"),
            ("sg", "National Commodity Crop Productivity Index v3, small grains"),
            ("all", "National Commodity Crop Productivity Index v3, highest crop value"),
        ]
    ],
    VariableSpec(
        name="pctearthmc",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint8",
        fill_value=255,
        units="percent",
        long_name="Percent of the map unit that is major earthy components",
        source_table="component",
        source_fields=("comppct_r", "majcompflag", "compkind"),
        algorithm_id="valu1_pctearthmc",
        component_scope="major earthy components",
        valid_range=(0, 100),
        missing_value_semantics="255 = missing",
    ),
    VariableSpec(
        name="rootznemc",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="cm",
        long_name="Root zone depth for commodity crops (150 cm cap/default when unrestricted)",
        source_table="component, chorizon, corestrictions",
        source_fields=("resdept_r", "reskind", "ph1to1h2o_r", "ec_r"),
        algorithm_id="valu1_rootzone",
        component_scope="major earthy components",
        valid_range=(0, 150),
        missing_value_semantics="65535 = missing",
    ),
    VariableSpec(
        name="rootznaws",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="mm",
        long_name="Root zone available water storage",
        source_table="component, chorizon, corestrictions",
        source_fields=("awc_r",),
        algorithm_id="valu1_rootzone",
        component_scope="major earthy components",
        valid_range=(0, 5000),
        missing_value_semantics="65535 = missing",
    ),
    VariableSpec(
        name="pwsl1pomu",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="percent",
        long_name="Potential wetland soil landscapes, percent of map unit (999 = water body)",
        source_table="mapunit, component",
        source_fields=("hydricrating", "drainagecl", "localphase", "otherph", "muname", "comppct_r"),
        algorithm_id="valu1_pwsl",
        valid_range=(0, 999),
        missing_value_semantics="65535 = missing; 999 is a water body, NOT 999 percent",
    ),
    VariableSpec(
        name="musumcpct",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="percent",
        long_name="Sum of comppct_r for all listed components (not clipped to 100)",
        source_table="component",
        source_fields=("comppct_r",),
        algorithm_id="valu1_musumcpct",
        valid_range=(0, 200),
        missing_value_semantics="65535 = missing",
    ),
    VariableSpec(
        name="musumcpcta",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="percent",
        long_name="Component percent admitted to the AWS calculation",
        source_table="component, chorizon",
        source_fields=("comppct_r", "awc_r"),
        algorithm_id="valu1_aws",
        valid_range=(0, 200),
        missing_value_semantics="65535 = missing",
    ),
    VariableSpec(
        name="musumcpcts",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="percent",
        long_name="Component percent admitted to the SOC calculation",
        source_table="component, chorizon, chfrags",
        source_fields=("comppct_r", "om_r", "dbthirdbar_r"),
        algorithm_id="valu1_soc",
        valid_range=(0, 200),
        missing_value_semantics="65535 = missing",
    ),
    # --- Terrain (dominant component, representative values) ---
    VariableSpec(
        name="slope",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="float32",
        fill_value=float("nan"),
        units="percent",
        long_name="Representative slope gradient of the dominant component",
        source_table="component",
        source_fields=("slope_r",),
        algorithm_id="dominant_component",
        component_scope="dominant component",
        valid_range=(0.0, 500.0),
        missing_value_semantics="NaN where the dominant component has no value",
    ),
    VariableSpec(
        name="slope_length",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="float32",
        fill_value=float("nan"),
        units="m",
        long_name="Representative USLE slope length of the dominant component",
        source_table="component",
        source_fields=("slopelenusle_r",),
        algorithm_id="dominant_component",
        component_scope="dominant component",
        valid_range=(0.0, 4000.0),
        missing_value_semantics="NaN where the dominant component has no value",
    ),
    VariableSpec(
        name="elevation",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="float32",
        fill_value=float("nan"),
        units="m",
        long_name="Representative elevation of the dominant component",
        source_table="component",
        source_fields=("elev_r",),
        algorithm_id="dominant_component",
        component_scope="dominant component",
        valid_range=(-100.0, 7000.0),
        missing_value_semantics="NaN where the dominant component has no value",
    ),
    VariableSpec(
        name="aspect",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="float32",
        fill_value=float("nan"),
        units="degree",
        long_name="Representative aspect of the dominant component, clockwise from north",
        source_table="component",
        source_fields=("aspectrep",),
        algorithm_id="dominant_component",
        component_scope="dominant component",
        valid_range=(0.0, 360.0),
        missing_value_semantics="NaN where the dominant component has no value",
        comment="Dominant-component value; never an arithmetic mean of angles.",
    ),
    # --- Surface texture (categorical, dictionary-coded) ---
    VariableSpec(
        name="texture_class",
        group="map_unit_properties",
        dims=DIMS_2D,
        dtype="uint16",
        fill_value=65535,
        units="1",
        long_name="Surface texture group of the dominant component (integer-coded)",
        source_table="component, chorizon, chtexturegrp",
        source_fields=("texture", "texdesc", "rvindicator"),
        algorithm_id="dominant_component_surface_texture",
        component_scope="dominant component",
        missing_value_semantics="65535 = missing; codes map to names via the class_names attr",
    ),
    # --- Declared-but-deferred examples (documented in docs/future-variables.md) ---
    VariableSpec(
        name="texture_class_by_depth",
        group="soil_properties",
        dims=DIMS_3D,
        dtype="uint16",
        fill_value=65535,
        units="1",
        long_name="Texture group per depth interval (integer-coded)",
        source_table="component, chorizon, chtexturegrp",
        source_fields=("texture", "rvindicator"),
        algorithm_id="unreviewed",
        status="deferred",
        comment="Needs a reviewed dominant-condition rule for horizons spanning an interval.",
    ),
]

_names = [v.name for v in VARIABLES]
if len(_names) != len(set(_names)):
    raise ValueError("duplicate variable names in config.VARIABLES")


def included_variables() -> list[VariableSpec]:
    return [v for v in VARIABLES if v.status == "included"]


def variables_by_name(names: list[str] | None = None) -> list[VariableSpec]:
    """Resolve --variables input; None/empty means all included variables.

    mukey is always ordered first: rasterization validates lookups against it.
    """
    included = {v.name: v for v in included_variables()}
    if not names:
        chosen = list(included.values())
    else:
        unknown = [n for n in names if n not in included]
        if unknown:
            deferred = [v.name for v in VARIABLES if v.status == "deferred"]
            raise ValueError(f"unknown or deferred variable(s) {unknown}; deferred: {deferred}")
        chosen = [included[n] for n in names]
    return sorted(chosen, key=lambda v: (v.name != "mukey", v.name))


# ---------------------------------------------------------------------------
# Store-wide attributes
# ---------------------------------------------------------------------------

SOURCE_PRODUCT_URL = (
    "https://www.nrcs.usda.gov/resources/data-and-reports/gridded-national-soil-survey-geographic-database-gnatsgo"
)

ROOT_ATTRS = {
    "title": "USDA NRCS gNATSGO analysis-ready soil property raster store",
    "description": (
        "Selected soil properties from the USDA NRCS gridded National Soil Survey Geographic "
        "Database (gNATSGO), rasterized onto each region's native 30 m map-unit grid. One zarr "
        "group per region (native CRS preserved; no mosaicking or reprojection), each with "
        "'soil_properties' (depth-indexed) and 'map_unit_properties' (2-D) subgroups. Values are "
        "derived from the release's relational tables by versioned, documented aggregation "
        "algorithms; see per-variable attrs and docs/data-reference.md in the processing repo."
    ),
    "product": "USDA NRCS gNATSGO analysis-ready soil property raster store",
    "dataset_id": "usda-gnatsgo",
    "dataset_version": DATASET_VERSION,
    "release_date": RELEASE_DATE,
    "license": (
        "US Public Domain. USDA NRCS soil survey data are public domain and free to "
        "redistribute; USDA NRCS asks for acknowledgement when the data are used."
    ),
    "attribution": (
        "Soil Survey Staff. Gridded National Soil Survey Geographic (gNATSGO) Database for the "
        f"United States of America and the Territories, {RELEASE_DATE[:4]} release. United States "
        "Department of Agriculture, Natural Resources Conservation Service. "
        f"Available at {SOURCE_PRODUCT_URL}."
    ),
    "producer": "USDA Natural Resources Conservation Service (NRCS), Soil Survey Staff",
    "source_product_url": SOURCE_PRODUCT_URL,
    "update_frequency": "annual USDA release; each release is published as a new versioned store path",
    "processing_code": "https://github.com/colinahill/usda_gnatsgo_data",
    "region_groups": sorted(REGIONS),
    "region_original_names": {name: spec.original_token for name, spec in sorted(REGIONS.items())},
    "depth_intervals": DEPTH_LABELS,
    "Conventions": "CF-1.10",
}


def region_attrs(region: RegionSpec) -> dict:
    return {
        "region": region.name,
        "original_filename_region_token": region.original_token,
        "mukey_raster_filename": region.muraster_filename,
        "survey_area_raster_filename": region.saraster_filename,
        "epsg": region.epsg,
        "pixel_size": region.pixel_size,
        "shape": [region.height, region.width],
        "geotransform": region.geotransform,
        "bounds_native": [
            region.x_min,
            region.y_max - region.height * region.pixel_size,
            region.x_min + region.width * region.pixel_size,
            region.y_max,
        ],
    }
