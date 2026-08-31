import polars as pl
import pytest

from usda_gnatsgo import mapunit, metadata

from .conftest import chtexturegrp_frame, components_frame, horizons_frame


def test_terrain_uses_dominant_component():
    values, audit = mapunit.terrain(components_frame())
    row = values.filter(pl.col("mukey") == 100).to_dicts()[0]
    assert row["slope"] == pytest.approx(5.0)  # c1 (60%) wins over c2 (40%)
    assert row["aspect"] == pytest.approx(180.0)
    audit_row = audit.filter(pl.col("mukey") == 100).to_dicts()[0]
    assert audit_row["selected_cokey"] == "c1"
    assert audit_row["selected_comppct_r"] == pytest.approx(60.0)


def test_terrain_missing_values_stay_null():
    values, _ = mapunit.terrain(components_frame())
    row = values.filter(pl.col("mukey") == 200).to_dicts()[0]
    assert row["slope"] is None and row["elevation"] is None


def test_surface_texture_dominant_component_surface_horizon():
    texture = mapunit.surface_texture(components_frame(), horizons_frame(), chtexturegrp_frame())
    by_mukey = {row["mukey"]: row["texture"] for row in texture.to_dicts()}
    assert by_mukey[100] == "L"  # c1's surface horizon h1, not h2 (deeper) or c2
    assert by_mukey[300] == "STV-L"
    assert by_mukey[200] is None  # water component has no horizons


def test_surface_texture_rv_only():
    grp = chtexturegrp_frame().with_columns(
        pl.when(pl.col("chkey") == "h1").then(pl.lit("No")).otherwise(pl.col("rvindicator")).alias("rvindicator")
    )
    texture = mapunit.surface_texture(components_frame(), horizons_frame(), grp)
    assert texture.filter(pl.col("mukey") == 100)["texture"][0] is None


def test_texture_class_table_and_attrs():
    classes = metadata.texture_class_table([("SL", "Sandy loam"), ("L", "Loam"), ("SL", "dup ignored")])
    assert classes == {0: {"name": "L", "description": "Loam"}, 1: {"name": "SL", "description": "Sandy loam"}}
    attrs = metadata.texture_class_attrs(classes)
    assert attrs["flag_values"] == [0, 1]
    assert attrs["class_names"] == {"0": "L", "1": "SL"}
    assert attrs["flag_meanings"] == "l sl"
