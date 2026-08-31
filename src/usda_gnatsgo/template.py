"""Create the empty store structure: groups, coords, attrs, and arrays.

Only metadata and small coordinate arrays are written; data arrays are created
empty (fill-value chunks occupy no storage) and filled per (region, variable)
by rasterize. init is idempotent and ADDITIVE: on an existing store it creates
only what is missing, so flipping a variable's status to 'included' extends a
published store without touching existing data.
"""

from __future__ import annotations

import logging

import numpy as np
import xarray as xr
import zarr
from icechunk import Session
from icechunk.xarray import to_icechunk

from . import config, metadata, rasterize

log = logging.getLogger(__name__)

GROUPS = ("soil_properties", "map_unit_properties", "soil_properties/diagnostics")


def coords_dataset(region: config.RegionSpec, *, with_depth: bool) -> xr.Dataset:
    coord_attrs = metadata.coordinate_attrs(region)
    coords: dict = {
        "y": ("y", region.y_coords(), coord_attrs["y"]),
        "x": ("x", region.x_coords(), coord_attrs["x"]),
        "spatial_ref": ((), np.int64(0), metadata.spatial_ref_attrs(region)),
    }
    if with_depth:
        coords.update(metadata.depth_coords())
    return xr.Dataset(coords=coords)


def _group_exists(session: Session, path: str) -> bool:
    try:
        zarr.open_group(session.store, path=path, mode="r")
        return True
    except (KeyError, FileNotFoundError):
        return False


def init_region(
    session: Session,
    region: config.RegionSpec,
    variables: list[config.VariableSpec] | None = None,
    *,
    texture_classes: dict | None = None,
) -> list[str]:
    """Create the region's groups/coords (if absent) and any missing arrays for
    the given (default: all included) variables. Returns created array paths."""
    variables = variables if variables is not None else config.included_variables()

    if not _group_exists(session, region.name):
        root = zarr.open_group(session.store, path=region.name, mode="a")
        root.attrs.update(config.region_attrs(region))
    for group_path in GROUPS:
        full = f"{region.name}/{group_path}"
        if not _group_exists(session, full):
            with_depth = group_path.startswith("soil_properties")
            to_icechunk(coords_dataset(region, with_depth=with_depth), session, group=full, mode="w")

    created = []
    for spec in variables:
        group = zarr.open_group(session.store, path=f"{region.name}/{spec.group}", mode="r+")
        if spec.name in group.array_keys():
            # additive init must never leave a store mixing two shard grids
            rasterize.verify_array_encoding(group[spec.name], spec)
            continue
        shape = (
            (len(config.DEPTH_LABELS), region.height, region.width)
            if spec.dims == config.DIMS_3D
            else (
                region.height,
                region.width,
            )
        )
        extra_attrs = None
        if spec.name == "texture_class" and texture_classes is not None:
            extra_attrs = metadata.texture_class_attrs({int(code): info for code, info in texture_classes.items()})
        group.create_array(
            spec.name,
            shape=shape,
            chunks=config.ENCODING.chunks(len(shape)),
            shards=config.ENCODING.shards(len(shape)),
            dtype=spec.dtype,
            fill_value=spec.fill_value,
            compressors=[zarr.codecs.ZstdCodec(level=config.ENCODING.zstd_level)],
            dimension_names=spec.dims,
            attributes=metadata.variable_attrs(spec, extra_attrs),
        )
        created.append(f"{region.name}/{spec.array_path}")
    if created:
        log.info("%s: created %d arrays", region.name, len(created))
    return created


def init_store(
    session: Session,
    regions: list[str] | None = None,
    variables: list[config.VariableSpec] | None = None,
    *,
    texture_classes: dict | None = None,
) -> list[str]:
    """Create/extend the full structure (root attrs + every region). No commit."""
    created = []
    for name in regions or sorted(config.REGIONS):
        created.extend(init_region(session, config.REGIONS[name], variables, texture_classes=texture_classes))
    root = zarr.open_group(session.store, mode="a")
    root.attrs.update(config.ROOT_ATTRS)
    return created
