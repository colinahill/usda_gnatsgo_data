import polars as pl
import pytest

from usda_gnatsgo import aggregate, valu1

from .conftest import (
    chfrags_frame,
    cointerp_frame,
    components_frame,
    corestrictions_frame,
    horizons_frame,
    mapunit_frame,
)


@pytest.fixture(scope="module")
def frame() -> pl.DataFrame:
    return valu1.build_valu1(
        components_frame(),
        horizons_frame(),
        chfrags_frame(),
        corestrictions_frame(),
        cointerp_frame(),
        mapunit_frame(),
    )


def _row(frame, mukey) -> dict:
    return frame.filter(pl.col("mukey") == mukey).to_dicts()[0]


def test_all_57_columns_present(frame):
    assert len(valu1.VALU1_COLUMNS) == 57
    assert frame.columns == ["mukey", *valu1.VALU1_COLUMNS]
    assert frame["mukey"].to_list() == [100, 200, 300, 400]  # sorted, one row per mukey


def test_aws_hand_computed(frame):
    row = _row(frame, 100)
    # c1: 0.15*30*10 + 0.10*70*10 = 115 mm over 0_100; c2: 0.12*50*10 = 60 mm
    assert row["aws0_100"] == pytest.approx((115 * 60 + 60 * 40) / 100)
    assert row["tk0_100a"] == pytest.approx((100 * 60 + 50 * 40) / 100)
    assert row["musumcpcta"] == pytest.approx(100.0)
    # 0_5: c1 7.5 mm, c2 6 mm
    assert row["aws0_5"] == pytest.approx((7.5 * 60 + 6.0 * 40) / 100)
    # both profiles end above 100 cm: no data deeper
    assert row["aws100_150"] is None
    assert row["aws150_999"] is None


def test_aws_water_mapunit_is_null(frame):
    row = _row(frame, 200)
    assert row["aws0_100"] is None and row["musumcpcta"] is None


def test_soc_hand_computed(frame):
    row = _row(frame, 100)
    # c1 h1 (0-5): 5 * 1.2 * (3/1.724/100) * (1 - 0.10) * 10000
    c1 = 5 * 1.2 * (3.0 / valu1.OM_TO_OC / 100) * 0.9 * 10_000
    # c2 h3 (0-5): 5 * 1.3 * (2/1.724/100) * 10000 (no fragments)
    c2 = 5 * 1.3 * (2.0 / valu1.OM_TO_OC / 100) * 10_000
    assert row["soc0_5"] == pytest.approx((c1 * 60 + c2 * 40) / 100, rel=1e-6)
    assert row["musumcpcts"] == pytest.approx(100.0)


def test_root_zone_restriction_and_cap(frame):
    # mukey 300: lithic bedrock at 50 cm -> depth 50, rzaws = 0.2*50*10 = 100 mm
    row = _row(frame, 300)
    assert row["rootznemc"] == pytest.approx(50.0)
    assert row["rootznaws"] == pytest.approx(100.0)
    assert row["droughty"] == 1  # 100 <= 152

    # mukey 400: unrestricted -> 150 cm default, rzaws = 0.15*150*10 = 225 mm
    row = _row(frame, 400)
    assert row["rootznemc"] == pytest.approx(150.0)
    assert row["rootznaws"] == pytest.approx(225.0)
    assert row["droughty"] == 0

    # mukey 200: no earthy components -> everything missing
    row = _row(frame, 200)
    assert row["rootznemc"] is None and row["droughty"] is None


def test_root_zone_horizon_criteria():
    components = pl.DataFrame(
        {
            "mukey": [1],
            "cokey": ["a"],
            "comppct_r": [100.0],
            "compname": ["X"],
            "compkind": ["Series"],
            "majcompflag": ["Yes"],
        }
    )
    horizons = pl.DataFrame(
        {
            "cokey": ["a", "a"],
            "chkey": ["h1", "h2"],
            "hzdept_r": [0.0, 60.0],
            "hzdepb_r": [60.0, 200.0],
            "awc_r": [0.10, 0.10],
            "ph1to1h2o_r": [6.0, 3.0],  # pH < 3.5 from 60 cm down
            "ec_r": [0.5, 0.5],
        }
    )
    overlaps = aggregate.horizon_interval_overlaps(horizons)
    empty_restrictions = pl.DataFrame(schema={"cokey": pl.String, "reskind": pl.String, "resdept_r": pl.Float64})
    result = valu1.root_zone(overlaps, components, empty_restrictions)
    assert result["rootznemc"][0] == pytest.approx(60.0)
    assert result["rootznaws"][0] == pytest.approx(60.0)  # 0.10 * 60 * 10


def test_nccpi_hand_computed(frame):
    row = _row(frame, 100)
    assert row["nccpi3corn"] == pytest.approx((0.8 * 60 + 0.5 * 40) / 100)
    assert row["nccpi3soy"] == pytest.approx(0.6)  # only c1 rated; renormalized over 60%
    assert row["nccpi3cot"] is None and row["nccpi3sg"] is None
    # c1 has an overall-rule rating (0.9); c2 falls back to its crop max (0.5)
    assert row["nccpi3all"] == pytest.approx((0.9 * 60 + 0.5 * 40) / 100)
    assert row["pctearthmc"] == pytest.approx(100.0)
    assert _row(frame, 200)["pctearthmc"] is None  # miscellaneous area is not earthy


def test_pwsl(frame):
    assert _row(frame, 100)["pwsl1pomu"] == 40  # c2 hydricrating Yes
    assert _row(frame, 200)["pwsl1pomu"] == 999  # muname 'Water'
    assert _row(frame, 300)["pwsl1pomu"] == 0  # nothing hydric
    assert _row(frame, 400)["pwsl1pomu"] == 100  # unranked + very poorly drained


def test_musumcpct_not_clipped(frame):
    assert _row(frame, 100)["musumcpct"] == pytest.approx(100.0)
    assert _row(frame, 200)["musumcpct"] == pytest.approx(85.0)
