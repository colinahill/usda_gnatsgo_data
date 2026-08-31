"""Shared fixtures: a shrunken region on the real CONUS lattice, a tiny
synthetic MURASTER, and a hand-computable relational fixture.

Tests run at toy scale (100x80 px, 16-px chunks, 32-px shards) while keeping
multi-chunk/multi-shard code paths hot.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
import rasterio
import rasterio.transform

from usda_gnatsgo import config

TEST_REGION = config.RegionSpec(
    name="testregion",
    original_token="TestRegion",
    muraster_filename="muraster_30m_TestRegion.tif",
    saraster_filename="SARASTER_30m_TestRegion.tif",
    epsg=5070,
    pixel_size=30.0,
    width=100,
    height=80,
    x_min=-2_356_125.0,
    y_max=3_172_575.0,
)
TEST_ENCODING = config.EncodingSpec(chunk_y=16, chunk_x=16, shard_y=32, shard_x=32)

# mukeys present in the synthetic raster; 999 is deliberately absent from the
# relational fixture to exercise the unmatched-key path.
MUKEYS = (100, 200, 300, 400)
UNMATCHED_MUKEY = 999


@pytest.fixture(autouse=True)
def small_config(monkeypatch):
    monkeypatch.setattr(config, "REGIONS", {TEST_REGION.name: TEST_REGION})
    monkeypatch.setattr(config, "ENCODING", TEST_ENCODING)


def synthetic_mukey_array() -> np.ndarray:
    """80x100 uint32 quadrants of the four fixture mukeys, a background border,
    and one unmatched key."""
    data = np.zeros((80, 100), dtype="uint32")
    data[2:40, 2:50] = 100
    data[2:40, 50:98] = 200
    data[40:78, 2:50] = 300
    data[40:78, 50:98] = 400
    data[10, 10] = UNMATCHED_MUKEY
    return data


def write_synthetic_muraster(directory) -> np.ndarray:
    data = synthetic_mukey_array()
    transform = rasterio.transform.from_origin(TEST_REGION.x_min, TEST_REGION.y_max, 30.0, 30.0)
    path = directory / TEST_REGION.muraster_filename
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=TEST_REGION.width,
        height=TEST_REGION.height,
        count=1,
        dtype="uint32",
        crs=rasterio.crs.CRS.from_epsg(TEST_REGION.epsg),
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(data, 1)
    return data


@pytest.fixture
def synthetic_muraster(tmp_path):
    return tmp_path, write_synthetic_muraster(tmp_path)


def _horizon(cokey, chkey, top, bottom, **props) -> dict:
    base = {
        "cokey": cokey,
        "chkey": chkey,
        "hzdept_r": float(top),
        "hzdepb_r": float(bottom),
        "sandtotal_r": None,
        "silttotal_r": None,
        "claytotal_r": None,
        "om_r": None,
        "dbtenthbar_r": None,
        "dbthirdbar_r": None,
        "dbfifteenbar_r": None,
        "dbovendry_r": None,
        "ksat_r": None,
        "awc_r": None,
        "wtenthbar_r": None,
        "wthirdbar_r": None,
        "wfifteenbar_r": None,
        "wsatiated_r": None,
        "cec7_r": None,
        "ecec_r": None,
        "sumbases_r": None,
        "ph1to1h2o_r": None,
        "ec_r": None,
    }
    base.update(props)
    return base


def components_frame() -> pl.DataFrame:
    rows = [
        # mukey 100: two major series components 60/40
        dict(
            mukey=100,
            cokey="c1",
            comppct_r=60.0,
            compname="Alpha",
            compkind="Series",
            majcompflag="Yes",
            otherph=None,
            localphase=None,
            hydricrating="No",
            drainagecl="Well drained",
            slope_r=5.0,
            slopelenusle_r=50.0,
            elev_r=250.0,
            aspectrep=180.0,
        ),
        dict(
            mukey=100,
            cokey="c2",
            comppct_r=40.0,
            compname="Beta",
            compkind="Series",
            majcompflag="Yes",
            otherph=None,
            localphase=None,
            hydricrating="Yes",
            drainagecl="Poorly drained",
            slope_r=2.0,
            slopelenusle_r=30.0,
            elev_r=240.0,
            aspectrep=90.0,
        ),
        # mukey 200: a water map unit (miscellaneous area)
        dict(
            mukey=200,
            cokey="c3",
            comppct_r=85.0,
            compname="Water",
            compkind="Miscellaneous area",
            majcompflag="Yes",
            otherph=None,
            localphase=None,
            hydricrating=None,
            drainagecl=None,
            slope_r=None,
            slopelenusle_r=None,
            elev_r=None,
            aspectrep=None,
        ),
        # mukey 300: restricted profile (lithic bedrock at 50 cm)
        dict(
            mukey=300,
            cokey="c4",
            comppct_r=100.0,
            compname="Gamma",
            compkind="Series",
            majcompflag="Yes",
            otherph=None,
            localphase=None,
            hydricrating="No",
            drainagecl="Well drained",
            slope_r=12.0,
            slopelenusle_r=80.0,
            elev_r=500.0,
            aspectrep=270.0,
        ),
        # mukey 400: deep unrestricted profile (droughty = 0)
        dict(
            mukey=400,
            cokey="c5",
            comppct_r=100.0,
            compname="Delta",
            compkind="Series",
            majcompflag="Yes",
            otherph=None,
            localphase=None,
            hydricrating="Unranked",
            drainagecl="Very poorly drained",
            slope_r=1.0,
            slopelenusle_r=100.0,
            elev_r=100.0,
            aspectrep=0.0,
        ),
    ]
    return pl.DataFrame(rows).with_columns(pl.col("mukey").cast(pl.Int64))


def horizons_frame() -> pl.DataFrame:
    rows = [
        _horizon(
            "c1",
            "h1",
            0,
            30,
            sandtotal_r=40.0,
            silttotal_r=40.0,
            claytotal_r=20.0,
            om_r=3.0,
            dbtenthbar_r=1.1,
            dbthirdbar_r=1.2,
            dbfifteenbar_r=1.3,
            dbovendry_r=1.5,
            ksat_r=10.0,
            awc_r=0.15,
            wtenthbar_r=30.0,
            wthirdbar_r=25.0,
            wfifteenbar_r=10.0,
            wsatiated_r=45.0,
            cec7_r=15.0,
            ecec_r=12.0,
            sumbases_r=10.0,
            ph1to1h2o_r=6.0,
            ec_r=0.5,
        ),
        _horizon(
            "c1",
            "h2",
            30,
            100,
            sandtotal_r=30.0,
            silttotal_r=45.0,
            claytotal_r=25.0,
            om_r=1.0,
            dbtenthbar_r=1.2,
            dbthirdbar_r=1.4,
            dbfifteenbar_r=1.5,
            dbovendry_r=1.6,
            ksat_r=2.0,
            awc_r=0.10,
            wtenthbar_r=28.0,
            wthirdbar_r=24.0,
            wfifteenbar_r=12.0,
            wsatiated_r=40.0,
            cec7_r=12.0,
            ecec_r=10.0,
            sumbases_r=8.0,
            ph1to1h2o_r=6.5,
            ec_r=0.5,
        ),
        _horizon(
            "c2",
            "h3",
            0,
            50,
            sandtotal_r=50.0,
            silttotal_r=30.0,
            claytotal_r=20.0,
            om_r=2.0,
            dbtenthbar_r=1.2,
            dbthirdbar_r=1.3,
            dbfifteenbar_r=1.4,
            dbovendry_r=1.55,
            ksat_r=20.0,
            awc_r=0.12,
            wtenthbar_r=27.0,
            wthirdbar_r=22.0,
            wfifteenbar_r=9.0,
            wsatiated_r=42.0,
            cec7_r=10.0,
            ecec_r=9.0,
            sumbases_r=7.0,
            ph1to1h2o_r=7.0,
            ec_r=0.4,
        ),
        _horizon(
            "c4",
            "h4",
            0,
            50,
            sandtotal_r=35.0,
            silttotal_r=40.0,
            claytotal_r=25.0,
            om_r=1.5,
            dbthirdbar_r=1.35,
            ksat_r=5.0,
            awc_r=0.20,
            ph1to1h2o_r=6.8,
            ec_r=0.3,
        ),
        _horizon(
            "c5",
            "h5",
            0,
            150,
            sandtotal_r=45.0,
            silttotal_r=35.0,
            claytotal_r=20.0,
            om_r=2.5,
            dbthirdbar_r=1.25,
            ksat_r=8.0,
            awc_r=0.15,
            ph1to1h2o_r=6.2,
            ec_r=0.6,
        ),
    ]
    return pl.DataFrame(rows)


def chfrags_frame() -> pl.DataFrame:
    return pl.DataFrame({"chkey": ["h1"], "fragvol_r": [10.0]})


def corestrictions_frame() -> pl.DataFrame:
    return pl.DataFrame({"cokey": ["c4"], "reskind": ["Lithic bedrock"], "resdept_r": [50.0]})


def cointerp_frame() -> pl.DataFrame:
    main = "NCCPI - National Commodity Crop Productivity Index (Ver 3.0)"
    rows = [
        dict(cokey="c1", mrulename=main, rulename="NCCPI - NCCPI Corn Submodel (II)", ruledepth=1, interphr=0.8),
        dict(cokey="c1", mrulename=main, rulename="NCCPI - NCCPI Soybeans Submodel (II)", ruledepth=1, interphr=0.6),
        dict(cokey="c2", mrulename=main, rulename="NCCPI - NCCPI Corn Submodel (II)", ruledepth=1, interphr=0.5),
        # the overall rule (no crop token) and Irrigated variants must be
        # ignored by the crop matching
        dict(cokey="c1", mrulename=main, rulename=main, ruledepth=0, interphr=0.9),
        dict(
            cokey="c1",
            mrulename="NCCPI - Irrigated Corn Submodel (hypothetical)",
            rulename="NCCPI - Irrigated Corn Submodel (hypothetical)",
            ruledepth=0,
            interphr=0.99,
        ),
    ]
    return pl.DataFrame(rows).with_columns(pl.col("ruledepth").cast(pl.Int64))


def mapunit_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "mukey": [100, 200, 300, 400],
            "muname": ["Alpha-Beta complex", "Water", "Gamma stony loam", "Delta muck, ponded"],
            "mukind": ["Complex", "Miscellaneous area", "Consociation", "Consociation"],
        }
    ).with_columns(pl.col("mukey").cast(pl.Int64))


def chtexturegrp_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "chkey": ["h1", "h2", "h3", "h4", "h5"],
            "chtgkey": ["t1", "t2", "t3", "t4", "t5"],
            "texture": ["L", "CL", "SL", "STV-L", "MUCK"],
            "texdesc": ["Loam", "Clay loam", "Sandy loam", "Very stony loam", "Muck"],
            "rvindicator": ["Yes", "Yes", "Yes", "Yes", "Yes"],
        }
    )


@pytest.fixture
def relational_fixture():
    return {
        "component": components_frame(),
        "chorizon": horizons_frame(),
        "chfrags": chfrags_frame(),
        "corestrictions": corestrictions_frame(),
        "cointerp": cointerp_frame(),
        "mapunit": mapunit_frame(),
        "chtexturegrp": chtexturegrp_frame(),
    }


@pytest.fixture
def extracted_dir(tmp_path, relational_fixture):
    """The relational fixture written as work/{release}/extracted/*.parquet."""
    out = tmp_path / "work" / config.RELEASE_DATE / "extracted"
    out.mkdir(parents=True)
    for name, frame in relational_fixture.items():
        frame.write_parquet(out / f"{name}.parquet")
    return out
