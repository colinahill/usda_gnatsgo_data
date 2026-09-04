"""Post-rasterization validation: structure, sampled pixel equality, domains.

The money check is sampled pixel equality: random windows of the store's mukey
array are compared byte-for-byte against the MURASTER, and sampled scientific
pixels are compared against the derived intermediate row for their mukey - one
shot catches placement, orientation, lookup, and encoding bugs.

Sampling is random but deliberately biased toward mapped ground. Most regions
are mostly background inside their bounding box (palau is 0.2% nonzero, CONUS
58%), so uniform draws spend the whole budget on ocean and "pass" without
comparing a single map unit. Candidate windows that hold no mapped pixel are
rejected and redrawn, and the scientific pixels are drawn from the mapped
positions those windows already read - which also means the value phase needs
no further MURASTER reads at all. A check that ends up comparing nothing is
reported as a failure, never a pass: a green tick that examined no data is
worse than a red one.

Results are streamed as they are produced (this is a generator) because a
region as large as CONUS spends minutes in the sampled phases and a caller
that logged only at the end would look hung. The scientific samples are point
reads scattered across the grid, so each one is a fresh round trip to the
store: they are issued through a thread pool, which is what makes a remote
store tractable at all (the reads are latency-bound, not CPU-bound).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
VALUE_WORKERS = 16  # concurrent point reads; these are latency-bound
PROGRESS_EVERY = 500  # point reads between progress lines
WINDOW_TRIES_PER_SAMPLE = 64  # rejection budget per window a sparse region needs
MAX_WINDOW_TRIES = 2048  # hard cap so an all-background region terminates


@dataclass(frozen=True)
class ValidationResult:
    check: str
    passed: bool
    message: str


def _result(check: str, passed: bool, message: str) -> ValidationResult:
    return ValidationResult(check, bool(passed), message)


def _undersampled(what: str, detail: str) -> ValidationResult:
    """A check that compared nothing. Failed on purpose, and worded so it is
    never mistaken for a store mismatch - the store may well be fine; what we
    know is that this run did not look at it."""
    return _result(
        what, False, f"insufficient sampling: {detail}; raise --samples/--window (the store was not checked)"
    )


def validate_region(
    repo: Repository,
    region_name: str,
    *,
    muraster_path: Path | None = None,
    derived_dir: Path | None = None,
    variables: list[config.VariableSpec] | None = None,
    samples: int = 8,
    window: int = SAMPLE_WINDOW,
    value_samples: int | None = None,
    workers: int = VALUE_WORKERS,
    seed: int | None = None,
) -> Iterator[ValidationResult]:
    """All checks for one region, yielded as they complete; source/intermediate
    comparisons run when the MURASTER / derived dir are available locally.

    `samples` random `window`-px squares *containing mapped pixels* are compared
    against the MURASTER (all-background candidates are redrawn), and
    `value_samples` mapped pixels from those windows are compared against the
    derived tables using `workers` concurrent point reads.
    """
    region = config.REGIONS[region_name]
    variables = variables if variables is not None else config.included_variables()
    value_samples = VALUE_SAMPLES if value_samples is None else value_samples
    session = repo.readonly_session("main")
    rng = np.random.default_rng(seed)

    # --- structure ---
    log.info("%s: structure checks for %d variables", region_name, len(variables))
    expected_2d = (region.height, region.width)
    for spec in variables:
        path = f"{region_name}/{spec.array_path}"
        try:
            arr = zarr.open_array(session.store, path=path, mode="r")
        except (KeyError, FileNotFoundError):
            yield _result(f"array:{spec.name}", False, f"{path} missing")
            continue
        expected = (len(config.DEPTH_LABELS), *expected_2d) if spec.dims == config.DIMS_3D else expected_2d
        ok = arr.shape == expected and str(arr.dtype) == spec.dtype
        yield _result(f"array:{spec.name}", ok, f"shape {arr.shape} dtype {arr.dtype}")
        attrs = dict(arr.attrs)
        missing_attrs = [
            k
            for k in ("long_name", "units", "aggregation_algorithm_id", "grid_mapping", "missing_value_semantics")
            if k not in attrs
        ]
        yield _result(f"attrs:{spec.name}", not missing_attrs, f"missing {missing_attrs or 'none'}")

    for group in ("soil_properties", "map_unit_properties"):
        x = zarr.open_array(session.store, path=f"{region_name}/{group}/x", mode="r")
        expected_x0 = region.x_min + region.pixel_size / 2
        yield _result(
            f"coords:{group}",
            x.shape == (region.width,)
            and math.isclose(float(x[0]), expected_x0, rel_tol=0, abs_tol=region.pixel_size * 1e-6),
            f"x[0]={float(x[0])} vs {expected_x0}",
        )
    x_soil = zarr.open_array(session.store, path=f"{region_name}/soil_properties/x", mode="r")[:]
    x_map = zarr.open_array(session.store, path=f"{region_name}/map_unit_properties/x", mode="r")[:]
    yield _result("coords:identical", bool(np.array_equal(x_soil, x_map)), "soil/map_unit x coords equality")

    if muraster_path is None or not Path(muraster_path).exists():
        yield _result("mukey_equality", True, "skipped (MURASTER not on disk)")
        return

    # --- sampled mukey equality against the source raster ---
    h = min(window, region.height)
    w = min(window, region.width)
    max_tries = min(max(samples, 1) * WINDOW_TRIES_PER_SAMPLE, MAX_WINDOW_TRIES)
    log.info(
        "%s: seeking %d random %dx%d windows with mapped pixels (up to %d draws)", region_name, samples, h, w, max_tries
    )
    mukey = zarr.open_array(session.store, path=f"{region_name}/map_unit_properties/mukey", mode="r")
    mismatches = checked = nonzero = accepted = rejected = 0
    pool_rows: list[np.ndarray] = []
    pool_cols: list[np.ndarray] = []
    pool_keys: list[np.ndarray] = []
    with rasterio.open(muraster_path) as src:
        for _ in range(max_tries):
            if accepted >= samples:
                break
            r0 = int(rng.integers(0, region.height - h + 1))
            c0 = int(rng.integers(0, region.width - w + 1))
            # probe the MURASTER first: it is local and tiled (~10 ms), while the
            # store window is a remote round trip, so a rejected candidate costs
            # nothing but a disk read
            src_block = src.read(1, window=rasterio.windows.Window(c0, r0, w, h))
            mapped = np.flatnonzero(src_block)
            if mapped.size == 0:
                rejected += 1
                continue
            accepted += 1
            store_block = mukey[r0 : r0 + h, c0 : c0 + w]
            mismatches += int((src_block != store_block).sum())
            checked += src_block.size
            nonzero += int(mapped.size)
            # thin each window down to the whole pixel budget before keeping it:
            # a dense 512x512 window holds 262k mapped pixels and 8 of them would
            # otherwise accumulate millions of coordinates to serve 200 draws
            if mapped.size > value_samples:
                mapped = rng.choice(mapped, value_samples, replace=False)
            rows, cols = np.unravel_index(mapped, src_block.shape)
            pool_rows.append(rows + r0)
            pool_cols.append(cols + c0)
            # the source key, not the store's: the value check must not take its
            # mukey from the same array family it is validating
            pool_keys.append(src_block[rows, cols])

    if nonzero == 0:
        # state what was drawn, not why it came up empty: `--samples 0` and a
        # genuinely nodata region reach here alike, and guessing between them
        # in the message is how a reader gets misled
        drawn = f"{rejected} random {h}x{w} draws (limit {max_tries})" if rejected else f"--samples {samples}"
        yield _undersampled("mukey_equality", f"no mapped pixels found in {drawn}")
    else:
        yield _result(
            "mukey_equality",
            mismatches == 0,
            f"{mismatches} mismatched of {checked:,} sampled px ({nonzero:,} nonzero) in {accepted} windows"
            + (f", {rejected} all-background candidates redrawn" if rejected else ""),
        )

    # --- sampled scientific values against the intermediates ---
    if derived_dir is None or not Path(derived_dir).exists():
        yield _result("value_equality", True, "skipped (derived intermediates not on disk)")
        return

    value_specs = [spec for spec in variables if spec.algorithm_id != "direct_raster"]
    pool_size = sum(part.size for part in pool_rows)
    if pool_size == 0 or not value_specs:
        yield _undersampled(
            "value_equality",
            f"no mapped pixels to compare ({pool_size} candidates from {accepted} windows, "
            f"{len(value_specs)} variables)",
        )
        return

    rows = np.concatenate(pool_rows)
    cols = np.concatenate(pool_cols)
    keys = np.concatenate(pool_keys)
    pixel_count = min(value_samples, rows.size)
    picked = rng.choice(rows.size, pixel_count, replace=False)
    arrays = {
        spec.name: zarr.open_array(session.store, path=f"{region_name}/{spec.array_path}", mode="r")
        for spec in value_specs
    }

    # Draw every (pixel, variable, depth) up front so the RNG stream stays
    # identical to a serial run, then read them concurrently. Every pixel is
    # already known to be mapped and its mukey already read, so this phase
    # touches the MURASTER not at all.
    tasks: list[tuple[int, int, int, config.VariableSpec, int | None]] = []
    for i in picked:
        r, c, key = int(rows[i]), int(cols[i]), int(keys[i])
        for spec in value_specs:
            depth_index = int(rng.integers(0, len(config.DEPTH_LABELS))) if spec.dims == config.DIMS_3D else None
            tasks.append((r, c, key, spec, depth_index))
    log.info(
        "%s: %d point reads (%d mapped pixels of %d found in %d windows x %d variables), %d workers",
        region_name,
        len(tasks),
        pixel_count,
        rows.size,
        accepted,
        len(value_specs),
        workers,
    )

    def read(task: tuple[int, int, int, config.VariableSpec, int | None]):
        r, c, _key, spec, depth_index = task
        arr = arrays[spec.name]
        return arr[r, c] if depth_index is None else arr[depth_index, r, c]

    stored_values: list = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in range(0, len(tasks), PROGRESS_EVERY):
            stored_values.extend(pool.map(read, tasks[start : start + PROGRESS_EVERY]))
            log.info("%s: %d/%d point reads", region_name, len(stored_values), len(tasks))

    soil = pl.read_parquet(Path(derived_dir) / "soil_properties.parquet")
    map_unit = pl.read_parquet(Path(derived_dir) / "map_unit_properties.parquet")
    value_mismatches: list[str] = []
    for (r, c, key, spec, depth_index), stored in zip(tasks, stored_values, strict=True):
        if depth_index is None:
            row = map_unit.filter(pl.col("mukey") == key)
        else:
            label = config.DEPTH_LABELS[depth_index]
            row = soil.filter((pl.col("mukey") == key) & (pl.col(config.DEPTH_DIM) == label))
        expected = row[spec.name][0] if row.height and spec.name in row.columns else None
        if not _values_match(stored, expected, spec):
            value_mismatches.append(f"{spec.name}@({r},{c}) mukey {key}: store {stored} vs table {expected}")
    yield _result(
        "value_equality",
        not value_mismatches,
        f"{len(value_mismatches)} mismatches of {len(tasks)} sampled values"
        + (f"; e.g. {value_mismatches[:3]}" if value_mismatches else ""),
    )


def _values_match(stored, expected, spec: config.VariableSpec) -> bool:
    stored = float(stored)
    if np.issubdtype(np.dtype(spec.dtype), np.floating):
        if expected is None or (isinstance(expected, float) and math.isnan(expected)):
            return math.isnan(stored)
        return math.isnan(stored) is False and math.isclose(stored, float(expected), rel_tol=1e-6, abs_tol=1e-5)
    if expected is None or (isinstance(expected, float) and math.isnan(expected)):
        return stored == spec.fill_value
    return stored == round(float(expected))
