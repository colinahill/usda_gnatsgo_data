"""Command-line interface.

Examples:
    # phase 1-3: inspect, extract, derive (local work/ intermediates)
    usda-gnatsgo inspect-release data/
    usda-gnatsgo extract data/
    usda-gnatsgo build-intermediates data/

    # phase 4-7 against a local dev store
    usda-gnatsgo init-store --store ./gnatsgo_store_local
    usda-gnatsgo rasterize data/ --store ./gnatsgo_store_local --regions guam,mexico
    usda-gnatsgo status --store ./gnatsgo_store_local
    usda-gnatsgo validate data/ --store ./gnatsgo_store_local --regions guam
    usda-gnatsgo release --store ./gnatsgo_store_local

    # the published product (`source-coop login` first, or --credentials-file)
    usda-gnatsgo init-store --source-coop-account chill
    usda-gnatsgo rasterize data/ --source-coop-account chill
"""

from __future__ import annotations

import contextlib
import functools
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from . import catalog, config, rasterize, store, template

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("usda_gnatsgo")

StoreOpt = Annotated[str | None, typer.Option("--store", help="Store target: local path or s3://bucket/prefix")]
AccountOpt = Annotated[
    str | None,
    typer.Option("--source-coop-account", help=f"Source Coop account; targets the '{store.PRODUCT_NAME}' product"),
]
CredsOpt = Annotated[
    str | None,
    typer.Option("--credentials-file", help="JSON credentials file (Source Coop 'JSON (SDK)' export format)"),
]
WorkDirOpt = Annotated[Path, typer.Option("--work-dir", help="intermediates directory")]
RegionsOpt = Annotated[str, typer.Option("--regions", help="comma-separated region groups; empty = all 12")]
VariablesOpt = Annotated[
    str, typer.Option("--variables", help="comma-separated variable names; empty = all included variables")
]


def _resolve_storage(store_uri: str | None, account: str | None, credentials_file: str | None):
    if bool(store_uri) == bool(account):
        raise typer.BadParameter("provide exactly one of --store or --source-coop-account")
    if account:
        log.info("store: source coop s3://%s/%s/%s", account, store.PRODUCT_NAME, store.STORE_SUBPATH)
        return store.source_coop_storage(account, credentials_file=credentials_file)
    log.info("store: %s", store_uri)
    return store.storage_from_uri(store_uri, credentials_file=credentials_file)


@contextlib.contextmanager
def _stop_on_expired_credentials():
    """Turn a credential failure into one actionable message and a clean exit.

    Scoped this widely because credentials can go bad at any store call, and
    icechunk reports them as a generic StorageError (see
    store.is_credentials_failure), so the type alone can't be caught.
    """
    try:
        yield
    except Exception as exc:
        if not (isinstance(exc, rasterize.CredentialsExpired) or store.is_credentials_failure(exc)):
            raise
        log.error("store credentials expired or were rejected: %s", exc)
        log.error("re-run `source-coop login` (or refresh --credentials-file), then re-run this command to resume")
        raise typer.Exit(code=1) from None


def _parse_regions(regions: str) -> list[str]:
    if not regions:
        return sorted(config.REGIONS)
    names = [r.strip() for r in regions.split(",") if r.strip()]
    unknown = [n for n in names if n not in config.REGIONS]
    if unknown:
        raise typer.BadParameter(f"unknown region(s) {unknown}; valid: {sorted(config.REGIONS)}")
    return names


def _parse_variables(variables: str) -> list[config.VariableSpec]:
    names = [v.strip() for v in variables.split(",") if v.strip()] if variables else None
    try:
        return config.variables_by_name(names)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None


def _derived_dir(work_dir: Path) -> Path:
    return work_dir / config.RELEASE_DATE / "derived"


def _load_texture_classes(work_dir: Path) -> dict | None:
    import json

    path = _derived_dir(work_dir) / "texture_classes.json"
    return json.loads(path.read_text()) if path.exists() else None


