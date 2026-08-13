"""DMI GeoParquet utilities.

This package contains the estimate, download, build, and xarray reader stages
for DMI weather-station data.
"""

from .download.xarray_reader import open_dataset

__all__ = ["open_dataset"]
