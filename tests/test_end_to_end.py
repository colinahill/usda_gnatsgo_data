"""Synthetic-fixture pipeline: intermediates -> init -> rasterize -> read back."""

from __future__ import annotations

import json

import icechunk
import numpy as np
import pytest
import rasterio
import typer
import xarray as xr
import zarr

from usda_gnatsgo import catalog, config, intermediates, lookup, rasterize, store, template, validate

from .conftest import TEST_REGION, UNMATCHED_MUKEY, write_synthetic_muraster

PIXELS = {100: (5, 5), 200: (5, 60), 300: (50, 5), 400: (50, 60)}  # (row, col) per mukey
BACKGROUND = (0, 0)
DEPTH_0_20 = config.DEPTH_LABELS.index("0_20")


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    """Run the full pipeline once for this module (module-scoped: config
    monkeypatching is re-applied per test by the autouse fixture, and the
    values it patches are identical every time)."""
    tmp_path = tmp_path_factory.mktemp("e2e")

    # conftest's autouse fixture is function-scoped; apply the same shrink here
    from unittest import mock

    with (
        mock.patch.object(config, "REGIONS", {TEST_REGION.name: TEST_REGION}),
        mock.patch.object(config, "ENCODING", type(config.ENCODING)(chunk_y=16, chunk_x=16, shard_y=32, shard_x=32)),
    ):
        raster_data = write_synthetic_muraster(tmp_path)
        (tmp_path / "gNATSGO_02_13_2026.gpkg").touch()
        release = catalog.Release(
            directory=tmp_path,
            geopackage=tmp_path / "gNATSGO_02_13_2026.gpkg",
            archive=None,
            release_date=config.RELEASE_DATE,
            release_date_source="test fixture",
        )

        # extracted parquet fixture
        from .conftest import (
            chfrags_frame,
            chtexturegrp_frame,
            cointerp_frame,
            components_frame,
            corestrictions_frame,
            horizons_frame,
            mapunit_frame,
        )

        extracted = tmp_path / "work" / config.RELEASE_DATE / "extracted"
        extracted.mkdir(parents=True)
        for name, frame in {
            "component": components_frame(),
            "chorizon": horizons_frame(),
            "chfrags": chfrags_frame(),
            "corestrictions": corestrictions_frame(),
            "cointerp": cointerp_frame(),
            "mapunit": mapunit_frame(),
            "chtexturegrp": chtexturegrp_frame(),
        }.items():
            frame.write_parquet(extracted / f"{name}.parquet")

        report = intermediates.build_intermediates(release, tmp_path / "work")
        assert report["passed"]
        derived = tmp_path / "work" / config.RELEASE_DATE / "derived"
        texture_classes = json.loads((derived / "texture_classes.json").read_text())

        storage = icechunk.local_filesystem_storage(str(tmp_path / "store"))
        repo = icechunk.Repository.open_or_create(storage)
        session = repo.writable_session("main")
        created = template.init_store(session, texture_classes=texture_classes)
        session.commit("init")
        assert len(created) == len(config.included_variables())

        for spec in config.variables_by_name(None):
            session = repo.writable_session("main")
            stats = rasterize.rasterize_variable(
                session,
                TEST_REGION,
                spec,
                release.muraster_path(TEST_REGION),
                derived if spec.algorithm_id != "direct_raster" else None,
                workers=2,
            )
            rasterize.record_variable_provenance(
                session, TEST_REGION.name, spec.name, {"windows": stats["windows_written"]}
            )
            session.commit(f"rasterize {spec.name}")

        yield {
            "tmp_path": tmp_path,
            "repo": repo,
            "release": release,
            "derived": derived,
            "raster": raster_data,
            "texture_classes": texture_classes,
        }


def _open(pipeline, group):
    session = pipeline["repo"].readonly_session("main")
    return xr.open_zarr(session.store, group=f"{TEST_REGION.name}/{group}", chunks=None)


def test_mukey_exact_equality(pipeline):
    ds = _open(pipeline, "map_unit_properties")
    np.testing.assert_array_equal(ds["mukey"].values, pipeline["raster"])
    assert ds["mukey"].dtype == np.uint32