@app.command()
def inspect_release(
    source: Annotated[Path, typer.Argument(help="release directory (e.g. data/)")],
    work_dir: WorkDirOpt = Path("work"),
    checksums: Annotated[bool, typer.Option(help="sha256 every artifact (slow for the 57 GB GeoPackage)")] = True,
):
    """Phase 1: locate the release, verify grids and schema, write the manifest."""
    from . import source as source_mod

    release = catalog.locate_release(source)
    log.info("release %s (date from %s)", release.release_date, release.release_date_source)
    report = source_mod.inspect_release(release, work_dir, checksums=checksums)
    if not report["passed"]:
        for error in report["grid_errors"] + report["schema"]["missing"]:
            log.error("%s", error)
        raise typer.Exit(code=1)
    log.info("inspection passed: %d artifacts", report["artifact_count"])


@app.command()
def extract(
    source: Annotated[Path, typer.Argument(help="release directory")],
    work_dir: WorkDirOpt = Path("work"),
    tables: Annotated[str, typer.Option(help="comma-separated table subset")] = "",
    force: Annotated[bool, typer.Option("--force", help="re-extract even when current")] = False,
):
    """Phase 2: stream required GeoPackage tables to Parquet."""
    from . import extract as extract_mod

    release = catalog.locate_release(source)
    table_list = [t.strip() for t in tables.split(",") if t.strip()] or None
    out_dir = extract_mod.extract_all(release, work_dir, tables=table_list, force=force)
    log.info("extracted tables under %s", out_dir)


@app.command()
def build_intermediates(
    source: Annotated[Path, typer.Argument(help="release directory")],
    work_dir: WorkDirOpt = Path("work"),
):
    """Phase 3: derive valu1/horizon/map-unit Parquet intermediates + gates."""
    from . import intermediates

    release = catalog.locate_release(source)
    report = intermediates.build_intermediates(release, work_dir)
    log.info("scientific gates passed: %d checks", len(report["checks"]))


@app.command()
def init_store(
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
    regions: RegionsOpt = "",
    work_dir: WorkDirOpt = Path("work"),
):
    """Phase 4: create (or additively extend) the store structure. Idempotent."""
    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage, create=True)
    session = repo.writable_session("main")
    created = template.init_store(session, _parse_regions(regions), texture_classes=_load_texture_classes(work_dir))
    if not created:
        log.info("store structure already complete; nothing to create")
        return
    snapshot = session.commit(f"Initialize/extend store structure ({len(created)} arrays)")
    log.info("committed %d new arrays at %s", len(created), snapshot)


class _PairWriter:
    """Session and commit policy for one (region, variable) pair.

    The pair's first checkpoint is a real commit; every later checkpoint amends
    it. A finished pair is therefore a single snapshot no matter how many
    checkpoints (or resumed runs) it took, while the branch tip still advances
    after every batch so an interrupted upload keeps the shards it wrote.

    Amending rewrites the tip, so it is only ever done to this pair's own
    in-progress commit: an untagged tip whose metadata names this pair.
    """

    def __init__(self, repo, region_name: str, variable: str, release_date: str, *, resuming: bool):
        self.repo = repo
        self.region_name = region_name
        self.variable = variable
        self.release_date = release_date
        self.session = repo.writable_session("main")
        self.amending = resuming and self._tip_is_own_pair()
        if resuming and not self.amending:
            log.info("%s/%s: resuming under a new commit (the branch tip belongs to other work)", region_name, variable)

    def checkpoint(self, stats: dict):
        """Persist a batch and return the session for the next one."""
        rasterize.record_progress(self.session, self.region_name, self.variable, stats)
        self._persist(stats, done=False)
        return self.session

    def finish(self, stats: dict, provenance: dict, before_commit=None) -> str:
        """Record provenance, drop the checkpoint record, and commit/amend."""
        rasterize.clear_progress(self.session, self.region_name, self.variable)
        rasterize.record_variable_provenance(self.session, self.region_name, self.variable, provenance)
        if before_commit is not None:
            before_commit(self.session)
        return self._persist(stats, done=True)

    def _persist(self, stats: dict, *, done: bool) -> str:
        pair = f"{self.region_name}/{self.variable}"
        message = f"Rasterize {pair} gNATSGO {self.release_date}"
        if not done:  # a tip left by an interrupted run should say so
            message += f" (in progress: {stats['windows_done']}/{stats['window_count']} windows)"
        # the full unmatched key list is resume state, not commit metadata
        metadata = {k: v for k, v in stats.items() if k != "unmatched_mukeys"}
        # a commit publishes nothing until the branch ref moves last, so a failed
        # one is safe to repeat on the same session; without this a single flaky
        # response from the store throws away the whole batch
        if self.amending:
            snapshot = rasterize.retry_transient(
                lambda: self.session.amend(message, metadata=metadata), f"{pair} checkpoint amend"
            )
        else:
            snapshot = rasterize.retry_transient(
                lambda: self.session.commit(message, metadata=metadata), f"{pair} checkpoint commit"
            )
            self.amending = True
        # committing consumes the session; the next batch needs a fresh one
        self.session = (
            None
            if done
            else rasterize.retry_transient(lambda: self.repo.writable_session("main"), f"{pair} session refresh")
        )
        return snapshot

    def _tip_is_own_pair(self) -> bool:
        tip = next(iter(self.repo.ancestry(branch="main")), None)
        if tip is None:
            return False
        metadata = tip.metadata or {}
        if (metadata.get("region"), metadata.get("variable")) != (self.region_name, self.variable):
            return False
        # a tag pinned to the tip would dangle on the snapshot amend replaces
        return not any(self.repo.lookup_tag(tag) == tip.id for tag in self.repo.list_tags())


