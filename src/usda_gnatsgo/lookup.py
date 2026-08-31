"""Sorted-mukey lookup construction and vectorized pixel expansion.

The rasterization hot path: raster mukey windows are mapped to per-variable
values with numpy searchsorted. Guards the out-of-bounds position case, keeps
integer sentinels without dtype promotion, and reports unmatched nonzero keys
instead of silently filling them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from . import config


@dataclass(frozen=True)
class Lookup:
    """One variable's (or one depth slice's) value per sorted mukey."""

    sorted_mukeys: np.ndarray  # int64, strictly ascending
    values: np.ndarray  # target dtype; sentinel/NaN already substituted for nulls
    fill_value: float | int

    def expand(self, raster_mukeys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Map a window of raster mukeys to values.

        Returns (values array shaped like the input, unmatched nonzero keys).
        Background (0) and unmatched keys become the fill value.
        """
        flat = raster_mukeys.ravel().astype("int64", copy=False)
        positions = np.searchsorted(self.sorted_mukeys, flat)

        candidate = (flat != config.NODATA_MUKEY) & (positions < self.sorted_mukeys.size)
        matched = np.zeros(flat.shape, dtype=bool)
        matched[candidate] = self.sorted_mukeys[positions[candidate]] == flat[candidate]

        output = np.full(flat.shape, self.fill_value, dtype=self.values.dtype)
        output[matched] = self.values[positions[matched]]

        unmatched = np.unique(flat[(flat != config.NODATA_MUKEY) & ~matched])
        return output.reshape(raster_mukeys.shape), unmatched


def _column_to_values(frame: pl.DataFrame, column: str, dtype: str, fill_value: float | int) -> np.ndarray:
    """A value column as the target dtype with nulls replaced by the sentinel,
    without float promotion of integer arrays."""
    series = frame[column]
    if np.issubdtype(np.dtype(dtype), np.floating):
        return series.cast(pl.Float64).fill_null(float("nan")).to_numpy().astype(dtype, copy=False)
    if series.dtype.is_float():
        # round before integer cast; nulls (and NaN) -> sentinel
        series = series.round(0)
    values = series.cast(pl.Int64, strict=False).fill_null(int(fill_value)).to_numpy()
    info = np.iinfo(np.dtype(dtype))
    out_of_range = (values < info.min) | (values > info.max)
    if out_of_range.any():
        bad = np.unique(values[out_of_range])[:5]
        raise ValueError(f"{column}: values out of {dtype} range, e.g. {bad}")
    return values.astype(dtype, copy=False)


def build_lookups(frame: pl.DataFrame, spec: config.VariableSpec, *, depth_label: str | None = None) -> Lookup:
    """A Lookup for one variable (2-D) or one depth slice of a 3-D variable.

    ``frame`` is the derived table: map_unit_properties (keyed mukey) or
    soil_properties (keyed mukey + depth_interval, filtered here).
    """
    if depth_label is not None:
        frame = frame.filter(pl.col(config.DEPTH_DIM) == depth_label)
    frame = frame.filter(pl.col("mukey").is_not_null()).sort("mukey")
    mukeys = frame["mukey"].to_numpy().astype("int64", copy=False)
    if mukeys.size > 1 and not (np.diff(mukeys) > 0).all():
        raise ValueError(f"{spec.name}: mukeys are not strictly ascending/unique")
    values = _column_to_values(frame, spec.name, spec.dtype, spec.fill_value)
    return Lookup(sorted_mukeys=mukeys, values=values, fill_value=spec.fill_value)
