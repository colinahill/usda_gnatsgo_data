import polars as pl
import pytest

from usda_gnatsgo import aggregate, config

from .conftest import components_frame, horizons_frame


@pytest.fixture
def overlaps():
    return aggregate.horizon_interval_overlaps(horizons_frame())


def _overlap(overlaps, cokey, label) -> dict[str, float]:
    rows = overlaps.filter((pl.col("cokey") == cokey) & (pl.col(config.DEPTH_DIM) == label))
    return dict(zip(rows["chkey"].to_list(), rows[aggregate.OVERLAP].to_list(), strict=True))


def test_overlaps_split_across_horizons(overlaps):
    assert _overlap(overlaps, "c1", "20_50") == {"h1": 10.0, "h2": 20.0}
    assert _overlap(overlaps, "c1", "0_100") == {"h1": 30.0, "h2": 70.0}


def test_shallow_profile_has_no_deep_overlap(overlaps):
    assert _overlap(overlaps, "c1", "100_150") == {}
    assert _overlap(overlaps, "c2", "150_profile") == {}


def test_profile_ended_uses_reported_bottom(overlaps):
    # c2's profile ends at 50 cm: 0_profile covers exactly 50 cm, never 999
    assert _overlap(overlaps, "c2", "0_profile") == {"h3": 50.0}
    assert _overlap(overlaps, "c1", "0_profile") == {"h1": 30.0, "h2": 70.0}


def test_invalid_horizon_depths_dropped():
    horizons = pl.DataFrame(
        {
            "cokey": ["cX", "cX"],
            "chkey": ["hA", "hB"],
            "hzdept_r": [0.0, None],
            "hzdepb_r": [0.0, 50.0],  # zero-thickness and missing top
        }
    )
    assert aggregate.horizon_interval_overlaps(horizons).height == 0


def _value(frame, mukey, label, column="value"):
    row = frame.filter((pl.col("mukey") == mukey) & (pl.col(config.DEPTH_DIM) == label))
    return row[column][0] if row.height else None


def test_linear_weighted_mean_hand_computed(overlaps):
    result = aggregate.linear_overlap_component_weighted_mean(overlaps, components_frame(), "om_r")
    # 0_20: c1 -> 3.0 (20 cm), c2 -> 2.0 (20 cm); (3*60 + 2*40)/100
    assert _value(result, 100, "0_20") == pytest.approx(2.6)
    assert _value(result, 100, "0_20", "contributing_thickness") == pytest.approx(20.0)
    assert _value(result, 100, "0_20", "contributing_component_percent") == pytest.approx(100.0)
    # 0_100: c1 -> (3*30 + 1*70)/100 = 1.6, c2 -> 2.0 over 50 cm
    assert _value(result, 100, "0_100") == pytest.approx((1.6 * 60 + 2.0 * 40) / 100)
    assert _value(result, 100, "0_100", "contributing_thickness") == pytest.approx((100 * 60 + 50 * 40) / 100)


def test_linear_mean_excludes_missing_values_not_zero_fills():
    components = pl.DataFrame(
        {"mukey": [1, 1], "cokey": ["a", "b"], "comppct_r": [50.0, 50.0], "majcompflag": ["Yes", "Yes"]}
    )
    horizons = pl.DataFrame(
        {
            "cokey": ["a", "b"],
            "chkey": ["ha", "hb"],
            "hzdept_r": [0.0, 0.0],
            "hzdepb_r": [100.0, 100.0],
            "om_r": [4.0, None],
        }
    )
    overlaps = aggregate.horizon_interval_overlaps(horizons)
    result = aggregate.linear_overlap_component_weighted_mean(overlaps, components, "om_r")
    # component b has no value: renormalize over a alone, and say so in the pct
    assert _value(result, 1, "0_100") == pytest.approx(4.0)
    assert _value(result, 1, "0_100", "contributing_component_percent") == pytest.approx(50.0)


def test_composition_shared_mask_closure(overlaps):
    result = aggregate.composition_shared_mask_weighted_mean(
        overlaps, components_frame(), ("sandtotal_r", "silttotal_r", "claytotal_r")
    )
    row = result.filter((pl.col("mukey") == 100) & (pl.col(config.DEPTH_DIM) == "0_20"))
    sand, silt, clay = row["sandtotal_r"][0], row["silttotal_r"][0], row["claytotal_r"][0]
    assert sand == pytest.approx(44.0)
    assert silt == pytest.approx(36.0)
    assert clay == pytest.approx(20.0)
    assert sand + silt + clay == pytest.approx(100.0)


def test_composition_shared_mask_requires_all_three():
    components = pl.DataFrame({"mukey": [1], "cokey": ["a"], "comppct_r": [100.0], "majcompflag": ["Yes"]})
    horizons = pl.DataFrame(
        {
            "cokey": ["a"],
            "chkey": ["ha"],
            "hzdept_r": [0.0],
            "hzdepb_r": [100.0],
            "sandtotal_r": [40.0],
            "silttotal_r": [40.0],
            "claytotal_r": [None],
        }
    ).with_columns(pl.col("claytotal_r").cast(pl.Float64))
    overlaps = aggregate.horizon_interval_overlaps(horizons)
    result = aggregate.composition_shared_mask_weighted_mean(
        overlaps, components, ("sandtotal_r", "silttotal_r", "claytotal_r")
    )
    assert result.height == 0  # missing clay excludes the horizon from ALL three


def test_dominant_component_prefers_major_then_pct_then_cokey():
    components = pl.DataFrame(
        {
            "mukey": [1, 1, 2, 2],
            "cokey": ["b", "a", "z", "y"],
            "comppct_r": [80.0, 20.0, 50.0, 50.0],
            "majcompflag": ["No", "Yes", "Yes", "Yes"],
        }
    )
    dominant = aggregate.dominant_component(components).sort("mukey")
    # mukey 1: the major 20% component beats the non-major 80% one
    assert dominant["cokey"].to_list() == ["a", "y"]  # mukey 2: tie -> ascending cokey


def test_least_transmissive_ksat(overlaps):
    result = aggregate.dominant_component_least_transmissive(overlaps, components_frame(), "ksat_r")
    # dominant component of mukey 100 is c1 (60%); min over 0_100 of (10, 2)
    assert _value(result, 100, "0_100") == pytest.approx(2.0)
    assert _value(result, 100, "0_5") == pytest.approx(10.0)  # only h1 in 0_5
    assert _value(result, 100, "0_100", "contributing_component_percent") == pytest.approx(60.0)
