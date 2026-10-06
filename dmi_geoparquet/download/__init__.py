"""Download-stage helpers for building the GeoParquet cache."""

from __future__ import annotations

from .cache import CacheSlice as CacheSlice
from .cache import build_cache as build_cache
from .cache import cache_slice_path as cache_slice_path
from .cache import iter_cache_slices as iter_cache_slices
from .plotting import plot_cache_timeseries as plot_cache_timeseries
from .xarray_reader import open_dataset as open_dataset