def test_soil_property_values(pipeline):
    ds = _open(pipeline, "soil_properties")
    sand = ds["sand"].sel(depth_interval="0_20").values
    r, c = PIXELS[100]
    assert sand[r, c] == pytest.approx(44.0)
    assert np.isnan(sand[BACKGROUND])  # background stays fill
    assert np.isnan(sand[10, 10])  # unmatched mukey 999 stays fill
    assert np.isnan(ds["aws"].sel(depth_interval="100_150").values[r, c])  # shallow profiles

    aws = ds["aws"].sel(depth_interval="0_100").values
    assert aws[r, c] == pytest.approx(93.0)
    ksat = ds["ksat"].sel(depth_interval="0_100").values
    assert ksat[r, c] == pytest.approx(2.0)  # least transmissive of dominant component


def test_map_unit_values(pipeline):
    ds = _open(pipeline, "map_unit_properties")
    droughty = ds["droughty"].values
    assert droughty[PIXELS[100]] == 1
    assert droughty[PIXELS[300]] == 1
    assert droughty[PIXELS[400]] == 0
    assert droughty[PIXELS[200]] == 255  # water map unit: missing
    assert droughty[BACKGROUND] == 255

    pwsl = ds["pwsl1pomu"].values
    assert pwsl[PIXELS[200]] == 999
    assert pwsl[PIXELS[100]] == 40
    assert pwsl[BACKGROUND] == 65535

    codes = {info["name"]: int(code) for code, info in pipeline["texture_classes"].items()}
    texture = ds["texture_class"].values
    assert texture[PIXELS[100]] == codes["L"]
    assert texture[PIXELS[300]] == codes["STV-L"]
    assert texture[PIXELS[200]] == 65535  # no surface texture for water
    class_names = ds["texture_class"].attrs["class_names"]
    assert class_names[str(codes["L"])] == "L"

    slope = ds["slope"].values
    assert slope[PIXELS[100]] == pytest.approx(5.0)
    assert np.isnan(slope[PIXELS[200]])


def test_diagnostics_group(pipeline):
    ds = _open(pipeline, "soil_properties/diagnostics")
    thickness = ds["particle_size_contributing_thickness"].sel(depth_interval="0_20").values
    assert thickness[PIXELS[100]] == pytest.approx(20.0)


def test_provenance_recorded_and_resume_guard(pipeline):
    session = pipeline["repo"].readonly_session("main")
    done = rasterize.rasterized_variables(session, TEST_REGION.name)
    assert set(done) == {v.name for v in config.included_variables()}


def test_validate_region_passes(pipeline, monkeypatch):
    monkeypatch.setattr(validate, "VALUE_SAMPLES", 25)
    results = list(
        validate.validate_region(
            pipeline["repo"],
            TEST_REGION.name,
            muraster_path=pipeline["release"].muraster_path(TEST_REGION),
            derived_dir=pipeline["derived"],
            samples=4,
            seed=42,
        )
    )
    failures = [r for r in results if not r.passed]
    assert not failures, failures


def test_validate_sampling_is_seeded_and_worker_count_invariant(pipeline):
    """Concurrency must not change what is sampled: the point reads are issued
    through a thread pool, but the RNG draws happen up front in serial order,
    so one worker and many workers must agree check-for-check."""

    def run(workers):
        return [
            (r.check, r.passed, r.message)
            for r in validate.validate_region(
                pipeline["repo"],
                TEST_REGION.name,
                muraster_path=pipeline["release"].muraster_path(TEST_REGION),
                derived_dir=pipeline["derived"],
                samples=3,
                window=8,
                value_samples=12,
                workers=workers,
                seed=7,
            )
        ]

    serial, parallel = run(1), run(8)
    assert serial == parallel
    assert all(passed for _, passed, _ in serial), serial
    # value_samples is honoured, and every draw now lands on a mapped pixel, so
    # the comparison count is exactly 12 x the non-direct variables
    values = next(m for c, _, m in serial if c == "value_equality")
    n_specs = len([s for s in config.included_variables() if s.algorithm_id != "direct_raster"])
    assert int(values.split(" of ")[1].split()[0]) == 12 * n_specs


def test_validate_fails_when_sampling_finds_no_mapped_pixels(pipeline):
    """A check that compared nothing must fail, not pass: an all-nodata raster
    used to yield a green mukey_equality over millions of background pixels and
    a green value_equality over zero values."""
    tmp_path = pipeline["tmp_path"]
    empty = tmp_path / "empty_muraster.tif"
    with rasterio.open(pipeline["release"].muraster_path(TEST_REGION)) as src:
        profile = src.profile
    with rasterio.open(empty, "w", **profile) as dst:
        dst.write(np.zeros((TEST_REGION.height, TEST_REGION.width), dtype="uint32"), 1)

    results = list(
        validate.validate_region(
            pipeline["repo"],
            TEST_REGION.name,
            muraster_path=empty,
            derived_dir=pipeline["derived"],
            samples=3,
            window=8,
            value_samples=5,
            seed=11,
        )
    )
    for check in ("mukey_equality", "value_equality"):
        result = next(r for r in results if r.check == check)
        assert not result.passed, result
        assert "insufficient sampling" in result.message
        # must not read as a data mismatch
        assert "mismatch" not in result.message


