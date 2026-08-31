"""Depth-overlap math and the versioned aggregation-operator registry.

All operators work on Polars frames extracted from the GeoPackage:

- components: mukey, cokey, comppct_r, majcompflag, compkind, ...
- horizons:   cokey, chkey, hzdept_r, hzdepb_r, <property columns>

The registry maps a VariableSpec.algorithm_id to an implementation; config.py
rejects unknown ids, and every implementation carries hand-computed unit-test
fixtures. Missing property values are never replaced with zero: numerators and
denominators are computed explicitly and NULL propagates to the output.
"""

from __future__ import annotations

import polars as pl

from . import config

OVERLAP = "overlap_cm"


def horizon_interval_overlaps(horizons: pl.DataFrame) -> pl.DataFrame:
    """One row per (horizon x depth interval) with positive overlap.

    For profile-ended intervals the open bottom is each component's reported
    profile bottom (max hzdepb_r), never a literal 999 cm.

    Requires cokey, chkey, hzdept_r, hzdepb_r; preserves all other columns.
    """
    horizons = horizons.filter(
        pl.col("hzdept_r").is_not_null() & pl.col("hzdepb_r").is_not_null() & (pl.col("hzdepb_r") > pl.col("hzdept_r"))
    ).with_columns(pl.col("hzdepb_r").max().over("cokey").alias("_profile_bottom"))

    frames = []
    for interval in config.DEPTH_INTERVALS:
        bottom = pl.lit(float(interval.bottom_cm)) if interval.bottom_cm is not None else pl.col("_profile_bottom")
        overlap = (
            pl.min_horizontal(pl.col("hzdepb_r"), bottom)
            - pl.max_horizontal(pl.col("hzdept_r"), pl.lit(float(interval.top_cm)))
        ).alias(OVERLAP)
        frames.append(
            horizons.with_columns(overlap, pl.lit(interval.label).alias(config.DEPTH_DIM)).filter(pl.col(OVERLAP) > 0)
        )
    return pl.concat(frames).drop("_profile_bottom")


def _component_weights(components: pl.DataFrame) -> pl.DataFrame:
    return components.select("mukey", "cokey", "comppct_r").filter(
        pl.col("comppct_r").is_not_null() & (pl.col("comppct_r") > 0)
    )


def _mukey_weighted(component_values: pl.DataFrame, components: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    """comppct_r-weighted mean over components with a value, with explicit
    renormalization and coverage diagnostics.

    component_values: cokey, *group, value, valid_thickness
    returns: mukey, *group, value, contributing_thickness, contributing_component_percent
    """
    weighted = component_values.join(_component_weights(components), on="cokey", how="inner")
    return (
        weighted.group_by(["mukey", *group])
        .agg(
            (pl.col("value") * pl.col("comppct_r")).sum().alias("_num"),
            (pl.col("valid_thickness") * pl.col("comppct_r")).sum().alias("_tk_num"),
            pl.col("comppct_r").sum().alias("contributing_component_percent"),
        )
        .with_columns(
            (pl.col("_num") / pl.col("contributing_component_percent")).alias("value"),
            (pl.col("_tk_num") / pl.col("contributing_component_percent")).alias("contributing_thickness"),
        )
        .drop("_num", "_tk_num")
    )


def linear_overlap_component_weighted_mean(
    overlaps: pl.DataFrame, components: pl.DataFrame, value_col: str
) -> pl.DataFrame:
    """The default intensive-property operator (algorithm id
    linear_overlap_component_weighted_mean), per final_plan section 6.1.

    Returns mukey, depth_interval, value, contributing_thickness,
    contributing_component_percent (one row per computable pair).
    """
    eligible = overlaps.filter(pl.col(value_col).is_not_null())
    component_values = (
        eligible.group_by("cokey", config.DEPTH_DIM)
        .agg(
            (pl.col(value_col) * pl.col(OVERLAP)).sum().alias("_num"),
            pl.col(OVERLAP).sum().alias("valid_thickness"),
        )
        .with_columns((pl.col("_num") / pl.col("valid_thickness")).alias("value"))
        .drop("_num")
    )
    return _mukey_weighted(component_values, components, [config.DEPTH_DIM])


def composition_shared_mask_weighted_mean(
    overlaps: pl.DataFrame, components: pl.DataFrame, value_cols: tuple[str, str, str]
) -> pl.DataFrame:
    """Sand/silt/clay under ONE shared eligibility mask (all three finite), so
    the aggregated values retain compositional closure where coverage is
    complete. Returns mukey, depth_interval, one value column per input, plus
    the shared contributing_thickness / contributing_component_percent pair.
    """
    mask = pl.all_horizontal([pl.col(c).is_not_null() for c in value_cols])
    eligible = overlaps.filter(mask)
    component_values = eligible.group_by("cokey", config.DEPTH_DIM).agg(
        *[(pl.col(c) * pl.col(OVERLAP)).sum().alias(f"_num_{c}") for c in value_cols],
        pl.col(OVERLAP).sum().alias("valid_thickness"),
    )
    weighted = component_values.join(_component_weights(components), on="cokey", how="inner")
    out = (
        weighted.group_by("mukey", config.DEPTH_DIM)
        .agg(
            *[
                ((pl.col(f"_num_{c}") / pl.col("valid_thickness")) * pl.col("comppct_r")).sum().alias(f"_num_{c}")
                for c in value_cols
            ],
            (pl.col("valid_thickness") * pl.col("comppct_r")).sum().alias("_tk_num"),
            pl.col("comppct_r").sum().alias("contributing_component_percent"),
        )
        .with_columns(
            *[(pl.col(f"_num_{c}") / pl.col("contributing_component_percent")).alias(c) for c in value_cols],
            (pl.col("_tk_num") / pl.col("contributing_component_percent")).alias("contributing_thickness"),
        )
        .drop([f"_num_{c}" for c in value_cols] + ["_tk_num"])
    )
    return out


def dominant_component(components: pl.DataFrame) -> pl.DataFrame:
    """Deterministic dominant component per mukey: major components preferred,
    then highest comppct_r, ties broken by ascending cokey (reproducible).

    Returns mukey, cokey, comppct_r, majcompflag.
    """
    return (
        components.filter(pl.col("comppct_r").is_not_null())
        .sort(
            [
                (pl.col("majcompflag").fill_null("") == "Yes").not_(),  # major first
                -pl.col("comppct_r"),
                pl.col("cokey"),
            ]
        )
        .group_by("mukey", maintain_order=True)
        .agg(pl.col("cokey").first(), pl.col("comppct_r").first(), pl.col("majcompflag").first())
    )


def dominant_component_least_transmissive(
    overlaps: pl.DataFrame, components: pl.DataFrame, value_col: str = "ksat_r"
) -> pl.DataFrame:
    """ksat operator: minimum finite ksat_r among the dominant component's
    horizons overlapping each interval (the least transmissive layer controls
    water movement through the profile).

    Returns mukey, depth_interval, value, contributing_thickness,
    contributing_component_percent (thickness = eligible overlap; percent =
    the dominant component's comppct_r).
    """
    dominant = dominant_component(components).select("mukey", "cokey", "comppct_r")
    eligible = overlaps.filter(pl.col(value_col).is_not_null()).join(dominant, on="cokey", how="inner")
    return eligible.group_by("mukey", config.DEPTH_DIM).agg(
        pl.col(value_col).min().alias("value"),
        pl.col(OVERLAP).sum().alias("contributing_thickness"),
        pl.col("comppct_r").first().alias("contributing_component_percent"),
    )
