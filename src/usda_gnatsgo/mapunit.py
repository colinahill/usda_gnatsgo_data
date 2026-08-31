"""Map-unit-level derivations: terrain (dominant component) and surface texture."""

from __future__ import annotations

import polars as pl

from . import aggregate

TERRAIN_COLUMNS = {
    "slope": "slope_r",
    "slope_length": "slopelenusle_r",
    "elevation": "elev_r",
    "aspect": "aspectrep",
}


def terrain(components: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Dominant-component representative terrain values.

    Returns (values frame: mukey + 4 terrain columns, audit frame recording the
    selected cokey / comppct_r / majcompflag per mukey).
    """
    dominant = aggregate.dominant_component(components)
    values = dominant.join(components.select("cokey", *TERRAIN_COLUMNS.values()), on="cokey", how="left").select(
        "mukey", *[pl.col(src).alias(name) for name, src in TERRAIN_COLUMNS.items()]
    )
    audit = dominant.select(
        "mukey",
        pl.col("cokey").alias("selected_cokey"),
        pl.col("comppct_r").alias("selected_comppct_r"),
        pl.col("majcompflag").alias("selected_majcompflag"),
    )
    return values.sort("mukey"), audit.sort("mukey")


def surface_texture(components: pl.DataFrame, horizons: pl.DataFrame, chtexturegrp: pl.DataFrame) -> pl.DataFrame:
    """Surface texture group string of the dominant component (SDV 'Surface
    Texture' semantics): the dominant component's surface horizon (lowest
    hzdept_r, ties by ascending chkey), RV chtexturegrp row (rvindicator =
    'Yes', ties by ascending chtgkey).

    Returns mukey, texture, texdesc (strings; dictionary encoding happens at
    intermediate-build time so codes cover the whole release).
    """
    dominant = aggregate.dominant_component(components).select("mukey", "cokey")

    surface_horizon = (
        horizons.filter(pl.col("hzdept_r").is_not_null() & pl.col("hzdepb_r").is_not_null())
        .sort(["hzdept_r", "chkey"])
        .group_by("cokey", maintain_order=True)
        .agg(pl.col("chkey").first())
    )

    rv_texture = (
        chtexturegrp.filter((pl.col("rvindicator").fill_null("") == "Yes") & pl.col("texture").is_not_null())
        .sort("chtgkey")
        .group_by("chkey", maintain_order=True)
        .agg(pl.col("texture").first(), pl.col("texdesc").first())
    )

    return (
        dominant.join(surface_horizon, on="cokey", how="left")
        .join(rv_texture, on="chkey", how="left")
        .select("mukey", "texture", "texdesc")
        .sort("mukey")
    )