def test_validate_still_skips_cleanly_without_local_sources(pipeline):
    """The genuine opt-outs stay passes: absent MURASTER / intermediates mean
    'not asked for', unlike sampling that ran and found nothing."""
    no_raster = list(
        validate.validate_region(
            pipeline["repo"], TEST_REGION.name, muraster_path=None, derived_dir=None, samples=2, seed=1
        )
    )
    mukey = next(r for r in no_raster if r.check == "mukey_equality")
    assert mukey.passed and "not on disk" in mukey.message

    no_derived = list(
        validate.validate_region(
            pipeline["repo"],
            TEST_REGION.name,
            muraster_path=pipeline["release"].muraster_path(TEST_REGION),
            derived_dir=None,
            samples=2,
            seed=1,
        )
    )
    values = next(r for r in no_derived if r.check == "value_equality")
    assert values.passed and "not on disk" in values.message


def test_validate_catches_corruption(pipeline, monkeypatch):
    monkeypatch.setattr(validate, "VALUE_SAMPLES", 0)
    repo = pipeline["repo"]
    session = repo.writable_session("main")
    arr = zarr.open_array(session.store, path=f"{TEST_REGION.name}/map_unit_properties/mukey", mode="r+")
    arr[3:5, 3:5] = 12345  # deliberate corruption
    session.commit("corrupt for test")
    try:
        results = list(
            validate.validate_region(
                repo,
                TEST_REGION.name,
                muraster_path=pipeline["release"].muraster_path(TEST_REGION),
                derived_dir=None,
                samples=6,
                seed=0,
            )
        )
        equality = next(r for r in results if r.check == "mukey_equality")
        assert not equality.passed
    finally:  # restore for later tests
        session = repo.writable_session("main")
        arr = zarr.open_array(session.store, path=f"{TEST_REGION.name}/map_unit_properties/mukey", mode="r+")
        arr[3:5, 3:5] = pipeline["raster"][3:5, 3:5]
        session.commit("restore after corruption test")


def test_additive_init_creates_only_missing_arrays(pipeline):
    """A new store initialized with a variable subset is completed additively
    without touching existing data."""
    tmp_path = pipeline["tmp_path"]
    storage = icechunk.local_filesystem_storage(str(tmp_path / "store_additive"))
    repo = icechunk.Repository.open_or_create(storage)

    session = repo.writable_session("main")
    created = template.init_store(session, variables=config.variables_by_name(["mukey"]))
    assert [p.rsplit("/", 1)[-1] for p in created] == ["mukey"]
    session.commit("init mukey only")

    session = repo.writable_session("main")
    rasterize.rasterize_variable(
        session,
        TEST_REGION,
        config.variables_by_name(["mukey"])[0],
        pipeline["release"].muraster_path(TEST_REGION),
        None,
        workers=1,
    )
    session.commit("rasterize mukey")

    session = repo.writable_session("main")
    created = template.init_store(session)  # all included variables
    assert len(created) == len(config.included_variables()) - 1  # everything except mukey
    session.commit("extend")

    ro = repo.readonly_session("main")
    arr = zarr.open_array(ro.store, path=f"{TEST_REGION.name}/map_unit_properties/mukey", mode="r")
    np.testing.assert_array_equal(arr[:], pipeline["raster"])  # untouched


def test_window_retry_recovers_from_transient_failure(pipeline, monkeypatch):
    monkeypatch.setattr(rasterize, "RETRY_BASE_SECONDS", 0.0)
    failures = {"remaining": 2}
    original = lookup.Lookup.expand

    def flaky(self, raster):
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            raise OSError("transient store error")
        return original(self, raster)

    monkeypatch.setattr(lookup.Lookup, "expand", flaky)
    repo = pipeline["repo"]
    session = repo.writable_session("main")
    spec = config.variables_by_name(["slope"])[0]
    stats = rasterize.rasterize_variable(
        session, TEST_REGION, spec, pipeline["release"].muraster_path(TEST_REGION), pipeline["derived"], workers=1
    )
    assert failures["remaining"] == 0
    assert stats["windows_written"] > 0