def rasterize_cmd(
    source: Annotated[Path, typer.Argument(help="release directory (MURASTER files)")],
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
    regions: RegionsOpt = "",
    variables: VariablesOpt = "",
    work_dir: WorkDirOpt = Path("work"),
    workers: Annotated[int, typer.Option(help="threads writing shard windows concurrently")] = 8,
    commit_every: Annotated[
        int,
        typer.Option("--commit-every", help="checkpoint after this many shard windows (0 = only at the end)"),
    ] = 8,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="re-rasterize (region, variable) pairs already recorded")
    ] = False,
):
    """Phase 5: fill (region, variable) pairs; one commit each, checkpointed.

    Pairs already recorded in the region's provenance are skipped unless
    confirmed interactively or --overwrite is passed. Within a pair, work is
    checkpointed every --commit-every windows, so an interrupted run resumes
    from its last checkpoint instead of re-uploading the whole variable. One
    writer per branch at a time.
    """
    release = catalog.locate_release(source)
    specs = _parse_variables(variables)
    region_names = _parse_regions(regions)
    derived = _derived_dir(work_dir)
    needs_derived = any(s.algorithm_id != "direct_raster" for s in specs)
    if needs_derived and not derived.exists():
        raise typer.BadParameter(f"derived intermediates not found at {derived}; run build-intermediates first")

    storage = _resolve_storage(store_uri, account, credentials_file)

    with _stop_on_expired_credentials():
        repo = store.open_repo(storage)

        for region_name in region_names:
            region = config.REGIONS[region_name]
            session = repo.readonly_session("main")
            done = rasterize.rasterized_variables(session, region_name)
            in_flight = rasterize.rasterize_progress(session, region_name)
            for spec in specs:
                # a checkpoint record outranks provenance: the pair is mid-write
                # (an interrupted --overwrite), so it owes the rest of its windows
                if spec.name in done and spec.name not in in_flight and not overwrite:
                    if sys.stdin.isatty() and sys.stdout.isatty():
                        if not typer.confirm(f"{region_name}/{spec.name} already rasterized; redo?", default=False):
                            log.info("skipping %s/%s (already recorded)", region_name, spec.name)
                            continue
                    else:
                        log.info("skipping %s/%s (already recorded; --overwrite to redo)", region_name, spec.name)
                        continue

                resume = in_flight.get(spec.name)
                resumed_windows = rasterize.resume_offset(resume, region)
                if resumed_windows:
                    log.info("%s/%s: resuming after window %d", region_name, spec.name, resumed_windows)
                writer = _PairWriter(repo, region_name, spec.name, release.release_date, resuming=bool(resumed_windows))
                stats = rasterize.rasterize_variable(
                    writer.session,
                    region,
                    spec,
                    release.muraster_path(region),
                    derived if spec.algorithm_id != "direct_raster" else None,
                    workers=workers,
                    commit_every=commit_every,
                    checkpoint=writer.checkpoint,
                    resume=resume,
                )
                provenance = {
                    "release_date": release.release_date,
                    "algorithm_id": spec.algorithm_id,
                    "algorithm_version": spec.algorithm_version,
                    "muraster": region.muraster_filename,
                    **{
                        k: stats[k]
                        for k in ("windows_written", "windows_skipped_all_background", "unmatched_mukey_count")
                    },
                }
                texture_attrs = None
                if spec.name == "texture_class":
                    texture_attrs = functools.partial(_update_texture_attrs, region_name=region_name, work_dir=work_dir)
                snapshot = writer.finish(stats, provenance, before_commit=texture_attrs)
                log.info("committed %s/%s as %s", region_name, spec.name, snapshot)

            done = rasterize.rasterized_variables(repo.readonly_session("main"), region_name)
            if all(v.name in done for v in config.included_variables()):
                tag = _ensure_tag(repo, f"{region_name}-{release.release_date}")
                if tag:
                    log.info("region %s complete; tagged %s", region_name, tag)


