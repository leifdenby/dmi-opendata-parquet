"""DMI GeoParquet utilities.

This package contains the estimate, download, build, and xarray reader stages
for DMI weather-station data.
"""

from dmi_geoparquet.xarray_reader import open_dataset

__all__ = ["open_dataset"]