def test_window_retry_exhaustion_fails(pipeline, monkeypatch):
    monkeypatch.setattr(rasterize, "RETRY_BASE_SECONDS", 0.0)

    def always_fail(self, raster):
        raise OSError("permanent store error")

    monkeypatch.setattr(lookup.Lookup, "expand", always_fail)
    repo = pipeline["repo"]
    session = repo.writable_session("main")
    spec = config.variables_by_name(["slope"])[0]
    with pytest.raises(RuntimeError, match=f"failed after {rasterize.TRANSIENT_ATTEMPTS} attempts"):
        rasterize.rasterize_variable(
            session, TEST_REGION, spec, pipeline["release"].muraster_path(TEST_REGION), pipeline["derived"], workers=1
        )


def _single_variable_store(tmp_path, name):
    """A fresh store holding just one initialized (empty) variable."""
    storage = icechunk.local_filesystem_storage(str(tmp_path / "store"))
    repo = icechunk.Repository.open_or_create(storage)
    session = repo.writable_session("main")
    spec = config.variables_by_name([name])[0]
    template.init_store(session, variables=[spec])
    session.commit(f"init {name} only")
    return repo, spec


def test_checkpointed_rasterize_survives_interruption(pipeline, tmp_path_factory):
    """An interrupted run keeps every shard it checkpointed, resumes from there,
    and still leaves exactly one commit for the pair."""
    from usda_gnatsgo.cli import _PairWriter

    repo, spec = _single_variable_store(tmp_path_factory.mktemp("checkpoint"), "mukey")
    muraster = pipeline["release"].muraster_path(TEST_REGION)
    array_path = f"{TEST_REGION.name}/{spec.array_path}"
    total_windows = len(rasterize.shard_aligned_windows(TEST_REGION))
    assert total_windows >= 4, "the fixture must span several batches for this test to mean anything"

    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=False)
    batches = {"count": 0}

    def interrupt_on_second_batch(stats):
        batches["count"] += 1
        session = writer.checkpoint(stats)
        if batches["count"] == 2:
            raise KeyboardInterrupt("simulated interruption")
        return session

    with pytest.raises(KeyboardInterrupt):
        rasterize.rasterize_variable(
            writer.session,
            TEST_REGION,
            spec,
            muraster,
            None,
            workers=1,
            commit_every=1,
            checkpoint=interrupt_on_second_batch,
        )

    session = repo.readonly_session("main")
    progress = rasterize.rasterize_progress(session, TEST_REGION.name)[spec.name]
    assert progress["windows_done"] == 2
    assert spec.name not in rasterize.rasterized_variables(session, TEST_REGION.name)
    partial = zarr.open_array(session.store, path=array_path, mode="r")[:]
    assert (partial != 0).any(), "the checkpointed windows must have survived the interruption"
    assert not np.array_equal(partial, pipeline["raster"])

    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=True)
    assert writer.amending, "the tip is this pair's own in-progress commit"
    stats = rasterize.rasterize_variable(
        writer.session,
        TEST_REGION,
        spec,
        muraster,
        None,
        workers=1,
        commit_every=1,
        checkpoint=writer.checkpoint,
        resume=progress,
    )
    assert stats["windows_done"] == total_windows
    writer.finish(stats, {"release_date": config.RELEASE_DATE})

    session = repo.readonly_session("main")
    np.testing.assert_array_equal(zarr.open_array(session.store, path=array_path, mode="r")[:], pipeline["raster"])
    assert not rasterize.rasterize_progress(session, TEST_REGION.name)  # cleared on completion
    assert spec.name in rasterize.rasterized_variables(session, TEST_REGION.name)
    messages = [snapshot.message for snapshot in repo.ancestry(branch="main")]
    assert sum("Rasterize" in message for message in messages) == 1, messages


