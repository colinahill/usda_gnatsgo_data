import numpy as np
import polars as pl
import pytest

from usda_gnatsgo import config, lookup


def spec(name="droughty", dtype="uint8", fill=255):
    return next(v for v in config.VARIABLES if v.name == name)


def test_expand_background_and_unmatched():
    table = lookup.Lookup(
        sorted_mukeys=np.array([10, 20, 30], dtype="int64"),
        values=np.array([1, 2, 3], dtype="uint8"),
        fill_value=255,
    )
    raster = np.array([[0, 10, 20], [30, 99, 40]], dtype="uint32")
    values, unmatched = table.expand(raster)
    assert values.dtype == np.uint8  # no promotion
    np.testing.assert_array_equal(values, [[255, 1, 2], [3, 255, 255]])
    assert sorted(unmatched.tolist()) == [40, 99]


def test_expand_key_above_all_sorted_keys_guarded():
    # searchsorted position == len(sorted): must not index out of bounds
    table = lookup.Lookup(
        sorted_mukeys=np.array([10], dtype="int64"), values=np.array([7], dtype="uint16"), fill_value=65535
    )
    values, unmatched = table.expand(np.array([4_000_000_000], dtype="uint32"))
    assert values[0] == 65535 and unmatched.tolist() == [4_000_000_000]


def test_expand_float_nan_fill():
    table = lookup.Lookup(
        sorted_mukeys=np.array([10], dtype="int64"),
        values=np.array([1.5], dtype="float32"),
        fill_value=float("nan"),
    )
    values, _ = table.expand(np.array([10, 0], dtype="uint32"))
    assert values[0] == pytest.approx(1.5) and np.isnan(values[1])


def test_build_lookups_int_sentinel_and_rounding():
    frame = pl.DataFrame({"mukey": [3, 1, 2], "droughty": [None, 1.0, 0.4]})
    built = lookup.build_lookups(frame, spec("droughty"))
    np.testing.assert_array_equal(built.sorted_mukeys, [1, 2, 3])
    np.testing.assert_array_equal(built.values, np.array([1, 0, 255], dtype="uint8"))


def test_build_lookups_3d_depth_slice():
    frame = pl.DataFrame(
        {
            "mukey": [1, 1, 2],
            config.DEPTH_DIM: ["0_5", "0_20", "0_5"],
            "ph": [6.0, 6.5, 7.0],
        }
    )
    built = lookup.build_lookups(frame, spec("ph"), depth_label="0_5")
    np.testing.assert_array_equal(built.sorted_mukeys, [1, 2])
    np.testing.assert_allclose(built.values, [6.0, 7.0])


def test_build_lookups_rejects_out_of_range():
    frame = pl.DataFrame({"mukey": [1], "droughty": [300.0]})
    with pytest.raises(ValueError, match="out of uint8 range"):
        lookup.build_lookups(frame, spec("droughty"))


def test_build_lookups_rejects_duplicate_mukeys():
    frame = pl.DataFrame({"mukey": [1, 1], "droughty": [0.0, 1.0]})
    with pytest.raises(ValueError, match="strictly ascending"):
        lookup.build_lookups(frame, spec("droughty"))
