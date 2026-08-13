"""Create timeseries plots from cached DMI GeoParquet data."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from .xarray_reader import VARIABLE_SPECS, open_dataset


def _variable_names(variables: Sequence[str] | None = None) -> list[str]:
    """Return the plot variable names to render."""

    if variables is not None:
        return list(variables)
    return [spec.name for spec in VARIABLE_SPECS]


def _plot_label(name: str) -> str:
    """Return a human-friendly label for a plot variable."""

    return name.replace("_", " ").title()


def _units_for(name: str) -> str:
    """Return the CF units for a variable."""

    for spec in VARIABLE_SPECS:
        if spec.name == name:
            return spec.units
    return ""


def _default_window(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the default two-day window for plotting."""

    times = pd.to_datetime(frame["time"], utc=True, errors="coerce").dropna()
    if times.empty:
        raise ValueError("cache does not contain any valid timestamps")
    end = times.max().floor("10min")
    start = end - pd.Timedelta(days=2)
    return start, end + pd.Timedelta(minutes=10)


def _time_window(
    frame: pd.DataFrame,
    start: str | None,
    end: str | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Resolve the plotting time window."""

    if start is not None and end is not None:
        return pd.to_datetime(start, utc=True), pd.to_datetime(end, utc=True)
    return _default_window(frame)


def _subset_window(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Subset the frame to the requested plotting window."""

    windowed = frame.copy()
    windowed["time"] = pd.to_datetime(windowed["time"], utc=True, errors="coerce")
    windowed = windowed.dropna(subset=["time", "station_id"])
    mask = (windowed["time"] >= start) & (windowed["time"] < end)
    return windowed.loc[mask]


def plot_cache_timeseries(
    cache_path: str | Path,
    output_dir: str | Path,
    variables: Sequence[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[Path]:
    """Render timeseries plots from a GeoParquet cache."""

    dataset = open_dataset(cache_path)
    requested_variables = _variable_names(variables)
    available_variables = [name for name in requested_variables if name in dataset]
    if not available_variables:
        raise ValueError("no requested variables are available in the cache")

    frame = dataset[[*available_variables]].to_dataframe().reset_index()
    start_ts, end_ts = _time_window(frame, start, end)
    windowed = _subset_window(frame, start_ts, end_ts)
    if windowed.empty:
        raise ValueError("no observations found in the requested time window")

    windowed["time"] = pd.to_datetime(windowed["time"], utc=True, errors="coerce")
    counts = windowed.groupby("time")["station_id"].nunique().sort_index()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for variable in available_variables:
        plot_frame = windowed[["time", "station_id", variable]].dropna(subset=["time", variable])
        if plot_frame.empty:
            continue

        fig, ax = plt.subplots(figsize=(12, 4))
        for _station_id, station_frame in plot_frame.groupby("station_id", sort=False):
            station_frame = station_frame.sort_values("time")
            ax.plot(
                station_frame["time"],
                station_frame[variable],
                linewidth=1.0,
                alpha=0.35,
            )

        ax2 = ax.twinx()
        ax2.step(counts.index, counts.values, where="post", color="black", alpha=0.7)
        ax2.set_ylabel("Station count")

        ax.set_title(
            f"DMI {_plot_label(variable)} from {start_ts.isoformat()} to {end_ts.isoformat()}"
        )
        ax.set_xlabel("Time")
        ax.set_ylabel(_units_for(variable))
        ax.grid(True, alpha=0.3)
        ax.set_xlim(
            mdates.date2num(start_ts.to_pydatetime()),
            mdates.date2num(end_ts.to_pydatetime()),
        )
        fig.autofmt_xdate()

        output_path = output_dir / f"{variable}.png"
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        written.append(output_path)

    return written