def test_checkpoint_commit_retries_a_transient_store_failure(pipeline, tmp_path_factory, monkeypatch):
    """A flaky response on the commit itself must not throw away the batch: the
    ref moves last, so the commit is safe to repeat on the same session."""
    from usda_gnatsgo.cli import _PairWriter

    monkeypatch.setattr(rasterize, "RETRY_BASE_SECONDS", 0.0)
    repo, spec = _single_variable_store(tmp_path_factory.mktemp("commit_retry"), "mukey")
    muraster = pipeline["release"].muraster_path(TEST_REGION)
    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=False)

    failures = {"remaining": 2}
    real_commit = icechunk.Session.commit
    real_amend = icechunk.Session.amend

    def flaky(real):
        def wrapper(self, *args, **kwargs):
            if failures["remaining"] > 0:
                failures["remaining"] -= 1
                raise icechunk.StorageError("session error: object store error service error", "object-store")
            return real(self, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(icechunk.Session, "commit", flaky(real_commit))
    monkeypatch.setattr(icechunk.Session, "amend", flaky(real_amend))

    stats = rasterize.rasterize_variable(
        writer.session,
        TEST_REGION,
        spec,
        muraster,
        None,
        workers=1,
        commit_every=1,
        checkpoint=writer.checkpoint,
    )
    assert failures["remaining"] == 0, "the flaky commits must actually have been exercised"
    writer.finish(stats, {"release_date": config.RELEASE_DATE})

    session = repo.readonly_session("main")
    array_path = f"{TEST_REGION.name}/{spec.array_path}"
    np.testing.assert_array_equal(zarr.open_array(session.store, path=array_path, mode="r")[:], pipeline["raster"])
    messages = [snapshot.message for snapshot in repo.ancestry(branch="main")]
    assert sum("Rasterize" in message for message in messages) == 1, messages


def test_expired_credentials_stop_the_run_instead_of_retrying(pipeline, tmp_path_factory, monkeypatch):
    """Expired credentials would fail every remaining window too, so the run
    must stop at its last checkpoint after a single attempt."""
    from usda_gnatsgo.cli import _PairWriter

    # a real backoff here would make a regression show up as a hang, not a failure
    monkeypatch.setattr(rasterize, "RETRY_BASE_SECONDS", 30.0)
    repo, spec = _single_variable_store(tmp_path_factory.mktemp("expired"), "mukey")
    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=False)

    attempts = {"count": 0}
    real_commit = icechunk.Session.commit

    def expired(self, *args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:  # checkpoint the first batch, then lose the credentials
            return real_commit(self, *args, **kwargs)
        # the shape icechunk reports a raising get_credentials callback in
        raise icechunk.StorageError(
            "object store error dispatch failure: other: the credential provider was not enabled: "
            f"CredentialsUnavailable: {store.CREDENTIALS_MARKER}: source-coop creds failed",
            "object-store",
        )

    monkeypatch.setattr(icechunk.Session, "commit", expired)
    monkeypatch.setattr(icechunk.Session, "amend", expired)

    with pytest.raises(rasterize.CredentialsExpired, match="no longer valid"):
        rasterize.rasterize_variable(
            writer.session,
            TEST_REGION,
            spec,
            pipeline["release"].muraster_path(TEST_REGION),
            None,
            workers=1,
            commit_every=1,
            checkpoint=writer.checkpoint,
        )
    assert attempts["count"] == 2, "the failing commit must not have been retried"

    monkeypatch.undo()
    progress = rasterize.rasterize_progress(repo.readonly_session("main"), TEST_REGION.name)[spec.name]
    assert progress["windows_done"] == 1, "the checkpoint taken before the expiry must still stand"


def test_expired_credentials_exit_the_cli_with_guidance(caplog):
    """The operator gets one actionable line and a nonzero exit, not a traceback."""
    from usda_gnatsgo.cli import _stop_on_expired_credentials

    with pytest.raises(typer.Exit) as exit_info, caplog.at_level("ERROR"), _stop_on_expired_credentials():
        raise store.CredentialsUnavailable("cached credentials have expired")
    assert exit_info.value.exit_code == 1
    assert "source-coop login" in caplog.text

    # unrelated failures must keep their own type and traceback
    with pytest.raises(ValueError), _stop_on_expired_credentials():
        raise ValueError("something else")


def test_transient_store_errors_are_not_mistaken_for_credential_failures():
    """The gateway noise that retries exist to absorb must stay retryable."""
    transient = icechunk.StorageError(
        "object store error service error: unhandled error: error parsing XML: no root element", "object-store"
    )
    assert not store.is_credentials_failure(transient)
    assert store.is_credentials_failure(icechunk.StorageError("... ExpiredToken ...", "object-store"))


def test_resume_carries_unmatched_mukey_count(pipeline, tmp_path_factory):
    """Unmatched keys found before an interruption still count afterwards, even
    though the window that found them is never re-read."""
    from usda_gnatsgo.cli import _PairWriter

    repo, spec = _single_variable_store(tmp_path_factory.mktemp("carry"), "slope")
    muraster = pipeline["release"].muraster_path(TEST_REGION)
    args = (TEST_REGION, spec, muraster, pipeline["derived"])

    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=False)
    stop_after_first = {"stop": False}

    def interrupt_after_the_unmatched_window(stats):
        session = writer.checkpoint(stats)
        if stats["unmatched_mukey_count"]:
            stop_after_first["stop"] = True
            raise KeyboardInterrupt("simulated interruption")
        return session

    with pytest.raises(KeyboardInterrupt):
        rasterize.rasterize_variable(
            writer.session, *args, workers=1, commit_every=1, checkpoint=interrupt_after_the_unmatched_window
        )
    assert stop_after_first["stop"]

    progress = rasterize.rasterize_progress(repo.readonly_session("main"), TEST_REGION.name)[spec.name]
    assert progress["unmatched_mukeys"] == [UNMATCHED_MUKEY]

    writer = _PairWriter(repo, TEST_REGION.name, spec.name, config.RELEASE_DATE, resuming=True)
    stats = rasterize.rasterize_variable(
        writer.session, *args, workers=1, commit_every=1, checkpoint=writer.checkpoint, resume=progress
    )
    assert stats["unmatched_mukey_count"] == 1
    assert not stats["unmatched_truncated"]


