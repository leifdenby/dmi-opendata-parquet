"""Build the final GeoParquet dataset from cached DMI observations."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from tqdm.auto import tqdm

from ..download.cache import _write_geoparquet


def _read_cache_files(cache_root: Path) -> pd.DataFrame:
    """Read all cached GeoParquet files into a single frame."""

    paths = sorted(cache_root.rglob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no parquet files found under {cache_root}")

    frames = [pd.read_parquet(path) for path in paths]
    return pd.concat(frames, ignore_index=True)


def _ensure_temporal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure year and month columns exist on the frame."""

    frame = df.copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["observed_at", "station_id", "parameter_id"])
    if "year" not in frame.columns:
        frame["year"] = frame["observed_at"].dt.year
    if "month" not in frame.columns:
        frame["month"] = frame["observed_at"].dt.month
    return frame


def final_partition_path(output_dir: str | Path, year: int, month: int) -> Path:
    """Return the final GeoParquet path for one month.

    Parameters
    ----------
    output_dir
        Final dataset root.
    year
        Partition year.
    month
        Partition month.

    Returns
    -------
    pathlib.Path
        ``<output_dir>/raw/year=YYYY/month=MM/part-00000.parquet``.
    """

    return (
        Path(output_dir) / "raw" / f"year={year:04d}" / f"month={month:02d}" / "part-00000.parquet"
    )


def build_final_partition(
    cache_paths: list[Path],
    output_dir: str | Path,
    year: int,
    month: int,
) -> Path | None:
    """Build the final GeoParquet file for one month from its cache files.

    Rows observed outside the month are dropped so each observation lands in
    exactly one partition.

    Parameters
    ----------
    cache_paths
        Cache files covering the month, typically one per parameter.
    output_dir
        Final dataset root.
    year
        Partition year.
    month
        Partition month.

    Returns
    -------
    pathlib.Path or None
        Written partition path, or ``None`` if the month has no observations.
    """

    if not cache_paths:
        return None
    frame = _ensure_temporal_columns(
        pd.concat([pd.read_parquet(path) for path in cache_paths], ignore_index=True)
    )
    frame = frame[(frame["year"] == year) & (frame["month"] == month)]
    if frame.empty:
        return None
    path = final_partition_path(output_dir, year, month)
    _write_geoparquet(frame.drop(columns=["year", "month"], errors="ignore"), path)
    return path


def build_final_geoparquet(
    cache_root: str | Path,
    output_dir: str | Path,
    overwrite: bool = False,
) -> list[Path]:
    """Build the final raw GeoParquet dataset from the cache."""

    cache_root = Path(cache_root)
    output_dir = Path(output_dir)

    frame = _ensure_temporal_columns(_read_cache_files(cache_root))
    if frame.empty:
        raise ValueError("cache contains no usable observations")

    written: list[Path] = []
    for (year, month), partition in tqdm(
        frame.groupby(["year", "month"], sort=True),
        desc="Building final GeoParquet",
        unit="partition",
    ):
        partition_path = final_partition_path(output_dir, int(year), int(month))
        if partition_path.exists() and not overwrite:
            written.append(partition_path)
            continue

        partition = partition.drop(columns=["year", "month"], errors="ignore")
        _write_geoparquet(partition, partition_path)
        written.append(partition_path)

    return written
