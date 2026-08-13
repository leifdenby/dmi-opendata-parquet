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
        partition_path = (
            output_dir
            / "raw"
            / f"year={int(year):04d}"
            / f"month={int(month):02d}"
            / "part-00000.parquet"
        )
        if partition_path.exists() and not overwrite:
            written.append(partition_path)
            continue

        partition = partition.drop(columns=["year", "month"], errors="ignore")
        _write_geoparquet(partition, partition_path)
        written.append(partition_path)

    return written
