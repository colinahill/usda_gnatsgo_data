"""Reconstruction of the 57 retained Valu1 parameters (one row per mukey).

The 2026 GeoPackage ships no valu1 table, so these are reimplemented from the
USDA column descriptions (docs/reference/gssurgo-valu1-column-descriptions.pdf)
and the NCCPI v2 user guide references therein. Interpretation choices that the
descriptions leave open are documented per family below and in
docs/data-reference.md; muaggatt.aws*wta and the 2020 Planetary Computer valu1
serve as validation comparators only and are never joined into output.

Column names follow the original Valu1 schema (aws0_5 ... tk0_999s, nccpi3*,
rootznemc, rootznaws, droughty, pwsl1pomu, musumcpct[a|s], pctearthmc).
"""

from __future__ import annotations

import polars as pl

from . import aggregate, config

OVERLAP = aggregate.OVERLAP

# Van Bemmelen factor: organic matter -> organic carbon.
OM_TO_OC = 1.724

# Root-limiting restriction kinds (PDF: hard bedrock, soft bedrock, fragipan,
# duripan, sulfuric material, dense layer). corestrictions reskind spellings:
ROOT_RESTRICTIVE_KINDS = (
    "Lithic bedrock",
    "Paralithic bedrock",
    "Densic bedrock",
    "Densic material",
    "Fragipan",
    "Duripan",
    "Sulfuric",
)
ROOT_ZONE_DEFAULT_CM = 150.0
DROUGHTY_THRESHOLD_MM = 152.0

# PWSL v1 phrases tested against localphase / otherph / muname.
PWSL_PHRASES = ("drained", "undrained", "channeled", "protected", "ponded", "flooded")


def _suffix_columns(prefix: str, postfix: str = "") -> dict[str, str]:
    """{depth label: valu1 column name} e.g. aws0_5 / tk150_999a."""
    return {d.label: f"{prefix}{d.source_suffix}{postfix}" for d in config.DEPTH_INTERVALS}


AWS_COLUMNS = _suffix_columns("aws")
TKA_COLUMNS = _suffix_columns("tk", "a")
SOC_COLUMNS = _suffix_columns("soc")
TKS_COLUMNS = _suffix_columns("tk", "s")

VALU1_COLUMNS: list[str] = [
    *AWS_COLUMNS.values(),
    *TKA_COLUMNS.values(),
    "musumcpcta",
    *SOC_COLUMNS.values(),
    *TKS_COLUMNS.values(),
    "musumcpcts",
    "nccpi3corn",
    "nccpi3soy",
    "nccpi3cot",
    "nccpi3sg",
    "nccpi3all",
    "pctearthmc",
    "rootznemc",
    "rootznaws",
    "droughty",
    "pwsl1pomu",
    "musumcpct",
]


def _earthy_major(components: pl.DataFrame) -> pl.DataFrame:
    """Major earthy components: majcompflag = 'Yes' and a non-miscellaneous
    compkind (soil series or higher taxa that can support crop growth)."""
    return components.filter(
        (pl.col("majcompflag").fill_null("") == "Yes")
        & pl.col("compkind").is_not_null()
        & (pl.col("compkind") != "Miscellaneous area")
        & pl.col("comppct_r").is_not_null()
        & (pl.col("comppct_r") > 0)
    )


