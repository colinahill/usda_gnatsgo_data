"""CF attribute construction: coordinates, CRS, variables, texture classes."""

from __future__ import annotations

import re

import numpy as np

from . import config
from .config import DEPTH_INTERVALS, RegionSpec, VariableSpec


def spatial_ref_attrs(region: RegionSpec) -> dict:
    """CF grid-mapping attrs for the scalar spatial_ref variable (rioxarray style)."""
    from pyproj import CRS

    crs = CRS.from_epsg(region.epsg)
    attrs = crs.to_cf()
    attrs["spatial_ref"] = attrs.get("crs_wkt", crs.to_wkt())  # rioxarray compatibility
    attrs["GeoTransform"] = region.geotransform
    return attrs


def coordinate_attrs(region: RegionSpec) -> dict[str, dict]:
    geographic = region.epsg == 4326
    return {
        "x": {
            "standard_name": "longitude" if geographic else "projection_x_coordinate",
            "long_name": ("longitude" if geographic else "x coordinate of projection") + " (pixel centre)",
            "units": "degrees_east" if geographic else "m",
            "axis": "X",
        },
        "y": {
            "standard_name": "latitude" if geographic else "projection_y_coordinate",
            "long_name": ("latitude" if geographic else "y coordinate of projection") + " (pixel centre)",
            "units": "degrees_north" if geographic else "m",
            "axis": "Y",
        },
    }


def depth_coords() -> dict[str, tuple[tuple[str, ...], np.ndarray, dict]]:
    """The string depth_interval coordinate plus its auxiliary coordinates.

    Returned as {name: (dims, values, attrs)} ready for xr.Dataset(coords=...).
    """
    dim = (config.DEPTH_DIM,)
    labels = np.array([d.label for d in DEPTH_INTERVALS])
    top = np.array([d.top_cm for d in DEPTH_INTERVALS], dtype="float64")
    bottom = np.array(
        [np.nan if d.bottom_cm is None else d.bottom_cm for d in DEPTH_INTERVALS],
        dtype="float64",
    )
    kind = np.array([d.kind for d in DEPTH_INTERVALS])
    profile_ended = np.array([d.to_reported_profile_depth for d in DEPTH_INTERVALS])
    suffix = np.array([d.source_suffix for d in DEPTH_INTERVALS])
    return {
        config.DEPTH_DIM: (
            dim,
            labels,
            {
                "long_name": "soil depth interval (mixed disjoint layers and cumulative zones)",
                "comment": (
                    "Labels are '{top}_{bottom}' in cm; '_profile' means to the reported depth of "
                    "the soil profile (an open bottom), NOT a literal bottom depth. See depth_kind, "
                    "depth_top_cm, depth_bottom_cm, to_reported_profile_depth, source_suffix."
                ),
            },
        ),
        "depth_top_cm": (dim, top, {"long_name": "interval top", "units": "cm"}),
        "depth_bottom_cm": (
            dim,
            bottom,
            {"long_name": "interval bottom (NaN = to the reported profile depth)", "units": "cm"},
        ),
        "depth_kind": (dim, kind, {"long_name": "interval kind: disjoint layer, cumulative zone, or both"}),
        "to_reported_profile_depth": (
            dim,
            profile_ended,
            {"long_name": "interval bottom is the reported depth of the soil profile"},
        ),
        "source_suffix": (
            dim,
            suffix,
            {"long_name": "suffix used by the original Valu1 source columns (999 = profile-ended)"},
        ),
    }


def variable_attrs(spec: VariableSpec, extra: dict | None = None) -> dict:
    """Zarr array attrs for one variable, built from its spec."""
    attrs: dict = {
        "long_name": spec.long_name,
        "units": spec.units,
        "source_table": spec.source_table,
        "source_fields": list(spec.source_fields),
        "source_estimate": "representative",
        "source_release": config.RELEASE_DATE,
        "aggregation_algorithm_id": spec.algorithm_id,
        "aggregation_algorithm_version": spec.algorithm_version,
        "aggregation_method": config.KNOWN_ALGORITHMS[spec.algorithm_id],
        "component_scope": spec.component_scope,
        "zarr_fill_value": None
        if isinstance(spec.fill_value, float) and np.isnan(spec.fill_value)
        else spec.fill_value,
        "missing_value_semantics": spec.missing_value_semantics,
        # NOTE: deliberately no CF missing_value/_FillValue attr - on integer
        # arrays it would make xarray mask the sentinel and upcast to float.
        "grid_mapping": "spatial_ref",
        "coordinates": "spatial_ref",
    }
    if isinstance(spec.fill_value, float) and np.isnan(spec.fill_value):
        attrs["zarr_fill_value"] = "NaN"
    if spec.valid_range is not None:
        attrs["valid_range_note"] = list(spec.valid_range)  # informational; not CF valid_range (no masking)
    if spec.comment:
        attrs["comment"] = spec.comment
    if spec.dims == config.DIMS_3D:
        attrs["depth_interval_source_suffixes"] = {d.label: d.source_suffix for d in DEPTH_INTERVALS}
    if extra:
        attrs.update(extra)
    return attrs


def _cf_sanitize(name: str) -> str:
    """CF flag_meanings tokens: lowercase, blank-separated, no special chars."""
    token = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    return token or "unknown"


def texture_class_table(textures: list[tuple[str, str]]) -> dict[int, dict[str, str]]:
    """Stable integer codes for the release's distinct RV texture groups.

    ``textures`` is [(texture, texdesc), ...]; codes are assigned by sorted
    distinct texture string so the mapping is deterministic for a release.
    """
    by_texture: dict[str, str] = {}
    for texture, texdesc in textures:
        if texture and texture not in by_texture:
            by_texture[texture] = texdesc or texture
    return {code: {"name": tex, "description": by_texture[tex]} for code, tex in enumerate(sorted(by_texture))}


def texture_class_attrs(classes: dict[int, dict[str, str]]) -> dict:
    """CF categorical attrs for texture_class (codes -> names in attrs, not pixels)."""
    codes = sorted(classes)
    return {
        "flag_values": codes,
        "flag_meanings": " ".join(_cf_sanitize(classes[c]["name"]) for c in codes),
        "class_names": {str(c): classes[c]["name"] for c in codes},
        "class_descriptions": {str(c): classes[c]["description"] for c in codes},
    }