def _update_texture_attrs(session, region_name: str, work_dir: Path) -> None:
    """Refresh texture_class code-table attrs from the derived intermediates."""
    import zarr

    from . import metadata

    classes = _load_texture_classes(work_dir)
    if not classes:
        return
    arr = zarr.open_array(session.store, path=f"{region_name}/map_unit_properties/texture_class", mode="r+")
    arr.attrs.update(metadata.texture_class_attrs({int(code): info for code, info in classes.items()}))


def _ensure_tag(repo, base: str) -> str | None:
    """Create a tag for the current main snapshot unless the base tag exists.

    Icechunk tags are immutable and names are burned forever on deletion, so
    NEVER delete a tag: re-runs get a ``-r2``/``-r3``... suffix.
    """
    import icechunk

    snapshot = repo.lookup_branch("main")
    try:
        repo.lookup_tag(base)
    except icechunk.IcechunkError:
        repo.create_tag(base, snapshot_id=snapshot)
        return base
    # base exists; only add a revision tag when it points elsewhere
    if repo.lookup_tag(base) == snapshot:
        return None
    for attempt in range(2, 100):
        name = f"{base}-r{attempt}"
        try:
            repo.lookup_tag(name)
        except icechunk.IcechunkError:
            repo.create_tag(name, snapshot_id=snapshot)
            log.warning("tag %s already used; tagged as %s", base, name)
            return name
    raise RuntimeError(f"could not create a tag for {base}")


@app.command()
def status(
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
):
    """The region x variable completion matrix (what a resumed backfill owes)."""
    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage)
    session = repo.readonly_session("main")
    specs = config.included_variables()
    incomplete_total = 0
    for region_name in sorted(config.REGIONS):
        done = rasterize.rasterized_variables(session, region_name)
        in_flight = rasterize.rasterize_progress(session, region_name)
        # a checkpointed pair still owes windows even if provenance calls it done
        missing = [s.name for s in specs if s.name not in done or s.name in in_flight]
        incomplete_total += len(missing)
        if not missing:
            detail = ""
            state = "complete"
        elif len(missing) == len(specs) and not in_flight:
            detail = ""
            state = "not started"
        else:
            detail = f"  missing: {', '.join(missing)}"
            state = f"{len(specs) - len(missing)}/{len(specs)}"
        if in_flight:
            started = ", ".join(
                f"{name} {record.get('windows_done', 0)}/{record.get('window_count', 0)} windows"
                for name, record in sorted(in_flight.items())
            )
            detail = f"  checkpointed: {started}{detail}"
        typer.echo(f"{region_name:24s} {state}{detail}")
    typer.echo(
        "\nall (region, variable) pairs complete" if not incomplete_total else f"\n{incomplete_total} pairs remaining"
    )


