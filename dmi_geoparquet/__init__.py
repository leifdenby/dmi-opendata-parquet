"""DMI GeoParquet utilities.

This package contains the cache builder, partition estimator, and xarray
reader for DMI weather-station data.
"""

from dmi_geoparquet.xarray_reader import open_dataset

__all__ = ["open_dataset"]
