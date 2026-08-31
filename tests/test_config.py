import pydantic
import pytest

from usda_gnatsgo import config


def test_variable_names_unique():
    names = [v.name for v in config.VARIABLES]
    assert len(names) == len(set(names))


def test_included_variable_count():
    included = config.included_variables()
    # 22 soil_properties + 2 diagnostics + 19 map_unit_properties
    assert len(included) == 43
    assert sum(v.group == "soil_properties" for v in included) == 22
    assert sum(v.group == "soil_properties/diagnostics" for v in included) == 2
    assert sum(v.group == "map_unit_properties" for v in included) == 19


def test_variables_by_name_orders_mukey_first():
    chosen = config.variables_by_name(["sand", "mukey", "aws"])
    assert [v.name for v in chosen] == ["mukey", "aws", "sand"]
    assert config.variables_by_name(None)[0].name == "mukey"


def test_variables_by_name_rejects_unknown_and_deferred():
    with pytest.raises(ValueError, match="unknown or deferred"):
        config.variables_by_name(["nope"])
    with pytest.raises(ValueError, match="unknown or deferred"):
        config.variables_by_name(["texture_class_by_depth"])  # declared but deferred


def test_unknown_algorithm_rejected():
    with pytest.raises(pydantic.ValidationError, match="unknown algorithm_id"):
        config.VariableSpec(
            name="bad",
            group="map_unit_properties",
            dims=config.DIMS_2D,
            dtype="float32",
            fill_value=float("nan"),
            units="1",
            long_name="bad",
            source_table="component",
            source_fields=("x",),
            algorithm_id="not_a_thing",
        )


def test_included_variable_needs_reviewed_algorithm():
    with pytest.raises(pydantic.ValidationError, match="reviewed algorithm"):
        config.VariableSpec(
            name="bad",
            group="map_unit_properties",
            dims=config.DIMS_2D,
            dtype="float32",
            fill_value=float("nan"),
            units="1",
            long_name="bad",
            source_table="component",
            source_fields=("x",),
            algorithm_id="unreviewed",
            status="included",
        )


def test_depth_intervals():
    assert len(config.DEPTH_INTERVALS) == 11
    labels = [d.label for d in config.DEPTH_INTERVALS]
    assert labels[0] == "0_5" and labels[-1] == "0_profile"
    profile_ended = [d.label for d in config.DEPTH_INTERVALS if d.to_reported_profile_depth]
    assert profile_ended == ["150_profile", "0_profile"]
    assert {d.source_suffix for d in config.DEPTH_INTERVALS if d.to_reported_profile_depth} == {"150_999", "0_999"}


def test_region_coords(small_config):
    region = config.REGIONS["testregion"]
    x = region.x_coords()
    y = region.y_coords()
    assert x[0] == region.x_min + 15.0 and len(x) == region.width
    assert y[0] == region.y_max - 15.0 and y[1] < y[0]


def test_encoding_shapes():
    enc = config.EncodingSpec()
    assert enc.chunks(3) == (1, 128, 128) and enc.shards(3) == (1, 4096, 4096)
    assert enc.chunks(2) == (128, 128) and enc.shards(2) == (4096, 4096)
    # the shard is the storage object and the write/retry unit: 64 MiB raw float32
    assert enc.shard_y * enc.shard_x * 4 == 64 * 2**20
    assert enc.shard_y % enc.chunk_y == 0 and enc.shard_x % enc.chunk_x == 0