def _stock_family(
    overlaps: pl.DataFrame, components: pl.DataFrame, stock_expr: pl.Expr, eligible: pl.Expr
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Shared machinery for the AWS and SOC families.

    ``stock_expr`` is the per-(horizon x interval) extensive contribution;
    ``eligible`` marks horizons with the data the calculation needs. Returns

    - per (mukey, depth_interval): value (comppct-weighted mean of component
      stocks, renormalized over contributing components) and thickness (same
      weighting of eligible overlap thickness);
    - per mukey: the admitted component percent (components with at least one
      eligible horizon anywhere in the profile).
    """
    weights = components.select("mukey", "cokey", "comppct_r").filter(
        pl.col("comppct_r").is_not_null() & (pl.col("comppct_r") > 0)
    )
    eligible_rows = overlaps.filter(eligible)

    component_stock = eligible_rows.group_by("cokey", config.DEPTH_DIM).agg(
        stock_expr.sum().alias("stock"),
        pl.col(OVERLAP).sum().alias("valid_thickness"),
    )
    weighted = component_stock.join(weights, on="cokey", how="inner")
    per_interval = (
        weighted.group_by("mukey", config.DEPTH_DIM)
        .agg(
            (pl.col("stock") * pl.col("comppct_r")).sum().alias("_num"),
            (pl.col("valid_thickness") * pl.col("comppct_r")).sum().alias("_tk_num"),
            pl.col("comppct_r").sum().alias("_pct"),
        )
        .with_columns(
            (pl.col("_num") / pl.col("_pct")).alias("value"),
            (pl.col("_tk_num") / pl.col("_pct")).alias("thickness"),
        )
        .select("mukey", config.DEPTH_DIM, "value", "thickness")
    )

    admitted = (
        eligible_rows.select("cokey")
        .unique()
        .join(weights, on="cokey", how="inner")
        .group_by("mukey")
        .agg(pl.col("comppct_r").sum().alias("admitted_pct"))
    )
    return per_interval, admitted


def _pivot_family(
    per_interval: pl.DataFrame, value_names: dict[str, str], thickness_names: dict[str, str]
) -> pl.DataFrame:
    """Long (mukey, depth_interval, value, thickness) -> wide valu1 columns."""
    out: pl.DataFrame | None = None
    for label in config.DEPTH_LABELS:
        part = per_interval.filter(pl.col(config.DEPTH_DIM) == label).select(
            "mukey",
            pl.col("value").alias(value_names[label]),
            pl.col("thickness").alias(thickness_names[label]),
        )
        out = part if out is None else out.join(part, on="mukey", how="full", coalesce=True)
    assert out is not None
    return out


def aws_family(overlaps: pl.DataFrame, components: pl.DataFrame) -> pl.DataFrame:
    """aws{suffix}, tk{suffix}a, musumcpcta.

    Per horizon: awc_r (cm water / cm soil) x overlap_cm x 10 -> mm of water.
    """
    per_interval, admitted = _stock_family(
        overlaps,
        components,
        stock_expr=pl.col("awc_r") * pl.col(OVERLAP) * 10.0,
        eligible=pl.col("awc_r").is_not_null(),
    )
    wide = _pivot_family(per_interval, AWS_COLUMNS, TKA_COLUMNS)
    return wide.join(
        admitted.select("mukey", pl.col("admitted_pct").alias("musumcpcta")), on="mukey", how="full", coalesce=True
    )


def soc_family(overlaps: pl.DataFrame, components: pl.DataFrame, chfrags: pl.DataFrame) -> pl.DataFrame:
    """soc{suffix}, tk{suffix}s, musumcpcts.

    Per horizon: overlap_cm x dbthirdbar_r (g/cm3) x (om_r / 1.724 / 100)
    x (1 - fragvol/100) x 10000 -> g C m-2, where fragvol is the horizon's
    summed chfrags fragvol_r (missing fragments = 0; SOC is a stock, not a
    concentration).
    """
    frag = (
        chfrags.filter(pl.col("fragvol_r").is_not_null())
        .group_by("chkey")
        .agg(pl.col("fragvol_r").sum().clip(0.0, 100.0).alias("_fragvol"))
    )
    with_frags = overlaps.join(frag, on="chkey", how="left").with_columns(pl.col("_fragvol").fill_null(0.0))
    stock = (
        pl.col(OVERLAP)
        * pl.col("dbthirdbar_r")
        * (pl.col("om_r") / OM_TO_OC / 100.0)
        * (1.0 - pl.col("_fragvol") / 100.0)
        * 10_000.0
    )
    per_interval, admitted = _stock_family(
        with_frags,
        components,
        stock_expr=stock,
        eligible=pl.col("om_r").is_not_null() & pl.col("dbthirdbar_r").is_not_null(),
    )
    wide = _pivot_family(per_interval, SOC_COLUMNS, TKS_COLUMNS)
    return wide.join(
        admitted.select("mukey", pl.col("admitted_pct").alias("musumcpcts")), on="mukey", how="full", coalesce=True
    )


def root_zone(overlaps: pl.DataFrame, components: pl.DataFrame, corestrictions: pl.DataFrame) -> pl.DataFrame:
    """rootznemc, rootznaws, droughty (major earthy components, weighted average).

    Root zone depth per component = the shallowest of: 150 cm (default/cap),
    the first root-limiting restriction (ROOT_RESTRICTIVE_KINDS), and the top
    of the first horizon with ph1to1h2o_r < 3.5 or ec_r > 12.
    """
    earthy = _earthy_major(components).select("mukey", "cokey", "comppct_r")

    restriction_depth = (
        corestrictions.filter(pl.col("reskind").is_in(ROOT_RESTRICTIVE_KINDS) & pl.col("resdept_r").is_not_null())
        .group_by("cokey")
        .agg(pl.col("resdept_r").min().alias("_restriction"))
    )
    horizon_limit = (
        overlaps.filter(pl.col(config.DEPTH_DIM) == "0_profile")  # one row per horizon
        .filter((pl.col("ph1to1h2o_r") < 3.5) | (pl.col("ec_r") > 12.0))
        .group_by("cokey")
        .agg(pl.col("hzdept_r").min().alias("_hz_limit"))
    )

    depths = (
        earthy.join(restriction_depth, on="cokey", how="left")
        .join(horizon_limit, on="cokey", how="left")
        .with_columns(
            pl.min_horizontal(
                pl.lit(ROOT_ZONE_DEFAULT_CM),
                pl.col("_restriction"),
                pl.col("_hz_limit"),
            )
            .clip(0.0, ROOT_ZONE_DEFAULT_CM)
            .alias("root_depth")
        )
    )

    # AWS within [0, root_depth] per component
    aws_rows = overlaps.filter((pl.col(config.DEPTH_DIM) == "0_profile") & pl.col("awc_r").is_not_null()).select(
        "cokey", "hzdept_r", "hzdepb_r", "awc_r"
    )
    component_rzaws = (
        depths.join(aws_rows, on="cokey", how="left")
        .with_columns(
            (
                (pl.min_horizontal(pl.col("hzdepb_r"), pl.col("root_depth")) - pl.col("hzdept_r")).clip(lower_bound=0.0)
                * pl.col("awc_r")
                * 10.0
            ).alias("_aws_mm")
        )
        .group_by("mukey", "cokey", "comppct_r", "root_depth")
        .agg(pl.col("_aws_mm").sum().alias("rz_aws"))
    )

    per_mukey = (
        component_rzaws.group_by("mukey")
        .agg(
            (pl.col("root_depth") * pl.col("comppct_r")).sum().alias("_depth_num"),
            (pl.col("rz_aws") * pl.col("comppct_r")).sum().alias("_aws_num"),
            pl.col("comppct_r").sum().alias("_pct"),
        )
        .with_columns(
            (pl.col("_depth_num") / pl.col("_pct")).round(0).alias("rootznemc"),
            (pl.col("_aws_num") / pl.col("_pct")).alias("rootznaws"),
        )
        .with_columns(
            pl.when(pl.col("rootznaws").is_not_null())
            .then((pl.col("rootznaws") <= DROUGHTY_THRESHOLD_MM).cast(pl.Int64))
            .otherwise(None)
            .alias("droughty")
        )
        .select("mukey", "rootznemc", "rootznaws", "droughty")
    )
    return per_mukey


NCCPI_CROPS = {"nccpi3corn": "Corn", "nccpi3soy": "Soybeans", "nccpi3cot": "Cotton", "nccpi3sg": "Small Grains"}


def nccpi(components: pl.DataFrame, cointerp: pl.DataFrame) -> pl.DataFrame:
    """nccpi3corn/soy/cot/sg/all + pctearthmc (major earthy components).

    cointerp is pre-filtered to NCCPI rows at extraction. In the 2026 release
    each crop submodel is published as its own main rule (mrulename ==
    rulename, ruledepth 0), and crop submodels are populated only sparsely
    (~17% of components) while the overall non-irrigated NCCPI rule covers
    every component and equals max(crop submodels) in ~97% of dual-rated
    components. So: crops are matched by rulename substring (Irrigated variant
    excluded); nccpi3all is the overall rule's rating, falling back to the
    per-component crop maximum where the overall rating is absent.
    """
    earthy = _earthy_major(components).select("mukey", "cokey", "comppct_r")

    rated = cointerp.filter(pl.col("interphr").is_not_null() & ~pl.col("rulename").str.contains("Irrigated"))
    per_component = earthy.select("cokey").unique()
    for column, token in NCCPI_CROPS.items():
        crop = (
            rated.filter(pl.col("rulename").str.contains(token))
            .group_by("cokey")
            .agg(pl.col("interphr").max().alias(column))
        )
        per_component = per_component.join(crop, on="cokey", how="left")
    overall = (
        rated.filter(pl.col("rulename").str.starts_with("NCCPI - National Commodity"))
        .group_by("cokey")
        .agg(pl.col("interphr").max().alias("_overall"))
    )
    per_component = per_component.join(overall, on="cokey", how="left").with_columns(
        pl.coalesce(pl.col("_overall"), pl.max_horizontal([pl.col(c) for c in NCCPI_CROPS])).alias("nccpi3all")
    )

    weighted = earthy.join(per_component, on="cokey", how="left")
    aggs = []
    for column in [*NCCPI_CROPS, "nccpi3all"]:
        aggs.extend(
            [
                (pl.col(column) * pl.col("comppct_r")).sum().alias(f"_num_{column}"),
                pl.col("comppct_r").filter(pl.col(column).is_not_null()).sum().alias(f"_pct_{column}"),
            ]
        )
    per_mukey = (
        weighted.group_by("mukey")
        .agg(*aggs, pl.col("comppct_r").sum().alias("pctearthmc"))
        .with_columns(
            *[
                pl.when(pl.col(f"_pct_{c}") > 0)
                .then(pl.col(f"_num_{c}") / pl.col(f"_pct_{c}"))
                .otherwise(None)
                .alias(c)
                for c in [*NCCPI_CROPS, "nccpi3all"]
            ]
        )
        .select("mukey", *NCCPI_CROPS, "nccpi3all", "pctearthmc")
    )
    return per_mukey


def _contains_phrase(column: str) -> pl.Expr:
    pattern = "(?i)(" + "|".join(PWSL_PHRASES) + ")"
    return pl.col(column).fill_null("").str.contains(pattern)


def pwsl(components: pl.DataFrame, mapunit: pl.DataFrame) -> pl.DataFrame:
    """pwsl1pomu: percent of the map unit meeting PWSL v1 criteria; 999 = water.

    Component tagging (per the Valu1 description): hydricrating 'Yes' -> PWSL;
    'No' -> not; 'Unranked' -> poorly/very poorly drained class or a
    drained/undrained/channeled/protected/ponded/flooded phrase in localphase or
    otherph; remaining unranked components fall back to those phrases in the
    map unit name. Water bodies: the map unit name contains 'water(s)' as a
    word, or components named 'Water' sum to >= 80 percent.
    """
    names = mapunit.select("mukey", pl.col("muname").fill_null("").alias("_muname"))
    comp = components.select(
        "mukey", "cokey", "comppct_r", "hydricrating", "drainagecl", "localphase", "otherph", "compname"
    ).join(names, on="mukey", how="left")

    rating = pl.col("hydricrating").fill_null("").str.to_lowercase()
    unranked_phrase = _contains_phrase("localphase") | _contains_phrase("otherph")
    unranked_drained = pl.col("drainagecl").fill_null("").is_in(["Poorly drained", "Very poorly drained"])
    muname_phrase = _contains_phrase("_muname")

    tagged = comp.with_columns(
        pl.when(rating == "yes")
        .then(True)
        .when(rating == "no")
        .then(False)
        .otherwise(unranked_drained | unranked_phrase | muname_phrase)
        .alias("_pwsl")
    )

    per_mukey = tagged.group_by("mukey").agg(
        pl.col("comppct_r").filter(pl.col("_pwsl") & pl.col("comppct_r").is_not_null()).sum().alias("_pwsl_pct"),
        pl.col("comppct_r")
        .filter((pl.col("compname").fill_null("") == "Water") & pl.col("comppct_r").is_not_null())
        .sum()
        .alias("_water_pct"),
    )
    water_name = names.with_columns(pl.col("_muname").str.contains(r"(?i)\bwaters?\b").alias("_water_name")).select(
        "mukey", "_water_name"
    )
    return (
        per_mukey.join(water_name, on="mukey", how="left")
        .with_columns(
            pl.when(pl.col("_water_name").fill_null(False) | (pl.col("_water_pct") >= 80.0))
            .then(999)
            .otherwise(pl.col("_pwsl_pct").round(0).cast(pl.Int64))
            .alias("pwsl1pomu")
        )
        .select("mukey", "pwsl1pomu")
    )


def musumcpct(components: pl.DataFrame) -> pl.DataFrame:
    """Sum of comppct_r over all listed components; never clipped to 100."""
    return (
        components.filter(pl.col("comppct_r").is_not_null())
        .group_by("mukey")
        .agg(pl.col("comppct_r").sum().alias("musumcpct"))
    )


def build_valu1(
    components: pl.DataFrame,
    horizons: pl.DataFrame,
    chfrags: pl.DataFrame,
    corestrictions: pl.DataFrame,
    cointerp: pl.DataFrame,
    mapunit: pl.DataFrame,
) -> pl.DataFrame:
    """All 57 Valu1 parameters, one row per mukey (mukeys from mapunit)."""
    # keep the (horizon x interval) frame narrow: ~30M rows on the real release
    needed = ["cokey", "chkey", "hzdept_r", "hzdepb_r", "awc_r", "om_r", "dbthirdbar_r", "ph1to1h2o_r", "ec_r"]
    overlaps = aggregate.horizon_interval_overlaps(horizons.select(needed))

    out = mapunit.select("mukey").unique()
    for frame in [
        aws_family(overlaps, components),
        soc_family(overlaps, components, chfrags),
        root_zone(overlaps, components, corestrictions),
        nccpi(components, cointerp),
        pwsl(components, mapunit),
        musumcpct(components),
    ]:
        out = out.join(frame, on="mukey", how="left")
    missing = [c for c in VALU1_COLUMNS if c not in out.columns]
    if missing:
        raise RuntimeError(f"valu1 build missing columns: {missing}")
    return out.select("mukey", *VALU1_COLUMNS).sort("mukey")