@app.command()
def validate(
    source: Annotated[Path | None, typer.Argument(help="release directory (enables source comparisons)")] = None,
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
    regions: RegionsOpt = "",
    work_dir: WorkDirOpt = Path("work"),
    samples: Annotated[int, typer.Option(help="random windows compared against the MURASTER")] = 8,
):
    """Phase 6: verify store structure and sampled contents."""
    from . import validate as validate_mod

    release = catalog.locate_release(source) if source else None
    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage)
    derived = _derived_dir(work_dir)
    failures = 0
    for region_name in _parse_regions(regions):
        region = config.REGIONS[region_name]
        results = validate_mod.validate_region(
            repo,
            region_name,
            muraster_path=release.muraster_path(region) if release else None,
            derived_dir=derived if derived.exists() else None,
            samples=samples,
        )
        for result in results:
            level = logging.INFO if result.passed else logging.ERROR
            log.log(
                level, "%s %s: %s [%s]", region_name, "PASS" if result.passed else "FAIL", result.check, result.message
            )
            failures += not result.passed
    if failures:
        raise typer.Exit(code=1)
    log.info("all validations passed")


@app.command()
def release(
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
):
    """Phase 7: tag the release (refuses while any (region, variable) pair is missing)."""
    import xarray as xr

    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage)
    session = repo.readonly_session("main")
    specs = config.included_variables()
    missing = []
    for region_name in sorted(config.REGIONS):
        done = rasterize.rasterized_variables(session, region_name)
        in_flight = rasterize.rasterize_progress(session, region_name)
        missing.extend(f"{region_name}/{s.name}" for s in specs if s.name not in done or s.name in in_flight)
    if missing:
        log.error("release refused: %d (region, variable) pairs missing, e.g. %s", len(missing), missing[:5])
        raise typer.Exit(code=1)

    tag = _ensure_tag(repo, config.RELEASE_DATE)
    log.info("release tag: %s", tag or f"{config.RELEASE_DATE} (already at this snapshot)")

    # reopen through the tag and re-run critical read checks
    tagged = repo.readonly_session(tag=config.RELEASE_DATE)
    for region_name in sorted(config.REGIONS):
        ds = xr.open_zarr(tagged.store, group=f"{region_name}/map_unit_properties", chunks=None)
        assert "mukey" in ds
    log.info("release %s verified through the tag for all %d regions", config.RELEASE_DATE, len(config.REGIONS))


@app.command()
def info(
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
    region: Annotated[str, typer.Option(help="show one region's datasets")] = "conus",
):
    """Show store structure, snapshots, and tags."""
    import xarray as xr

    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage)
    session = repo.readonly_session("main")
    for group in ("soil_properties", "map_unit_properties"):
        try:
            ds = xr.open_zarr(session.store, group=f"{region}/{group}", chunks=None)
        except (KeyError, FileNotFoundError):
            continue
        typer.echo(f"--- {region}/{group} ---")
        typer.echo(str(ds))
    typer.echo("--- tags ---")
    for tag in sorted(repo.list_tags()):
        typer.echo(tag)
    typer.echo("--- recent snapshots ---")
    for snap in list(repo.ancestry(branch="main"))[:10]:
        typer.echo(f"{snap.id}  {snap.written_at:%Y-%m-%d %H:%M}  {snap.message}")


