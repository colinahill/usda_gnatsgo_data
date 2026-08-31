"""Inspect a located release: inventory artifacts, verify grids, check schema.

Everything structural is verified against the actual files (never trusted from
documentation): each MURASTER must match its RegionSpec exactly, each
MURASTER/SARASTER pair must share a grid, and every relational column the
pipeline reads must exist in the GeoPackage before expensive work starts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from . import config
from .catalog import Release

log = logging.getLogger(__name__)

TRANSFORM_TOL_FRACTION = 1e-6  # of a pixel

# Every relational column the pipeline reads, checked before extraction.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "mapunit": ("mukey", "muname", "mukind"),
    "muaggatt": ("mukey", "slopegraddcp", "slopegradwta", "aws025wta", "aws0100wta", "aws0150wta"),
    "component": (
        "mukey",
        "cokey",
        "comppct_r",
        "compname",
        "compkind",
        "majcompflag",
        "otherph",
        "localphase",
        "hydricrating",
        "drainagecl",
        "slope_r",
        "slopelenusle_r",
        "elev_r",
        "aspectrep",
    ),
    "chorizon": (
        "cokey",
        "chkey",
        "hzdept_r",
        "hzdepb_r",
        "sandtotal_r",
        "silttotal_r",
        "claytotal_r",
        "om_r",
        "dbtenthbar_r",
        "dbthirdbar_r",
        "dbfifteenbar_r",
        "dbovendry_r",
        "ksat_r",
        "awc_r",
        "wtenthbar_r",
        "wthirdbar_r",
        "wfifteenbar_r",
        "wsatiated_r",
        "cec7_r",
        "ecec_r",
        "sumbases_r",
        "ph1to1h2o_r",
        "ec_r",
    ),
    "chfrags": ("chkey", "fragvol_r"),
    "corestrictions": ("cokey", "reskind", "resdept_r"),
    "cointerp": ("cokey", "mrulename", "rulename", "ruledepth", "interphr"),
    "chtexturegrp": ("chkey", "chtgkey", "texture", "texdesc", "rvindicator"),
}


@dataclass(frozen=True)
class RasterProfile:
    filename: str
    width: int
    height: int
    epsg: int | None
    transform: tuple[float, float, float, float, float, float]
    dtype: str
    nodata: float | None


def read_profile(path: Path) -> RasterProfile:
    import rasterio

    with rasterio.open(path) as src:
        t = src.transform
        return RasterProfile(
            filename=path.name,
            width=src.width,
            height=src.height,
            epsg=src.crs.to_epsg() if src.crs else None,
            transform=(t.a, t.b, t.c, t.d, t.e, t.f),
            dtype=src.dtypes[0],
            nodata=src.nodata,
        )


def verify_region_grid(release: Release, region: config.RegionSpec) -> list[str]:
    """Errors (empty = pass) comparing the MURASTER to its RegionSpec and the
    SARASTER to the MURASTER grid. MURASTER is the output-grid authority."""
    errors: list[str] = []
    mu = read_profile(release.muraster_path(region))
    tol = region.pixel_size * TRANSFORM_TOL_FRACTION

    if mu.epsg != region.epsg:
        errors.append(f"{region.name}: MURASTER EPSG {mu.epsg} != spec {region.epsg}")
    if (mu.width, mu.height) != (region.width, region.height):
        errors.append(f"{region.name}: MURASTER shape {mu.height}x{mu.width} != spec {region.height}x{region.width}")
    expected = (region.pixel_size, 0.0, region.x_min, 0.0, -region.pixel_size, region.y_max)
    for got, want, label in zip(mu.transform, expected, ["a", "b", "c", "d", "e", "f"], strict=True):
        if not math.isclose(got, want, abs_tol=tol):
            errors.append(f"{region.name}: MURASTER transform {label}={got} != spec {want}")
    if mu.dtype != "uint32":
        errors.append(f"{region.name}: MURASTER dtype {mu.dtype} != uint32")

    sa_path = release.saraster_path(region)
    if sa_path.exists():
        sa = read_profile(sa_path)
        if (sa.width, sa.height) != (mu.width, mu.height) or sa.epsg != mu.epsg:
            errors.append(f"{region.name}: SARASTER grid/CRS does not match MURASTER")
        else:
            for got, want in zip(sa.transform, mu.transform, strict=True):
                if not math.isclose(got, want, abs_tol=tol):
                    errors.append(f"{region.name}: SARASTER transform differs from MURASTER beyond tolerance")
                    break
    else:
        errors.append(f"{region.name}: SARASTER {sa_path.name} missing")
    return errors


def sha256_file(path: Path, chunk_bytes: int = 64 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()


ARTIFACT_TYPES = {
    ".gpkg": "geopackage",
    ".tif": "raster",
    ".7z": "archive",
    ".dbf": "vat",
    ".xml": "metadata",
    ".ovr": "overview",
    ".tfw": "worldfile",
    ".cpg": "codepage",
}


def inventory_artifacts(release: Release, *, checksums: bool = True) -> pa.Table:
    """One row per release artifact (path, type, size, sha256)."""
    paths = sorted(p for p in release.directory.rglob("*") if p.is_file())
    if release.archive and release.archive not in paths:
        paths.append(release.archive)
    rows = {
        "relative_path": [],
        "artifact_type": [],
        "size_bytes": [],
        "sha256": [],
        "source_archive": [],
        "release_date": [],
    }
    for path in tqdm(paths, desc="inventorying artifacts", unit="file"):
        rows["relative_path"].append(str(path.relative_to(release.directory.parent)))
        suffix = path.suffix.lower()
        rows["artifact_type"].append(ARTIFACT_TYPES.get(suffix, suffix.lstrip(".") or "other"))
        rows["size_bytes"].append(path.stat().st_size)
        rows["sha256"].append(sha256_file(path) if checksums else None)
        rows["source_archive"].append(release.archive.name if release.archive else None)
        rows["release_date"].append(release.release_date)
    return pa.table(rows)


def check_geopackage_schema(release: Release) -> dict:
    """Verify every required table/column exists; return a schema report dict."""
    report: dict = {"geopackage": release.geopackage.name, "tables": {}, "missing": []}
    uri = f"file:{release.geopackage}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        for table, columns in REQUIRED_COLUMNS.items():
            try:
                present = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            except sqlite3.Error as exc:
                report["missing"].append(f"{table}: {exc}")
                continue
            if not present:
                report["missing"].append(f"table {table} not found")
                continue
            missing_cols = [c for c in columns if c not in present]
            if missing_cols:
                report["missing"].append(f"{table}: missing columns {missing_cols}")
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            report["tables"][table] = {"rows": count, "required_columns_present": not missing_cols}
    report["passed"] = not report["missing"]
    return report


def inspect_release(release: Release, work_dir: Path, *, checksums: bool = True) -> dict:
    """Run the full phase-1 inspection; write source_manifest.parquet and
    schema_report.json under ``work_dir/{release_date}``; return the report."""
    out_dir = Path(work_dir) / release.release_date
    out_dir.mkdir(parents=True, exist_ok=True)

    grid_errors: list[str] = []
    for region in config.REGIONS.values():
        grid_errors.extend(verify_region_grid(release, region))

    schema_report = check_geopackage_schema(release)

    manifest = inventory_artifacts(release, checksums=checksums)
    manifest_path = out_dir / "source_manifest.parquet"
    tmp = manifest_path.with_suffix(".parquet.tmp")
    pq.write_table(manifest, tmp)
    tmp.rename(manifest_path)

    report = {
        "release_date": release.release_date,
        "release_date_source": release.release_date_source,
        "warnings": list(release.warnings),
        "geopackage": release.geopackage.name,
        "archive": release.archive.name if release.archive else None,
        "artifact_count": manifest.num_rows,
        "grid_errors": grid_errors,
        "schema": schema_report,
        "passed": not grid_errors and schema_report["passed"],
    }
    report_path = out_dir / "schema_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    log.info(
        "inspection %s: %d artifacts, %d grid errors, schema %s",
        release.release_date,
        manifest.num_rows,
        len(grid_errors),
        "OK" if schema_report["passed"] else "FAILED",
    )
    return report
