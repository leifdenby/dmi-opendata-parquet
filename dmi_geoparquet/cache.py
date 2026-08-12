from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm.auto import tqdm

from dmi_geoparquet.partitioning import DMI_MAX_LIMIT, _fetch_dmi_features


@dataclass(frozen=True)
class CacheSlice:
    start: date
    end: date


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def iter_cache_slices(start: date, end: date, partition: str) -> list[CacheSlice]:
    slices: list[CacheSlice] = []
    cursor = start
    while cursor < end:
        if partition == "daily":
            next_cursor = min(end, cursor + timedelta(days=1))
        elif partition == "weekly":
            next_cursor = min(end, cursor + timedelta(days=7))
        elif partition == "monthly":
            next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            next_cursor = min(end, next_month)
        else:
            raise ValueError(f"unsupported cache partition size: {partition}")
        slices.append(CacheSlice(start=cursor, end=next_cursor))
        cursor = next_cursor
    return slices


def _point_wkb(longitude: float | None, latitude: float | None) -> bytes | None:
    if longitude is None or latitude is None:
        return None
    return struct.pack("<bIdd", 1, 1, float(longitude), float(latitude))


def _fetch_observation_rows(parameter_id: str, start: date, end: date) -> pd.DataFrame:
    start_ts = f"{start.isoformat()}T00:00:00Z"
    end_ts = f"{end.isoformat()}T00:00:00Z"
    rows: list[dict[str, object]] = []
    for feature in _fetch_dmi_features(parameter_id, DMI_MAX_LIMIT, start_ts, end_ts):
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [None, None]) if isinstance(geometry, dict) else [None, None]
        longitude = coordinates[0] if len(coordinates) > 0 else None
        latitude = coordinates[1] if len(coordinates) > 1 else None
        if not isinstance(properties, dict):
            continue
        rows.append(
            {
                "station_id": properties.get("stationId"),
                "parameter_id": properties.get("parameterId"),
                "observed_at": properties.get("observed"),
                "created_at": properties.get("created"),
                "value": properties.get("value"),
                "latitude": latitude,
                "longitude": longitude,
                "geometry": _point_wkb(longitude, latitude),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True, errors="coerce")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    df["year"] = df["observed_at"].dt.year
    df["month"] = df["observed_at"].dt.month
    df["day"] = df["observed_at"].dt.day
    return df


def _geo_metadata() -> bytes:
    geo = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point"],
                "crs": None,
            }
        },
    }
    return json.dumps(geo).encode("utf-8")


def _write_geoparquet(df: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[b"geo"] = _geo_metadata()
    table = table.replace_schema_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="snappy")


def build_cache(
    start: str,
    end: str,
    output_dir: str | Path,
    parameters: Iterable[str],
    partition_size: str = "monthly",
    overwrite: bool = False,
) -> list[Path]:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    slices = iter_cache_slices(start_date, end_date, partition_size)
    output_dir = Path(output_dir)
    written: list[Path] = []

    jobs = [(parameter_id, cache_slice) for parameter_id in parameters for cache_slice in slices]
    for parameter_id, cache_slice in tqdm(jobs, desc="Building cache", unit="slice"):
        if partition_size == "monthly":
            partition_parts = [f"year={cache_slice.start.year}", f"month={cache_slice.start.month:02d}"]
        elif partition_size == "weekly":
            partition_parts = [f"year={cache_slice.start.year}", f"week={cache_slice.start.isocalendar().week:02d}"]
        else:
            partition_parts = [
                f"year={cache_slice.start.year}",
                f"month={cache_slice.start.month:02d}",
                f"day={cache_slice.start.day:02d}",
            ]
        path = output_dir / parameter_id / partition_size / Path(*partition_parts) / "part-00000.parquet"
        if path.exists() and not overwrite:
            written.append(path)
            continue

        df = _fetch_observation_rows(parameter_id, cache_slice.start, cache_slice.end)
        if df.empty:
            continue
        _write_geoparquet(df, path)
        written.append(path)

    return written
