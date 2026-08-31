"""Fill one (region, variable) pair: windowed MURASTER reads -> zarr writes.

Design notes:
- Work is split into windows aligned to the zarr shard grid (the storage-object
  unit), so concurrent writers never co-write one object.
- The mukey window is read once per window; all-background windows are skipped
  and all-fill inner chunks are omitted by the sharding codec, so background
  costs nothing.
- Values come from the derived Parquet via sorted-mukey searchsorted lookups;
  unmatched nonzero keys are collected and reported, never silently filled.
- Each window is retried with exponential backoff to absorb transient object-
  store errors, then fails the variable (fail fast; checkpoints make a retry
  cheap). ``retry_transient`` is the one retry policy here, shared with the
  caller's checkpoint commits: S3-compatible gateways return responses the AWS
  SDK cannot classify (empty-bodied errors, HTTP/2 stream resets), and those get
  no retry from the SDK or from icechunk, so every store call needs one.
- Expired credentials are the one failure never retried: they would fail every
  remaining window too, so the run stops at its last checkpoint with a message
  saying to re-login, rather than logging thousands of doomed attempts.
- Windows run through a thread pool in batches of ``commit_every``. After each
  batch the caller's ``checkpoint`` callback persists the batch and hands back a
  fresh session, so an interrupted backfill keeps every shard it uploaded. The
  commit policy itself (commit vs amend) stays with the caller.
- Resume is driven by ``windows_done``, a count of leading windows completed,
  which stays meaningful when ``commit_every`` changes between runs.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl
import rasterio
import rasterio.windows
import zarr
from icechunk import Session, StorageError

from . import config, lookup, store

log = logging.getLogger(__name__)

TRANSFORM_TOL_FRACTION = 1e-6
TRANSIENT_ATTEMPTS = 5
RETRY_BASE_SECONDS = 2.0
# Unmatched keys are tracked in full in memory but only this many are carried in
# the resume record, so the region group attrs stay small. Past the cap a
# resumed run reports unmatched_truncated and its count is a lower bound.
UNMATCHED_CARRY_LIMIT = 1000

PROGRESS_ATTR = "rasterize_progress"

# Commits/amends the work so far and returns a fresh writable session.
Checkpoint = Callable[[dict], Session]


class CredentialsExpired(RuntimeError):
    """Store credentials went bad mid-run; every later call would fail too.

    Raised instead of retrying so the run stops at the last checkpoint rather
    than burning the remaining windows on doomed attempts.
    """

    def __init__(self, during: str, cause: BaseException):
        self.during = during
        super().__init__(f"store credentials are no longer valid (during {during}): {cause}")


def retry_transient[T](
    operation: Callable[[], T],
    describe: str,
    *,
    retry_on: type[BaseException] | tuple[type[BaseException], ...] = StorageError,
    attempts: int = TRANSIENT_ATTEMPTS,
) -> T:
    """Run ``operation``, retrying ``retry_on`` failures with exponential backoff.

    ``operation`` must be safe to repeat: a window write overwrites whole shard
    objects, and a failed commit publishes nothing (the branch ref moves last),
    so both are idempotent.
    """
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except retry_on as exc:
            # expired credentials would fail every remaining attempt and every
            # remaining window; stop now so the operator sees one clear message
            if store.is_credentials_failure(exc):
                raise CredentialsExpired(describe, exc) from exc
            if attempt == attempts:
                raise RuntimeError(f"{describe} failed after {attempts} attempts: {exc}") from exc
            delay = RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            log.warning("%s: attempt %d failed (%s); retrying in %.0fs", describe, attempt, exc, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def verify_grid(src: rasterio.DatasetReader, region: config.RegionSpec) -> None:
    """The MURASTER must match the RegionSpec exactly (no placement here)."""
    if (src.width, src.height) != (region.width, region.height):
        raise ValueError(
            f"{src.name}: shape {src.height}x{src.width} != configured {region.height}x{region.width} "
            f"for {region.name}; the RegionSpec in config.py no longer matches the source"
        )
    if src.crs is None or src.crs.to_epsg() != region.epsg:
        raise ValueError(f"{src.name}: CRS {src.crs} != configured EPSG:{region.epsg}")
    t = src.transform
    tol = region.pixel_size * TRANSFORM_TOL_FRACTION
    expected = (region.pixel_size, 0.0, region.x_min, 0.0, -region.pixel_size, region.y_max)
    for got, want in zip((t.a, t.b, t.c, t.d, t.e, t.f), expected, strict=True):
        if not math.isclose(got, want, abs_tol=tol):
            raise ValueError(f"{src.name}: transform {t!r} != configured grid for {region.name}")


def verify_array_encoding(target: zarr.Array, spec: config.VariableSpec) -> None:
    """The array's shards must be the ones windows are aligned to.

    Windows are the storage-object unit precisely because they match the shard
    grid. Against an array sharded differently (a store created under an older
    EncodingSpec) two windows would co-write one object, so refuse instead.
    """
    expected = config.ENCODING.shards(len(spec.dims))
    if tuple(target.shards or ()) != expected:
        raise ValueError(
            f"{target.path}: array shards {target.shards} != configured {expected}; this store was created "
            "under a different EncodingSpec. Re-init a store at a new DATASET_VERSION path (or delete this "
            "one) rather than writing shard-aligned windows into a different shard grid"
        )


def shard_aligned_windows(region: config.RegionSpec) -> list[tuple[int, int, int, int]]:
    """(gy0, gy1, gx0, gx1) windows aligned to the shard grid, covering the region."""
    enc = config.ENCODING
    windows = []
    gy = 0
    while gy < region.height:
        gy_next = min(gy + enc.shard_y, region.height)
        gx = 0
        while gx < region.width:
            gx_next = min(gx + enc.shard_x, region.width)
            windows.append((gy, gy_next, gx, gx_next))
            gx = gx_next
        gy = gy_next
    return windows


def _load_lookups(spec: config.VariableSpec, derived_dir: Path) -> dict[str | None, lookup.Lookup]:
    """{depth label (or None for 2-D): Lookup} for one variable."""
    if spec.dims == config.DIMS_3D:
        table = pl.read_parquet(derived_dir / "soil_properties.parquet", columns=["mukey", config.DEPTH_DIM, spec.name])
        return {label: lookup.build_lookups(table, spec, depth_label=label) for label in config.DEPTH_LABELS}
    table = pl.read_parquet(derived_dir / "map_unit_properties.parquet", columns=["mukey", spec.name])
    return {None: lookup.build_lookups(table, spec)}


def rasterize_variable(
    session: Session,
    region: config.RegionSpec,
    spec: config.VariableSpec,
    muraster_path: Path,
    derived_dir: Path | None,
    *,
    workers: int = 8,
    commit_every: int = 0,
    checkpoint: Checkpoint | None = None,
    resume: dict | None = None,
) -> dict:
    """Write one variable for one region and return its stats.

    Commits nothing itself. With ``commit_every`` > 0 and a ``checkpoint``
    callback, every batch of that many windows is handed to the callback to be
    persisted; the callback returns the session the next batch writes through.
    ``resume`` is a previous stats/progress record: its leading ``windows_done``
    windows are skipped and its counters are carried into the returned stats.
    """
    array_path = f"{region.name}/{spec.array_path}"
    with rasterio.open(muraster_path) as src:
        verify_grid(src, region)

    is_mukey = spec.algorithm_id == "direct_raster"
    lookups: dict[str | None, lookup.Lookup] = {}
    if not is_mukey:
        if derived_dir is None:
            raise ValueError(f"{spec.name} needs the derived intermediates directory")
        lookups = _load_lookups(spec, Path(derived_dir))

    target = zarr.open_array(session.store, path=array_path, mode="r+")
    verify_array_encoding(target, spec)
    windows = shard_aligned_windows(region)
    done = resume_offset(resume, region)
    local = threading.local()
    stats_lock = threading.Lock()
    written = int(resume.get("windows_written", 0)) if done else 0
    skipped = int(resume.get("windows_skipped_all_background", 0)) if done else 0
    unmatched: set[int] = {int(k) for k in resume.get("unmatched_mukeys", ())} if done else set()
    unmatched_truncated = bool(resume.get("unmatched_truncated")) if done else False

    def write_window(window: tuple[int, int, int, int]) -> None:
        nonlocal written, skipped
        gy0, gy1, gx0, gx1 = window
        src_handle = getattr(local, "src", None)
        if src_handle is None:  # rasterio handles aren't thread-safe
            src_handle = local.src = rasterio.open(muraster_path)
        block = src_handle.read(1, window=rasterio.windows.Window(gx0, gy0, gx1 - gx0, gy1 - gy0))
        if not block.any():  # all background -> leave chunks unwritten
            with stats_lock:
                skipped += 1
            return
        if is_mukey:
            target[gy0:gy1, gx0:gx1] = block
        else:
            window_unmatched: set[int] = set()
            for depth_index, label in enumerate(config.DEPTH_LABELS) if spec.dims == config.DIMS_3D else [(None, None)]:
                values, missing = lookups[label].expand(block)
                if depth_index is None:
                    target[gy0:gy1, gx0:gx1] = values
                else:
                    target[depth_index, gy0:gy1, gx0:gx1] = values
                window_unmatched.update(int(k) for k in missing)
            if window_unmatched:
                with stats_lock:
                    unmatched.update(window_unmatched)
        with stats_lock:
            written += 1

    def process(window: tuple[int, int, int, int]) -> None:
        gy0, gy1, gx0, gx1 = window
        # broader than the commit path on purpose: a window also covers rasterio
        # reads and lookup expansion, and a retry there is cheap
        retry_transient(
            lambda: write_window(window),
            f"{region.name}/{spec.name} window ({gy0},{gx0})-({gy1},{gx1})",
            retry_on=Exception,
        )

    def current_stats() -> dict:
        keys = sorted(unmatched)
        return {
            "region": region.name,
            "variable": spec.name,
            "windows_done": done,
            "window_count": len(windows),
            "shard_shape": [config.ENCODING.shard_y, config.ENCODING.shard_x],
            "windows_written": written,
            "windows_skipped_all_background": skipped,
            "unmatched_mukey_count": len(keys),
            "unmatched_mukeys": keys[:UNMATCHED_CARRY_LIMIT],
            "unmatched_truncated": unmatched_truncated or len(keys) > UNMATCHED_CARRY_LIMIT,
        }

    pending = windows[done:]
    batch_size = commit_every if commit_every > 0 else len(pending)
    log.info(
        "%s/%s: %d of %d windows to write, %d workers, checkpoint every %d",
        region.name,
        spec.name,
        len(pending),
        len(windows),
        workers,
        batch_size,
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            # list() blocks until the batch is done and propagates the first
            # exception from any worker
            list(pool.map(process, batch))
            done += len(batch)
            if checkpoint is not None:
                session = checkpoint(current_stats())
                target = zarr.open_array(session.store, path=array_path, mode="r+")
                log.info("%s/%s: %d/%d windows persisted", region.name, spec.name, done, len(windows))

    if unmatched:
        log.warning(
            "%s/%s: %d raster mukeys not in the derived table (left at fill), e.g. %s",
            region.name,
            spec.name,
            len(unmatched),
            sorted(unmatched)[:20],
        )
    log.info("%s/%s: %d windows written, %d skipped", region.name, spec.name, written, skipped)
    return current_stats()


def rasterized_variables(session_or_store, region_name: str) -> dict:
    """The region's per-variable provenance/resume record."""
    zarr_store = getattr(session_or_store, "store", session_or_store)
    try:
        group = zarr.open_group(zarr_store, path=region_name, mode="r")
    except (KeyError, FileNotFoundError):
        return {}
    return dict(group.attrs.get("rasterized_variables", {}))


