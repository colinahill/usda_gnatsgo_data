"""Post-rasterization validation: structure, sampled pixel equality, domains.

The money check is sampled pixel equality: random windows of the store's mukey
array are compared byte-for-byte against the MURASTER, and sampled scientific
pixels are compared against the derived intermediate row for their mukey - one
shot catches placement, orientation, lookup, and encoding bugs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import rasterio
import rasterio.windows
import zarr
from icechunk import Repository

from . import config

log = logging.getLogger(__name__)

SAMPLE_WINDOW = 512  # px per random mukey-equality sample
VALUE_SAMPLES = 200  # scientific pixels compared against the intermediates


@dataclass(frozen=True)
class ValidationResult:
    check: str
    passed: bool
    message: str


def _result_list_add(results: list[ValidationResult], check: str, passed: bool, message: str) -> None:
    results.append(ValidationResult(check, bool(passed), message))


def validate_region(
    repo: Repository,
    region_name: str,
    *,
    muraster_path: Path | None = None,
    derived_dir: Path | None = None,
    variables: list[config.VariableSpec] | None = None,
    samples: int = 8,
    seed: int | None = None,
) -> list[ValidationResult]:
    """All checks for one region; source/intermediate comparisons run when the
    MURASTER / derived dir are available locally."""
    region = config.REGIONS[region_name]
    variables = variables if variables is not None else config.included_variables()
    session = repo.readonly_session("main")
    results: list[ValidationResult] = []
    rng = np.random.default_rng(seed)

    # --- structure ---
    expected_2d = (region.height, region.width)
    for spec in variables:
        path = f"{region_name}/{spec.array_path}"
        try:
            arr = zarr.open_array(session.store, path=path, mode="r")
        except (KeyError, FileNotFoundError):
            _result_list_add(results, f"array:{spec.name}", False, f"{path} missing")
            continue
        expected = (len(config.DEPTH_LABELS), *expected_2d) if spec.dims == config.DIMS_3D else expected_2d
        ok = arr.shape == expected and str(arr.dtype) == spec.dtype
        _result_list_add(results, f"array:{spec.name}", ok, f"shape {arr.shape} dtype {arr.dtype}")
        attrs = dict(arr.attrs)
        missing_attrs = [
            k
            for k in ("long_name", "units", "aggregation_algorithm_id", "grid_mapping", "missing_value_semantics")
            if k not in attrs
        ]
        _result_list_add(results, f"attrs:{spec.name}", not missing_attrs, f"missing {missing_attrs or 'none'}")

    for group in ("soil_properties", "map_unit_properties"):
        x = zarr.open_array(session.store, path=f"{region_name}/{group}/x", mode="r")
        expected_x0 = region.x_min + region.pixel_size / 2
        _result_list_add(
            results,
            f"coords:{group}",
            x.shape == (region.width,)
            and math.isclose(float(x[0]), expected_x0, rel_tol=0, abs_tol=region.pixel_size * 1e-6),
            f"x[0]={float(x[0])} vs {expected_x0}",
        )
    x_soil = zarr.open_array(session.store, path=f"{region_name}/soil_properties/x", mode="r")[:]
    x_map = zarr.open_array(session.store, path=f"{region_name}/map_unit_properties/x", mode="r")[:]
    _result_list_add(
        results, "coords:identical", bool(np.array_equal(x_soil, x_map)), "soil/map_unit x coords equality"
    )

    if muraster_path is None or not Path(muraster_path).exists():
        _result_list_add(results, "mukey_equality", True, "skipped (MURASTER not on disk)")
        return results

    # --- sampled mukey equality against the source raster ---
    mukey = zarr.open_array(session.store, path=f"{region_name}/map_unit_properties/mukey", mode="r")
    mismatches = checked = nonzero = 0
    sampled_keys: list[np.ndarray] = []
    with rasterio.open(muraster_path) as src:
        for _ in range(samples):
            h = min(SAMPLE_WINDOW, region.height)
            w = min(SAMPLE_WINDOW, region.width)
            r0 = int(rng.integers(0, region.height - h + 1))
            c0 = int(rng.integers(0, region.width - w + 1))
            src_block = src.read(1, window=rasterio.windows.Window(c0, r0, w, h))
            store_block = mukey[r0 : r0 + h, c0 : c0 + w]
            mismatches += int((src_block != store_block).sum())
            checked += src_block.size
            nonzero += int((src_block != 0).sum())
            sampled_keys.append(store_block[store_block != 0])
    _result_list_add(
        results,
        "mukey_equality",
        mismatches == 0,
        f"{mismatches} mismatched of {checked:,} sampled px ({nonzero:,} nonzero) in {samples} windows",
    )

    # --- sampled scientific values against the intermediates ---
    if derived_dir is None or not Path(derived_dir).exists():
        _result_list_add(results, "value_equality", True, "skipped (derived intermediates not on disk)")
        return results
    keys = np.concatenate(sampled_keys) if sampled_keys else np.array([], dtype="uint32")
    if keys.size == 0:
        _result_list_add(results, "value_equality", True, "skipped (sampled windows were all background)")
        return results

    soil = pl.read_parquet(Path(derived_dir) / "soil_properties.parquet")
    map_unit = pl.read_parquet(Path(derived_dir) / "map_unit_properties.parquet")
    value_mismatches: list[str] = []
    checked_values = 0

    # sample pixels with known mukeys and re-read single store pixels
    pixel_count = min(VALUE_SAMPLES, keys.size)
    arrays = {
        spec.name: zarr.open_array(session.store, path=f"{region_name}/{spec.array_path}", mode="r")
        for spec in variables
        if spec.algorithm_id != "direct_raster"
    }
    with rasterio.open(muraster_path) as src:
        for _ in range(pixel_count):
            r = int(rng.integers(0, region.height))
            c = int(rng.integers(0, region.width))
            key = int(src.read(1, window=rasterio.windows.Window(c, r, 1, 1))[0, 0])
            if key == 0:
                continue
            for spec in variables:
                if spec.algorithm_id == "direct_raster":
                    continue
                arr = arrays[spec.name]
                if spec.dims == config.DIMS_3D:
                    depth_index = int(rng.integers(0, len(config.DEPTH_LABELS)))
                    label = config.DEPTH_LABELS[depth_index]
                    stored = arr[depth_index, r, c]
                    row = soil.filter((pl.col("mukey") == key) & (pl.col(config.DEPTH_DIM) == label))
                else:
                    stored = arr[r, c]
                    row = map_unit.filter(pl.col("mukey") == key)
                expected = row[spec.name][0] if row.height and spec.name in row.columns else None
                checked_values += 1
                if not _values_match(stored, expected, spec):
                    value_mismatches.append(f"{spec.name}@({r},{c}) mukey {key}: store {stored} vs table {expected}")
    _result_list_add(
        results,
        "value_equality",
        not value_mismatches,
        f"{len(value_mismatches)} mismatches of {checked_values} sampled values"
        + (f"; e.g. {value_mismatches[:3]}" if value_mismatches else ""),
    )
    return results


def _values_match(stored, expected, spec: config.VariableSpec) -> bool:
    stored = float(stored)
    if np.issubdtype(np.dtype(spec.dtype), np.floating):
        if expected is None or (isinstance(expected, float) and math.isnan(expected)):
            return math.isnan(stored)
        return math.isnan(stored) is False and math.isclose(stored, float(expected), rel_tol=1e-6, abs_tol=1e-5)
    if expected is None or (isinstance(expected, float) and math.isnan(expected)):
        return stored == spec.fill_value
    return stored == round(float(expected))
