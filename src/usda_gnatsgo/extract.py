"""Stream required GeoPackage columns to typed Parquet intermediates.

Reads happen through the stdlib sqlite3 driver in bounded batches (the
GeoPackage is a 56 GB SQLite file; whole-table loads are never attempted).
Each output is written atomically (tmp + rename) with a JSON sidecar recording
the source fingerprint and row counts, making Parquet the resumable boundary
between the database and the scientific calculations.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from .catalog import Release
from .source import REQUIRED_COLUMNS

log = logging.getLogger(__name__)

BATCH_ROWS = 500_000

# Numeric typing: SSURGO map-unit keys are numeric strings that must join
# against uint32 raster values, so mukey is cast to int64 (validated); other
# keys stay strings. Measurement columns are float64; ruledepth is int.
INT_COLUMNS = {"mukey", "ruledepth"}
FLOAT_SUFFIXES = ("_r", "_l", "_h")
FLOAT_COLUMNS = {"interphr", "slopegraddcp", "slopegradwta", "aws025wta", "aws050wta", "aws0100wta", "aws0150wta"}


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    where: str | None = None  # SQL filter pushed into the read
    unique_key: tuple[str, ...] | None = None  # asserted unique when set


TABLES: dict[str, TableSpec] = {
    "mapunit": TableSpec("mapunit", REQUIRED_COLUMNS["mapunit"], unique_key=("mukey",)),
    "muaggatt": TableSpec("muaggatt", REQUIRED_COLUMNS["muaggatt"], unique_key=("mukey",)),
    "component": TableSpec("component", REQUIRED_COLUMNS["component"], unique_key=("cokey",)),
    "chorizon": TableSpec("chorizon", REQUIRED_COLUMNS["chorizon"], unique_key=("chkey",)),
    "chfrags": TableSpec("chfrags", REQUIRED_COLUMNS["chfrags"]),
    "corestrictions": TableSpec("corestrictions", REQUIRED_COLUMNS["corestrictions"]),
    "cointerp": TableSpec(
        "cointerp",
        REQUIRED_COLUMNS["cointerp"],
        # Only the NCCPI v3 interpretation is consumed; filtering here keeps the
        # largest table (~hundreds of millions of rows) out of the intermediate.
        where="mrulename LIKE 'NCCPI%'",
    ),
    "chtexturegrp": TableSpec("chtexturegrp", REQUIRED_COLUMNS["chtexturegrp"]),
}


def _column_type(column: str) -> pa.DataType:
    if column in INT_COLUMNS:
        return pa.int64()
    if column in FLOAT_COLUMNS or column.endswith(FLOAT_SUFFIXES):
        return pa.float64()
    return pa.string()


def table_schema(spec: TableSpec) -> pa.Schema:
    return pa.schema([(c, _column_type(c)) for c in spec.columns])


def _coerce(value, dtype: pa.DataType):
    if value is None:
        return None
    if pa.types.is_int64(dtype):
        return int(value)
    if pa.types.is_float64(dtype):
        return float(value)
    return str(value)


def source_fingerprint(release: Release) -> dict:
    stat = release.geopackage.stat()
    return {"geopackage": release.geopackage.name, "size_bytes": stat.st_size, "mtime": int(stat.st_mtime)}


def extract_table(release: Release, spec: TableSpec, out_path: Path, *, batch_rows: int = BATCH_ROWS) -> dict:
    """Stream one table to Parquet; returns the sidecar metadata dict."""
    schema = table_schema(spec)
    columns_sql = ", ".join(f'"{c}"' for c in spec.columns)
    query = f'SELECT {columns_sql} FROM "{spec.name}"'
    count_query = f'SELECT COUNT(*) FROM "{spec.name}"'
    if spec.where:
        query += f" WHERE {spec.where}"
        count_query += f" WHERE {spec.where}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    rows_written = 0
    uri = f"file:{release.geopackage}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        total = conn.execute(count_query).fetchone()[0]
        cursor = conn.execute(query)
        with (
            pq.ParquetWriter(tmp, schema) as writer,
            tqdm(total=total, desc=f"extract {spec.name}", unit="row", unit_scale=True) as bar,
        ):
            while rows := cursor.fetchmany(batch_rows):
                arrays = [
                    pa.array((_coerce(row[i], schema.field(i).type) for row in rows), type=schema.field(i).type)
                    for i in range(len(spec.columns))
                ]
                writer.write_table(pa.table(arrays, schema=schema))
                rows_written += len(rows)
                bar.update(len(rows))

    if spec.unique_key:
        import duckdb

        dupes = duckdb.sql(
            f"SELECT COUNT(*) FROM (SELECT {', '.join(spec.unique_key)} FROM read_parquet('{tmp}') "
            f"GROUP BY {', '.join(spec.unique_key)} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if dupes:
            tmp.unlink()
            raise ValueError(f"{spec.name}: {dupes} duplicate {spec.unique_key} keys; extraction aborted")

    tmp.rename(out_path)
    sidecar = {
        "table": spec.name,
        "columns": list(spec.columns),
        "where": spec.where,
        "rows": rows_written,
        "source": source_fingerprint(release),
    }
    out_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))
    log.info("extracted %s: %s rows -> %s", spec.name, f"{rows_written:,}", out_path)
    return sidecar


def is_current(release: Release, out_path: Path) -> bool:
    """True when an existing extract matches the current source fingerprint."""
    sidecar_path = out_path.with_suffix(".json")
    if not (out_path.exists() and sidecar_path.exists()):
        return False
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except json.JSONDecodeError:
        return False
    return sidecar.get("source") == source_fingerprint(release)


def extract_all(release: Release, work_dir: Path, *, tables: list[str] | None = None, force: bool = False) -> Path:
    """Extract every required table under ``work_dir/{release}/extracted/``.

    Existing extracts with a matching source fingerprint are reused.
    """
    out_dir = Path(work_dir) / release.release_date / "extracted"
    for name in tables or list(TABLES):
        spec = TABLES[name]
        out_path = out_dir / f"{name}.parquet"
        if not force and is_current(release, out_path):
            log.info("reusing existing extract %s", out_path)
            continue
        extract_table(release, spec, out_path)
    return out_dir