def test_refuses_a_store_built_on_another_shard_grid(pipeline, tmp_path_factory):
    """Windows are shard-aligned, so writing them into differently sharded
    arrays would let two windows co-write one object. Refuse, don't corrupt."""
    from unittest import mock

    tmp_path = tmp_path_factory.mktemp("mismatch")
    storage = icechunk.local_filesystem_storage(str(tmp_path / "store"))
    repo = icechunk.Repository.open_or_create(storage)
    spec = config.variables_by_name(["mukey"])[0]
    session = repo.writable_session("main")
    with mock.patch.object(config, "ENCODING", config.EncodingSpec(chunk_y=16, chunk_x=16, shard_y=64, shard_x=64)):
        template.init_store(session, variables=[spec])
    session.commit("init under a different shard grid")

    with pytest.raises(ValueError, match="array shards"):
        rasterize.rasterize_variable(
            repo.writable_session("main"),
            TEST_REGION,
            spec,
            pipeline["release"].muraster_path(TEST_REGION),
            None,
            workers=1,
        )
    with pytest.raises(ValueError, match="EncodingSpec"):  # additive init refuses before creating anything
        template.init_store(repo.writable_session("main"), variables=[spec])


def test_resume_offset_rejects_a_record_from_another_shard_grid():
    windows = len(rasterize.shard_aligned_windows(TEST_REGION))
    enc = config.ENCODING
    record = {
        "variable": "mukey",
        "windows_done": 2,
        "window_count": windows,
        "shard_shape": [enc.shard_y, enc.shard_x],
    }
    assert rasterize.resume_offset(record, TEST_REGION) == 2
    assert rasterize.resume_offset(None, TEST_REGION) == 0
    assert rasterize.resume_offset({**record, "shard_shape": [8192, 8192]}, TEST_REGION) == 0
    assert rasterize.resume_offset({**record, "window_count": windows + 1}, TEST_REGION) == 0
    assert rasterize.resume_offset({**record, "windows_done": windows + 1}, TEST_REGION) == 0


def test_shard_window_coverage():
    windows = rasterize.shard_aligned_windows(TEST_REGION)
    covered = np.zeros((TEST_REGION.height, TEST_REGION.width), dtype=int)
    for gy0, gy1, gx0, gx1 in windows:
        covered[gy0:gy1, gx0:gx1] += 1
    assert (covered == 1).all()  # exact cover, no gaps, no overlaps
    enc = config.ENCODING
    for gy0, _gy1, gx0, _gx1 in windows:
        assert gy0 % enc.shard_y == 0 and gx0 % enc.shard_x == 0


def test_unmatched_mukeys_reported(pipeline):
    session = pipeline["repo"].readonly_session("main")
    done = rasterize.rasterized_variables(session, TEST_REGION.name)
    assert done  # provenance exists; the unmatched key was counted during the run
    # verify via a direct expansion
    spec = config.variables_by_name(["slope"])[0]
    import polars as pl

    table = pl.read_parquet(pipeline["derived"] / "map_unit_properties.parquet", columns=["mukey", "slope"])
    built = lookup.build_lookups(table, spec)
    _, unmatched = built.expand(np.array([UNMATCHED_MUKEY], dtype="uint32"))
    assert unmatched.tolist() == [UNMATCHED_MUKEY]
