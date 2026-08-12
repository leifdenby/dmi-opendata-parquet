"""Read cached DMI GeoParquet files into xarray datasets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import xarray as xr


@dataclass(frozen=True)
class CfVariableSpec:
    """Map a DMI parameter to a CF-style xarray variable.

    Parameters
    ----------
    source_parameter_id
        Source DMI parameter identifier.
    name
        Output variable name.
    standard_name
        CF standard name.
    units
        CF units string.
    kelvin_offset
        Optional additive offset used to convert Celsius to Kelvin.
    """

    source_parameter_id: str
    name: str
    standard_name: str
    units: str
    kelvin_offset: float | None = None


VARIABLE_SPECS: tuple[CfVariableSpec, ...] = (
    CfVariableSpec("temp_dry", "air_temperature", "air_temperature", "K", 273.15),
    CfVariableSpec("temp_dew", "dew_point_temperature", "dew_point_temperature", "K", 273.15),
    CfVariableSpec("wind_speed", "wind_speed", "wind_speed", "m s-1", None),
)


def _read_cache_file(path: Path) -> pd.DataFrame:
    """Read one cached GeoParquet file.

    Parameters
    ----------
    path
        Cache file path.

    Returns
    -------
    pandas.DataFrame
        Cached observation rows.
    """

    table = pq.read_table(path)
    df = table.to_pandas()
    if "geometry" in df.columns and "longitude" not in df.columns and "latitude" not in df.columns:
        # Geometry is retained in the cache for inspection, but the xarray view
        # is driven by station and time coordinates.
        pass
    return df


def _read_cache_tree(root: Path) -> pd.DataFrame:
    """Read all cached GeoParquet files below a root directory.

    Parameters
    ----------
    root
        Cache root directory.

    Returns
    -------
    pandas.DataFrame
        Concatenated cache rows.
    """

    paths = sorted(root.rglob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no parquet files found under {root}")
    frames = [_read_cache_file(path) for path in paths]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize cached cache rows for xarray conversion.

    Parameters
    ----------
    df
        Input data frame.

    Returns
    -------
    pandas.DataFrame
        Normalized frame.
    """

    if df.empty:
        return df

    if "observed_at" in df.columns:
        df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True, errors="coerce")
    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")

    if "parameter_id" not in df.columns:
        raise ValueError("cache files must include parameter_id")

    return df.dropna(subset=["station_id", "observed_at", "parameter_id"])


def _pivot_variables(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long cache rows to a wide observation table.

    Parameters
    ----------
    df
        Normalized long-form observations.

    Returns
    -------
    pandas.DataFrame
        Wide table indexed by station and observation time.
    """

    wide = (
        df.pivot_table(
            index=["station_id", "observed_at", "latitude", "longitude"],
            columns="parameter_id",
            values="value",
            aggfunc="first",
        )
        .reset_index()
        .sort_values(["station_id", "observed_at"])
        .reset_index(drop=True)
    )
    wide.columns.name = None
    return wide


def _to_kelvin(series: pd.Series, offset: float | None) -> pd.Series:
    """Convert a series to Kelvin when an offset is supplied.

    Parameters
    ----------
    series
        Input numeric series.
    offset
        Offset to add to the values.

    Returns
    -------
    pandas.Series
        Converted values.
    """

    if offset is None:
        return series
    return series + offset


def _attach_variable_attrs(dataset: xr.Dataset) -> xr.Dataset:
    """Attach CF-style metadata to dataset variables and coordinates.

    Parameters
    ----------
    dataset
        Dataset to annotate.

    Returns
    -------
    xarray.Dataset
        Annotated dataset.
    """

    for spec in VARIABLE_SPECS:
        if spec.name in dataset:
            dataset[spec.name].attrs.update(
                {
                    "standard_name": spec.standard_name,
                    "units": spec.units,
                }
            )
    if "time" in dataset.coords:
        dataset["time"].attrs.update({"standard_name": "time"})
    if "latitude" in dataset.coords:
        dataset["latitude"].attrs.update({"standard_name": "latitude", "units": "degrees_north"})
    if "longitude" in dataset.coords:
        dataset["longitude"].attrs.update({"standard_name": "longitude", "units": "degrees_east"})
    return dataset


def _build_dataset(df: pd.DataFrame) -> xr.Dataset:
    """Build an xarray dataset from cached observations.

    Parameters
    ----------
    df
        Normalized cached observations.

    Returns
    -------
    xarray.Dataset
        Dataset with an ``obs`` dimension.
    """

    if df.empty:
        raise ValueError("no observations found in cache")

    wide = _pivot_variables(df)
    wide = wide.rename(columns={"observed_at": "time", "station_id": "station_id"})
    wide["time"] = pd.to_datetime(wide["time"], utc=True)

    for spec in VARIABLE_SPECS:
        if spec.source_parameter_id in wide.columns:
            wide[spec.name] = _to_kelvin(
                pd.to_numeric(wide[spec.source_parameter_id], errors="coerce"),
                spec.kelvin_offset,
            )

    coords = {
        "time": ("obs", wide["time"].to_numpy()),
        "station_id": ("obs", wide["station_id"].astype(str).to_numpy()),
        "latitude": ("obs", wide["latitude"].to_numpy()),
        "longitude": ("obs", wide["longitude"].to_numpy()),
    }

    data_vars = {
        spec.name: ("obs", wide[spec.name].to_numpy())
        for spec in VARIABLE_SPECS
        if spec.name in wide.columns
    }

    dataset = xr.Dataset(data_vars=data_vars, coords=coords)
    dataset = _attach_variable_attrs(dataset)
    if "station_id" in dataset.coords:
        dataset["station_id"].attrs.update({"long_name": "DMI station identifier"})
    dataset.attrs.update(
        {
            "source": "DMI GeoParquet cache",
            "feature_type": "trajectory",
        }
    )
    return dataset


def open_dataset(path: str | Path | Iterable[str | Path]) -> xr.Dataset:
    """Open cached DMI GeoParquet files as an xarray dataset.

    Parameters
    ----------
    path
        Cache root, single file, or iterable of roots/files.

    Returns
    -------
    xarray.Dataset
        Dataset containing CF-annotated variables.
    """

    if isinstance(path, (str, Path)):
        root = Path(path)
        if root.is_file():
            frames = [_normalize_frame(_read_cache_file(root))]
        else:
            frames = [_normalize_frame(_read_cache_tree(root))]
    else:
        frames = []
        for item in path:
            item_path = Path(item)
            frames.append(
                _normalize_frame(
                    _read_cache_file(item_path)
                    if item_path.is_file()
                    else _read_cache_tree(item_path)
                )
            )

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _build_dataset(df)