@app.command()
def garbage_collect(
    store_uri: StoreOpt = None,
    account: AccountOpt = None,
    credentials_file: CredsOpt = None,
    older_than_hours: Annotated[float, typer.Option(help="keep objects written more recently than this")] = 1.0,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="report what would be deleted, delete nothing")] = False,
):
    """Reclaim objects that no branch or tag references any more.

    Checkpointed rasterizing amends a pair's commit after every batch, and each
    amend orphans the snapshot and manifests it replaced; those are what this
    reclaims. Never run it while a backfill is writing: an uncommitted session's
    freshly uploaded shards are unreferenced too, and --older-than-hours is the
    only thing keeping them.
    """
    storage = _resolve_storage(store_uri, account, credentials_file)
    repo = store.open_repo(storage)
    session = repo.readonly_session("main")
    unfinished = [
        f"{region}/{variable}"
        for region in sorted(config.REGIONS)
        for variable in sorted(rasterize.rasterize_progress(session, region))
    ]
    if unfinished:
        log.warning(
            "%d pair(s) are checkpointed but unfinished (%s); confirm no rasterize is running",
            len(unfinished),
            ", ".join(unfinished),
        )
    summary = repo.garbage_collect(datetime.now(UTC) - timedelta(hours=older_than_hours), dry_run=dry_run)
    log.info(
        "%s %d chunks, %d manifests, %d snapshots, %d transaction logs, %d attribute files (%.2f GB)",
        "would delete" if dry_run else "deleted",
        summary.chunks_deleted,
        summary.manifests_deleted,
        summary.snapshots_deleted,
        summary.transaction_logs_deleted,
        summary.attributes_deleted,
        summary.bytes_deleted / 1e9,
    )


@app.command()
def publish_readme(
    account: Annotated[str, typer.Option("--source-coop-account")] = store.SOURCE_COOP_ACCOUNT,
    credentials_file: CredsOpt = None,
):
    """Upload product/README.md to the product root (the Source Coop landing page)."""
    from . import remote

    remote.upload_readme(account, credentials_file=credentials_file)


@app.command()
def upload_audit(
    account: Annotated[str, typer.Option("--source-coop-account")] = store.SOURCE_COOP_ACCOUNT,
    credentials_file: CredsOpt = None,
    work_dir: WorkDirOpt = Path("work"),
):
    """Upload reports + diagnostics Parquet to the product's audit/{release}/ prefix."""
    from . import remote

    base = work_dir / config.RELEASE_DATE
    files = [
        *sorted((base / "reports").glob("*.json")),
        base / "derived" / "horizon_diagnostics.parquet",
        base / "derived" / "terrain_audit.parquet",
        base / "derived" / "texture_classes.json",
        base / "source_manifest.parquet",
        base / "schema_report.json",
    ]
    files = [f for f in files if f.exists()]
    if not files:
        raise typer.BadParameter(f"no audit artifacts under {base}")
    remote.upload_audit(account, files, config.RELEASE_DATE, credentials_file=credentials_file)


@app.command()
def clean_remote_store(
    account: Annotated[str, typer.Option("--source-coop-account")] = store.SOURCE_COOP_ACCOUNT,
    credentials_file: CredsOpt = None,
    workers: Annotated[int, typer.Option(help="parallel deletes")] = 8,
):
    """DESTRUCTIVE: delete every object in the remote store prefix.

    Only needed to abandon a version path - rasterizing is additive and never
    requires this. Asks for two confirmations, interactive terminal only.
    """
    from . import remote

    prefix = f"s3://{account}/{store.PRODUCT_NAME}/{store.STORE_SUBPATH}"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise typer.BadParameter(f"clean-remote-store refuses to run non-interactively (target {prefix})")

    s3 = remote.client(credentials_file)
    keys, total = remote.store_keys(account, s3=s3)
    if not keys:
        log.info("%s is already empty", prefix)
        return

    typer.echo(f"\nTarget: {prefix}\n  {len(keys)} objects, {total / 1e9:.2f} GB")
    typer.echo("This deletes the published store: all data, snapshot history, and tags.\n")
    if not typer.confirm(f"Delete all {len(keys)} objects under {prefix}?"):
        raise typer.Abort
    if typer.prompt("Confirm again by typing the store path exactly") != prefix:
        typer.echo("path did not match; nothing deleted")
        raise typer.Abort

    remote.delete_keys(account, keys, workers=workers, s3=s3)
    log.info("deleted %d objects under %s", len(keys), prefix)


# typer uses function names with underscores -> dashes; keep `rasterize` clean
app.command("rasterize")(rasterize_cmd)

if __name__ == "__main__":
    app()