def record_variable_provenance(session: Session, region_name: str, variable: str, provenance: dict) -> None:
    """Merge one variable's provenance into the region group attrs (the resume guard)."""
    group = zarr.open_group(session.store, path=region_name, mode="r+")
    record = dict(group.attrs.get("rasterized_variables", {}))
    record[variable] = provenance
    group.attrs["rasterized_variables"] = record


def rasterize_progress(session_or_store, region_name: str) -> dict:
    """{variable: stats} for variables mid-flight in this region.

    An entry exists only between the first checkpoint and completion: finishing
    a variable moves it to ``rasterized_variables`` and clears this record.
    """
    zarr_store = getattr(session_or_store, "store", session_or_store)
    try:
        group = zarr.open_group(zarr_store, path=region_name, mode="r")
    except (KeyError, FileNotFoundError):
        return {}
    return dict(group.attrs.get(PROGRESS_ATTR, {}))


def record_progress(session: Session, region_name: str, variable: str, stats: dict) -> None:
    """Write one variable's checkpoint record (what a resumed run reads)."""
    group = zarr.open_group(session.store, path=region_name, mode="r+")
    record = dict(group.attrs.get(PROGRESS_ATTR, {}))
    record[variable] = stats
    group.attrs[PROGRESS_ATTR] = record


