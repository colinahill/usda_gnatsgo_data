"""Build the derived per-mukey Parquet intermediates from the extracted tables.

Outputs under ``work/{release}/derived/`` (all written atomically, tmp+rename):

- valu1.parquet                mukey + all 57 Valu1 parameters (source names)
- soil_properties.parquet      (mukey, depth_interval) x 22 value columns +
                               the shared particle-size diagnostics pair
- map_unit_properties.parquet  mukey x 18 value columns (everything 2-D except
                               the mukey array itself), texture as int codes
- horizon_diagnostics.parquet  per-property coverage (audit artifact)
- terrain_audit.parquet        dominant-component selection audit
- texture_classes.json         integer code -> texture name/description
- scientific_report.json       gate results computed before any raster writes
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import polars as pl

from . import config, horizons, mapunit, metadata, valu1
from .catalog import Release

log = logging.getLogger(__name__)

SOIL_VALU1_COLUMNS = {
    "aws": valu1.AWS_COLUMNS,
    "aws_contributing_thickness": valu1.TKA_COLUMNS,
    "soc": valu1.SOC_COLUMNS,
    "soc_contributing_thickness": valu1.TKS_COLUMNS,
}

MAP_UNIT_VALU1_COLUMNS = [
    "droughty",
    "nccpi3corn",
    "nccpi3soy",
    "nccpi3cot",
    "nccpi3sg",
    "nccpi3all",
    "pctearthmc",
    "rootznemc",
    "rootznaws",
    "pwsl1pomu",
    "musumcpct",
    "musumcpcta",
    "musumcpcts",
]


def _read(extract_dir: Path, table: str) -> pl.DataFrame:
    return pl.read_parquet(extract_dir / f"{table}.parquet")


def _write_atomic(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.write_parquet(tmp)
    tmp.rename(path)
    log.info("wrote %s (%s rows)", path, f"{frame.height:,}")


def normalize_valu1_soil(valu1_frame: pl.DataFrame) -> pl.DataFrame:
    """Wide valu1 columns -> long (mukey, depth_interval) with the four
    normalized soil_properties columns."""
    frames = []
    for interval in config.DEPTH_INTERVALS:
        frames.append(
            valu1_frame.select(
                "mukey",
                pl.lit(interval.label).alias(config.DEPTH_DIM),
                *[pl.col(columns[interval.label]).alias(name) for name, columns in SOIL_VALU1_COLUMNS.items()],
            )
        )
    return pl.concat(frames)


def build_intermediates(release: Release, work_dir: Path) -> dict:
    """Run all derivations; returns the scientific report dict (also written)."""
    base = Path(work_dir) / release.release_date
    extract_dir = base / "extracted"
    derived_dir = base / "derived"

    components = _read(extract_dir, "component")
    horizons_df = _read(extract_dir, "chorizon")
    chfrags = _read(extract_dir, "chfrags")
    corestrictions = _read(extract_dir, "corestrictions")
    cointerp = _read(extract_dir, "cointerp")
    mapunit_df = _read(extract_dir, "mapunit")
    chtexturegrp = _read(extract_dir, "chtexturegrp")

    log.info(
        "building valu1 (%s map units, %s components, %s horizons)",
        f"{mapunit_df.height:,}",
        f"{components.height:,}",
        f"{horizons_df.height:,}",
    )
    valu1_frame = valu1.build_valu1(components, horizons_df, chfrags, corestrictions, cointerp, mapunit_df)
    _write_atomic(valu1_frame, derived_dir / "valu1.parquet")

    log.info("building horizon properties")
    horizon_wide, horizon_diag = horizons.build_horizon_properties(components, horizons_df)

    soil = (
        normalize_valu1_soil(valu1_frame)
        .join(horizon_wide, on=["mukey", config.DEPTH_DIM], how="full", coalesce=True)
        .sort(["mukey", config.DEPTH_DIM])
    )
    _write_atomic(soil, derived_dir / "soil_properties.parquet")
    _write_atomic(horizon_diag, derived_dir / "horizon_diagnostics.parquet")

    log.info("building terrain and surface texture")
    terrain_values, terrain_audit = mapunit.terrain(components)
    texture = mapunit.surface_texture(components, horizons_df, chtexturegrp)

    # release-wide texture code table (built from ALL RV texture groups so the
    # attrs cover every code that could appear in a future additive variable)
    all_rv = chtexturegrp.filter(pl.col("rvindicator").fill_null("") == "Yes").select("texture", "texdesc")
    classes = metadata.texture_class_table(list(all_rv.iter_rows()))
    code_by_name = {info["name"]: code for code, info in classes.items()}
    texture_coded = texture.with_columns(
        pl.col("texture").replace_strict(code_by_name, default=None).alias("texture_class")
    ).select("mukey", "texture_class")

    map_unit = (
        valu1_frame.select("mukey", *MAP_UNIT_VALU1_COLUMNS)
        .join(terrain_values, on="mukey", how="full", coalesce=True)
        .join(texture_coded, on="mukey", how="full", coalesce=True)
        .sort("mukey")
    )
    _write_atomic(map_unit, derived_dir / "map_unit_properties.parquet")
    _write_atomic(terrain_audit, derived_dir / "terrain_audit.parquet")
    (derived_dir / "texture_classes.json").write_text(
        json.dumps({str(code): info for code, info in classes.items()}, indent=2)
    )

    report = scientific_report(valu1_frame, soil, map_unit)
    (base / "reports").mkdir(parents=True, exist_ok=True)
    (base / "reports" / "scientific_report.json").write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise RuntimeError(f"scientific validation failed: {[c for c in report['checks'] if not c['passed']]}")
    return report


def scientific_report(valu1_frame: pl.DataFrame, soil: pl.DataFrame, map_unit: pl.DataFrame) -> dict:
    """Pre-rasterization gates (final_plan section 15.2/15.4 subset that is
    checkable on the intermediates)."""
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check(
        "valu1_unique_mukey",
        valu1_frame.height == valu1_frame.select("mukey").n_unique(),
        f"{valu1_frame.height:,} rows",
    )
    check(
        "soil_unique_key",
        soil.height == soil.select("mukey", config.DEPTH_DIM).n_unique(),
        f"{soil.height:,} rows",
    )
    check(
        "map_unit_unique_mukey",
        map_unit.height == map_unit.select("mukey").n_unique(),
        f"{map_unit.height:,} rows",
    )

    for column in ["aws", "soc", "aws_contributing_thickness", "soc_contributing_thickness"]:
        negative = soil.filter(pl.col(column) < 0).height
        check(f"{column}_nonnegative", negative == 0, f"{negative} negative values")

    nccpi_bad = map_unit.filter((pl.col("nccpi3all") < 0) | (pl.col("nccpi3all") > 1)).height
    check("nccpi_in_unit_range", nccpi_bad == 0, f"{nccpi_bad} out-of-range")

    droughty_bad = map_unit.filter(~pl.col("droughty").is_in([0, 1]) & pl.col("droughty").is_not_null()).height
    check("droughty_domain", droughty_bad == 0, f"{droughty_bad} out-of-domain")

    pwsl_bad = map_unit.filter(
        pl.col("pwsl1pomu").is_not_null() & ~(pl.col("pwsl1pomu").is_between(0, 100) | (pl.col("pwsl1pomu") == 999))
    ).height
    check("pwsl_domain", pwsl_bad == 0, f"{pwsl_bad} out-of-domain")

    rootzn_bad = map_unit.filter(pl.col("rootznemc") > 150).height
    check("rootzn_capped", rootzn_bad == 0, f"{rootzn_bad} above 150 cm")

    closure = soil.filter(
        pl.col("sand").is_not_null() & (pl.col("particle_size_contributing_component_percent") >= 80)
    ).with_columns((pl.col("sand") + pl.col("silt") + pl.col("clay")).alias("_sum"))
    if closure.height:
        off = closure.filter((pl.col("_sum") < 97.0) | (pl.col("_sum") > 103.0)).height
        frac = off / closure.height
        check("particle_size_closure", frac < 0.02, f"{frac:.4f} of well-covered rows outside 97-103%")
    else:
        check("particle_size_closure", True, "no well-covered rows to test")

    return {"checks": checks, "passed": all(c["passed"] for c in checks)}
