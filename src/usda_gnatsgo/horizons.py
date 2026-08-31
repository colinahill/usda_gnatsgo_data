"""Selected horizon properties on the 11-interval depth coordinate.

Produces (from the extracted component/chorizon Parquet):
- a wide frame keyed (mukey, depth_interval) with the 18 selected property
  columns (representative estimates only), including the shared particle-size
  diagnostics pair that is published as rasters;
- a long per-property coverage frame for horizon_diagnostics.parquet (audit
  artifact; traceable through the mukey array).
"""

from __future__ import annotations

import polars as pl

from . import aggregate, config

# variable name -> chorizon representative column, for every property using the
# default linear operator. sand/silt/clay (shared mask) and ksat (least
# transmissive) are handled by their own operators below.
LINEAR_PROPERTIES: dict[str, str] = {
    "organic_matter": "om_r",
    "bulk_density_tenth_bar": "dbtenthbar_r",
    "bulk_density_one_third_bar": "dbthirdbar_r",
    "bulk_density_fifteen_bar": "dbfifteenbar_r",
    "bulk_density_oven_dry": "dbovendry_r",
    "awc": "awc_r",
    "water_content_tenth_bar": "wtenthbar_r",
    "water_content_one_third_bar": "wthirdbar_r",
    "water_content_fifteen_bar": "wfifteenbar_r",
    "satiated_water_content": "wsatiated_r",
    "cec_7": "cec7_r",
    "effective_cec": "ecec_r",
    "sum_of_bases": "sumbases_r",
    "ph": "ph1to1h2o_r",
}

COMPOSITION = {"sand": "sandtotal_r", "silt": "silttotal_r", "clay": "claytotal_r"}

HORIZON_PROPERTY_NAMES = [*COMPOSITION, "ksat", *LINEAR_PROPERTIES]


def build_horizon_properties(components: pl.DataFrame, horizons: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Returns (wide properties frame, long diagnostics frame).

    wide: mukey, depth_interval, 18 property columns,
          particle_size_contributing_thickness,
          particle_size_contributing_component_percent
    long: mukey, depth_interval, property, contributing_thickness,
          contributing_component_percent
    """
    overlaps = aggregate.horizon_interval_overlaps(horizons)
    keys = ["mukey", config.DEPTH_DIM]

    wide: pl.DataFrame | None = None
    diagnostics: list[pl.DataFrame] = []

    def merge(frame: pl.DataFrame) -> None:
        nonlocal wide
        wide = frame if wide is None else wide.join(frame, on=keys, how="full", coalesce=True)

    # sand/silt/clay under the shared eligibility mask (+ published diagnostics)
    composition = aggregate.composition_shared_mask_weighted_mean(
        overlaps, components, tuple(COMPOSITION.values())
    ).rename(
        {source: name for name, source in COMPOSITION.items()}
        | {
            "contributing_thickness": "particle_size_contributing_thickness",
            "contributing_component_percent": "particle_size_contributing_component_percent",
        }
    )
    merge(composition)
    diagnostics.append(
        composition.select(
            *keys,
            pl.lit("particle_size_shared_mask").alias("property"),
            pl.col("particle_size_contributing_thickness").alias("contributing_thickness"),
            pl.col("particle_size_contributing_component_percent").alias("contributing_component_percent"),
        )
    )

    # ksat: least transmissive horizon of the dominant component
    ksat = aggregate.dominant_component_least_transmissive(overlaps, components, "ksat_r")
    merge(ksat.select(*keys, pl.col("value").alias("ksat")))
    diagnostics.append(
        ksat.select(*keys, pl.lit("ksat").alias("property"), "contributing_thickness", "contributing_component_percent")
    )

    # everything else: the default linear operator
    for name, source in LINEAR_PROPERTIES.items():
        result = aggregate.linear_overlap_component_weighted_mean(overlaps, components, source)
        merge(result.select(*keys, pl.col("value").alias(name)))
        diagnostics.append(
            result.select(
                *keys, pl.lit(name).alias("property"), "contributing_thickness", "contributing_component_percent"
            )
        )

    assert wide is not None
    return wide.sort(keys), pl.concat(diagnostics).sort([*keys, "property"])
