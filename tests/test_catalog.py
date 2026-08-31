import pytest

from usda_gnatsgo import catalog, config

from .conftest import TEST_REGION


def test_parse_release_date():
    assert catalog.parse_release_date("gNATSGO_gpkg_02_13_2026.7z") == "2026-02-13"
    assert catalog.parse_release_date("gNATSGO_01_30_2026.gpkg") == "2026-01-30"
    assert catalog.parse_release_date("MURASTER_30m_CONUS_2026.tif") is None  # bare year is not a date
    assert catalog.parse_release_date("foo_13_45_2026.gpkg") is None  # invalid month


def _make_release(tmp_path, dirname, gpkg_name, archive_name=None):
    directory = tmp_path / dirname
    directory.mkdir()
    (directory / gpkg_name).touch()
    (directory / TEST_REGION.muraster_filename).touch()
    if archive_name:
        (tmp_path / archive_name).touch()
    return tmp_path


def test_locate_release_from_gpkg_date(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2026-02-13")
    source = _make_release(tmp_path, "extracted", "gNATSGO_02_13_2026.gpkg")
    release = catalog.locate_release(source)
    assert release.release_date == "2026-02-13"
    assert release.release_date_source == "gNATSGO_02_13_2026.gpkg"
    assert release.warnings == ()


def test_misleading_directory_name_warns_but_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2026-02-13")
    source = _make_release(tmp_path, "gNATSGO_gpkg_01_30_2026", "gNATSGO_02_13_2026.gpkg")
    release = catalog.locate_release(source)
    assert release.release_date == "2026-02-13"
    assert len(release.warnings) == 1 and "not authoritative" in release.warnings[0]


def test_conflicting_archive_and_gpkg_dates_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2026-02-13")
    source = _make_release(tmp_path, "extracted", "gNATSGO_02_13_2026.gpkg", "gNATSGO_gpkg_03_01_2026.7z")
    with pytest.raises(ValueError, match="conflicting release dates"):
        catalog.locate_release(source)


def test_release_config_mismatch_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2027-01-01")
    source = _make_release(tmp_path, "extracted", "gNATSGO_02_13_2026.gpkg")
    with pytest.raises(ValueError, match=r"config\.RELEASE_DATE"):
        catalog.locate_release(source)


def test_missing_muraster_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2026-02-13")
    directory = tmp_path / "extracted"
    directory.mkdir()
    (directory / "gNATSGO_02_13_2026.gpkg").touch()
    with pytest.raises(FileNotFoundError, match="missing MURASTER"):
        catalog.locate_release(tmp_path)


def test_no_date_anywhere_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_DATE", "2026-02-13")
    source = _make_release(tmp_path, "extracted", "gNATSGO.gpkg")
    with pytest.raises(ValueError, match="no MM_DD_YYYY release date"):
        catalog.locate_release(source)
