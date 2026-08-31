"""Locate the local gNATSGO release and establish its authoritative identity.

The release date comes from the source archive / GeoPackage filenames
(``MM_DD_YYYY`` -> ISO). The extraction-directory name is NOT authoritative
(the 2026 package extracts ``gNATSGO_gpkg_02_13_2026.7z`` into a directory
named ``gNATSGO_gpkg_01_30_2026``): a mismatching directory name warns, but
conflicting archive/GeoPackage dates are a hard error.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{2})_(\d{2})_(\d{4})")


def parse_release_date(filename: str) -> str | None:
    """ISO date from a ``MM_DD_YYYY`` token in a filename, else None."""
    match = _DATE_RE.search(filename)
    if not match:
        return None
    month, day, year = match.groups()
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return f"{year}-{month}-{day}"


@dataclass(frozen=True)
class Release:
    """A located, identity-checked local release."""

    directory: Path  # extraction directory holding the GeoPackage and rasters
    geopackage: Path
    archive: Path | None  # original .7z when present
    release_date: str
    release_date_source: str  # which filename supplied the date
    warnings: tuple[str, ...] = field(default=())

    def muraster_path(self, region: config.RegionSpec) -> Path:
        return self.directory / region.muraster_filename

    def saraster_path(self, region: config.RegionSpec) -> Path:
        """The survey-area raster lives in a subdirectory in the 2026 package
        ("Soil Surrvey Area Boundaries and Data Source", typo USDA's); search
        the release tree rather than hardcoding that name."""
        direct = self.directory / region.saraster_filename
        if direct.exists():
            return direct
        matches = sorted(self.directory.rglob(region.saraster_filename))
        return matches[0] if matches else direct


def locate_release(source: Path) -> Release:
    """Resolve ``source`` (an extraction directory, or a directory containing
    one plus the archive, e.g. ``data/``) to a Release with a reconciled date.
    """
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"release source {source} is not a directory")

    geopackages = sorted(source.glob("*.gpkg")) or sorted(source.glob("*/*.gpkg"))
    if len(geopackages) != 1:
        raise ValueError(f"expected exactly one .gpkg under {source}, found {len(geopackages)}: {geopackages}")
    geopackage = geopackages[0]
    directory = geopackage.parent

    archives = sorted(source.glob("*.7z")) + ([] if source == directory else sorted(directory.glob("*.7z")))
    archive = archives[0] if archives else None
    if len(archives) > 1:
        raise ValueError(f"multiple source archives under {source}: {archives}")

    gpkg_date = parse_release_date(geopackage.name)
    archive_date = parse_release_date(archive.name) if archive else None
    if gpkg_date and archive_date and gpkg_date != archive_date:
        raise ValueError(
            f"conflicting release dates: archive {archive.name} -> {archive_date}, "
            f"GeoPackage {geopackage.name} -> {gpkg_date}"
        )
    release_date = gpkg_date or archive_date
    if release_date is None:
        raise ValueError(
            f"no MM_DD_YYYY release date in {geopackage.name}" + (f" or {archive.name}" if archive else "")
        )
    date_source = geopackage.name if gpkg_date else archive.name  # type: ignore[union-attr]

    warnings = []
    dir_date = parse_release_date(directory.name)
    if dir_date and dir_date != release_date:
        message = (
            f"extraction directory {directory.name} suggests {dir_date} but the source files say "
            f"{release_date}; the directory name is not authoritative"
        )
        warnings.append(message)
        log.warning(message)

    if release_date != config.RELEASE_DATE:
        raise ValueError(
            f"located release {release_date} but config.RELEASE_DATE is {config.RELEASE_DATE}; "
            "update config.py (and bump DATASET_VERSION for a new release) before ingesting"
        )

    missing = [r.muraster_filename for r in config.REGIONS.values() if not (directory / r.muraster_filename).exists()]
    if missing:
        raise FileNotFoundError(f"release at {directory} is missing MURASTER files: {missing}")

    return Release(
        directory=directory,
        geopackage=geopackage,
        archive=archive,
        release_date=release_date,
        release_date_source=date_source,
        warnings=tuple(warnings),
    )