def clear_progress(session: Session, region_name: str, variable: str) -> None:
    """Drop one variable's checkpoint record (it is complete, or being redone)."""
    group = zarr.open_group(session.store, path=region_name, mode="r+")
    record = dict(group.attrs.get(PROGRESS_ATTR, {}))
    if record.pop(variable, None) is None:
        return
    group.attrs[PROGRESS_ATTR] = record


def resume_offset(record: dict | None, region: config.RegionSpec) -> int:
    """How many leading windows a checkpoint record lets us skip.

    Returns 0 for anything we cannot trust: a record from a different shard grid
    (the window list itself would differ) or an out-of-range count. Re-doing the
    variable is always safe, so an unusable record is a warning, not an error.
    """
    if not record:
        return 0
    windows = len(shard_aligned_windows(region))
    enc = config.ENCODING
    done = int(record.get("windows_done", 0))
    stale = (
        list(record.get("shard_shape", [])) != [enc.shard_y, enc.shard_x]
        or int(record.get("window_count", -1)) != windows
        or not 0 <= done <= windows
    )
    if stale:
        log.warning(
            "%s/%s: ignoring a checkpoint record written for a different shard grid (%s, %s windows); "
            "the variable will be rewritten from the start",
            region.name,
            record.get("variable", "?"),
            record.get("shard_shape"),
            record.get("window_count"),
        )
        return 0
    return done
