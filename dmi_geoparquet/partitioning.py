"""Estimate DMI fetch volumes and partition sizing."""

from __future__ import annotations

import calendar
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class PartitionCandidate:
    """A candidate partitioning strategy.

    Parameters
    ----------
    name
        Human-readable name for the strategy.
    columns
        Partition columns in order.
    partition_days
        Number of days represented by the partition or ``"month"``.
    split_factor
        Optional divisor used to approximate sub-partition splitting.
    """

    name: str
    columns: tuple[str, ...]
    partition_days: int | str
    split_factor: int = 1


@dataclass(frozen=True)
class PartitionEstimate:
    """Projected file-size outcome for one candidate partitioning strategy.

    Parameters
    ----------
    name
        Candidate name.
    rows
        Observed sample row count.
    estimated_bytes
        Estimated compressed bytes per partition.
    estimated_mb
        Estimated compressed megabytes per partition.
    projected_month_rows
        Extrapolated rows per month.
    projected_year_rows
        Extrapolated rows per year.
    """

    name: str
    rows: int
    estimated_bytes: int
    estimated_mb: float
    projected_month_rows: int
    projected_year_rows: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_CANDIDATES: tuple[PartitionCandidate, ...] = (
    PartitionCandidate("year_month", ("year", "month"), "month"),
    PartitionCandidate("year_month_day", ("year", "month", "day"), 1),
    PartitionCandidate("year_month_week", ("year", "month", "week"), 7),
    PartitionCandidate(
        "year_month_station_bucket",
        ("year", "month", "station_bucket"),
        "month",
        8,
    ),
    PartitionCandidate(
        "year_month_week_station_bucket",
        ("year", "month", "week", "station_bucket"),
        7,
        8,
    ),
)

DMI_MAX_LIMIT = 300000


def build_sample_frame(rows: int = 10000) -> pd.DataFrame:
    """Build a synthetic sample frame for local estimation.

    Parameters
    ----------
    rows
        Number of rows to generate.

    Returns
    -------
    pandas.DataFrame
        Synthetic observation-like data.
    """

    timestamps = pd.date_range("2026-08-01", periods=rows, freq="10min")
    df = pd.DataFrame(
        {
            "station_id": [f"{60000 + (i % 25):05d}" for i in range(rows)],
            "observed_at": timestamps,
            "temp_dry": (10 + (pd.Series(range(rows)) % 80) / 10).astype("float32"),
            "temp_dew": (7 + (pd.Series(range(rows)) % 60) / 10).astype("float32"),
            "wind_speed": (pd.Series(range(rows)) % 220 / 10).astype("float32"),
            "latitude": 55.0 + (pd.Series(range(rows)) % 50) * 0.01,
            "longitude": 10.0 + (pd.Series(range(rows)) % 50) * 0.01,
        }
    )
    df["year"] = df["observed_at"].dt.year
    df["month"] = df["observed_at"].dt.month
    df["day"] = df["observed_at"].dt.day
    df["week"] = df["observed_at"].dt.isocalendar().week.astype("int32")
    df["station_bucket"] = pd.factorize(df["station_id"])[0] % 8
    return df


def _fetch_json(url: str) -> dict[str, object]:
    """Fetch JSON from a URL.

    Parameters
    ----------
    url
        Request URL.

    Returns
    -------
    dict[str, object]
        Parsed JSON payload.
    """

    with urlopen(url) as response:  # nosec: trusted public DMI endpoint
        return json.loads(response.read().decode("utf-8"))


def default_sample_window(now: datetime | None = None) -> tuple[str, str]:
    """Return the default one-day sample window one year back.

    Parameters
    ----------
    now
        Reference time. Defaults to the current UTC time.

    Returns
    -------
    tuple[str, str]
        Inclusive start and exclusive end timestamps in RFC3339 format.
    """

    now = now or datetime.now(timezone.utc)
    sample_start = date(now.year - 1, now.month, now.day)
    sample_end = sample_start + timedelta(days=1)
    return (
        f"{sample_start.isoformat()}T00:00:00Z",
        f"{sample_end.isoformat()}T00:00:00Z",
    )


def _fetch_dmi_page(url: str) -> dict[str, object]:
    """Fetch one DMI page.

    Parameters
    ----------
    url
        Request URL.

    Returns
    -------
    dict[str, object]
        Parsed payload or an empty dictionary.
    """

    payload = _fetch_json(url)
    if not isinstance(payload, dict):
        return {}
    return payload


def _next_link(payload: dict[str, object]) -> str | None:
    """Extract the ``next`` link from a DMI response.

    Parameters
    ----------
    payload
        Parsed DMI response.

    Returns
    -------
    str or None
        Next-page URL when present.
    """

    links = payload.get("links", [])
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("rel") == "next":
            href = link.get("href")
            return href if isinstance(href, str) else None
    return None


def _fetch_dmi_features(
    parameter_id: str,
    limit: int,
    datetime_start: str,
    datetime_end: str,
) -> list[dict[str, object]]:
    """Fetch all matching DMI feature pages for a parameter.

    Parameters
    ----------
    parameter_id
        DMI parameter identifier.
    limit
        Maximum page size.
    datetime_start
        Inclusive start timestamp.
    datetime_end
        Exclusive end timestamp.

    Returns
    -------
    list[dict[str, object]]
        Flattened list of GeoJSON features.
    """

    limit = min(limit, DMI_MAX_LIMIT)
    query = urlencode(
        {
            "limit": limit,
            "parameterId": parameter_id,
            "datetime": f"{datetime_start}/{datetime_end}",
            "sortorder": "observed,DESC",
        }
    )
    url = f"https://opendataapi.dmi.dk/v2/metObs/collections/observation/items?{query}"
    features: list[dict[str, object]] = []
    while url:
        payload = _fetch_dmi_page(url)
        page_features = payload.get("features", [])
        if isinstance(page_features, list):
            features.extend(feature for feature in page_features if isinstance(feature, dict))
        url = _next_link(payload)
    return features


def fetch_dmi_sample_frame(
    parameter_ids: Sequence[str],
    limit: int = DMI_MAX_LIMIT,
    datetime_start: str | None = None,
    datetime_end: str | None = None,
) -> pd.DataFrame:
    """Fetch a wide sample frame from DMI.

    Parameters
    ----------
    parameter_ids
        Parameter identifiers to fetch.
    limit
        Maximum page size.
    datetime_start
        Inclusive start timestamp.
    datetime_end
        Exclusive end timestamp.

    Returns
    -------
    pandas.DataFrame
        Wide frame with one row per station and observation time.
    """

    if datetime_start is None or datetime_end is None:
        datetime_start, datetime_end = default_sample_window()
    rows: list[dict[str, object]] = []
    for parameter_id in parameter_ids:
        for feature in _fetch_dmi_features(parameter_id, limit, datetime_start, datetime_end):
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coordinates = (
                geometry.get("coordinates", [None, None])
                if isinstance(geometry, dict)
                else [None, None]
            )
            longitude = coordinates[0] if len(coordinates) > 0 else None
            latitude = coordinates[1] if len(coordinates) > 1 else None
            if not isinstance(properties, dict):
                continue
            rows.append(
                {
                    "station_id": properties.get("stationId"),
                    "parameter_id": properties.get("parameterId"),
                    "observed_at": properties.get("observed"),
                    "value": properties.get("value"),
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "station_id",
                "observed_at",
                "temp_dry",
                "temp_dew",
                "wind_speed",
                "latitude",
                "longitude",
            ]
        )

    df = pd.DataFrame(rows)
    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True, errors="coerce")
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
    wide["year"] = wide["observed_at"].dt.year
    wide["month"] = wide["observed_at"].dt.month
    wide["day"] = wide["observed_at"].dt.day
    wide["week"] = wide["observed_at"].dt.isocalendar().week.astype("int32")
    wide["station_bucket"] = pd.factorize(wide["station_id"])[0] % 8
    return wide


def _table_size_bytes(df: pd.DataFrame) -> int:
    """Measure the compressed Parquet size of a DataFrame.

    Parameters
    ----------
    df
        Input frame.

    Returns
    -------
    int
        Compressed parquet size in bytes.
    """

    table = pa.Table.from_pandas(df, preserve_index=False)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue().size


def estimate_partition_sizes(
    df: pd.DataFrame,
    candidates: Iterable[PartitionCandidate] = DEFAULT_CANDIDATES,
) -> list[PartitionEstimate]:
    """Estimate per-partition file sizes for candidate layouts.

    Parameters
    ----------
    df
        Sample frame.
    candidates
        Partitioning strategies to evaluate.

    Returns
    -------
    list[PartitionEstimate]
        Estimates ordered by the input candidates.
    """

    if df.empty:
        return []

    observed = pd.to_datetime(df.get("observed_at"), utc=True, errors="coerce").dropna()
    sample_start = observed.min().normalize() if not observed.empty else None
    sample_days = (
        max(
            1,
            int(
                ((observed.max().normalize() + pd.Timedelta(days=1)) - sample_start).total_seconds()
                // 86400
            ),
        )
        if sample_start is not None
        else 1
    )

    sample_bytes = _table_size_bytes(df)
    bytes_per_day = max(1, int(sample_bytes / sample_days))
    rows_per_day = max(1, int(len(df) / sample_days))

    month_days = (
        calendar.monthrange(sample_start.year, sample_start.month)[1]
        if sample_start is not None
        else 30
    )
    year_days = 366 if sample_start is not None and calendar.isleap(sample_start.year) else 365

    estimates: list[PartitionEstimate] = []
    for candidate in candidates:
        partition_days = (
            month_days if candidate.partition_days == "month" else int(candidate.partition_days)
        )
        size_bytes = int((bytes_per_day * partition_days) / candidate.split_factor)
        estimates.append(
            PartitionEstimate(
                name=candidate.name,
                rows=len(df),
                estimated_bytes=size_bytes,
                estimated_mb=size_bytes / (1024 * 1024),
                projected_month_rows=rows_per_day * month_days,
                projected_year_rows=rows_per_day * year_days,
            )
        )
    return estimates


def choose_partition(
    estimates: list[PartitionEstimate],
    soft_min_mb: float = 10.0,
    soft_max_mb: float = 50.0,
) -> PartitionEstimate:
    """Choose the best candidate for the soft file-size target.

    Parameters
    ----------
    estimates
        Candidate estimates.
    soft_min_mb
        Lower bound of the acceptable range.
    soft_max_mb
        Upper bound of the acceptable range.

    Returns
    -------
    PartitionEstimate
        Selected candidate.
    """

    in_range = [e for e in estimates if soft_min_mb <= e.estimated_mb <= soft_max_mb]
    if in_range:
        return min(in_range, key=lambda e: abs(e.estimated_mb - (soft_min_mb + soft_max_mb) / 2))
    below = [e for e in estimates if e.estimated_mb < soft_min_mb]
    if below:
        return max(below, key=lambda e: e.estimated_mb)
    return min(estimates, key=lambda e: e.estimated_mb)


def estimate_from_frame(
    df: pd.DataFrame,
    soft_min_mb: float = 10.0,
    soft_max_mb: float = 50.0,
) -> dict[str, object]:
    """Estimate partition choices from an in-memory frame.

    Parameters
    ----------
    df
        Sample frame.
    soft_min_mb
        Lower bound of the acceptable file size range.
    soft_max_mb
        Upper bound of the acceptable file size range.

    Returns
    -------
    dict[str, object]
        JSON-serializable estimate result.
    """

    estimates = estimate_partition_sizes(df)
    chosen = choose_partition(estimates, soft_min_mb=soft_min_mb, soft_max_mb=soft_max_mb)
    return {
        "soft_min_mb": soft_min_mb,
        "soft_max_mb": soft_max_mb,
        "chosen": chosen.to_dict(),
        "candidates": [estimate.to_dict() for estimate in estimates],
    }


def estimate_from_parquet(
    path: str | Path,
    soft_min_mb: float = 10.0,
    soft_max_mb: float = 50.0,
) -> dict[str, object]:
    """Estimate partition choices from a Parquet file.

    Parameters
    ----------
    path
        Parquet file path.
    soft_min_mb
        Lower bound of the acceptable file size range.
    soft_max_mb
        Upper bound of the acceptable file size range.

    Returns
    -------
    dict[str, object]
        JSON-serializable estimate result.
    """

    df = pd.read_parquet(path)
    return estimate_from_frame(df, soft_min_mb=soft_min_mb, soft_max_mb=soft_max_mb)


def estimate_from_dmi(
    parameter_ids: Sequence[str],
    soft_min_mb: float = 10.0,
    soft_max_mb: float = 50.0,
    datetime_start: str | None = None,
    datetime_end: str | None = None,
) -> dict[str, object]:
    """Estimate partition and download cost from live DMI data.

    Parameters
    ----------
    parameter_ids
        Parameter identifiers to sample.
    soft_min_mb
        Lower bound of the acceptable file size range.
    soft_max_mb
        Upper bound of the acceptable file size range.
    datetime_start
        Inclusive sample start timestamp.
    datetime_end
        Exclusive sample end timestamp.

    Returns
    -------
    dict[str, object]
        JSON-serializable estimate result including projections.
    """

    if datetime_start is None or datetime_end is None:
        datetime_start, datetime_end = default_sample_window()
    fetch_started_at = time.perf_counter()
    df = fetch_dmi_sample_frame(
        parameter_ids,
        limit=DMI_MAX_LIMIT,
        datetime_start=datetime_start,
        datetime_end=datetime_end,
    )
    fetch_seconds = max(0.0, time.perf_counter() - fetch_started_at)
    result = estimate_from_frame(df, soft_min_mb=soft_min_mb, soft_max_mb=soft_max_mb)
    result["source"] = "dmi"
    result["parameters"] = list(parameter_ids)
    result["sample_rows"] = len(df)
    result["datetime_start"] = datetime_start
    result["datetime_end"] = datetime_end
    result["fetch_seconds"] = fetch_seconds
    if not df.empty and "observed_at" in df.columns:
        observed = pd.to_datetime(df["observed_at"], utc=True, errors="coerce").dropna()
        if not observed.empty:
            sample_start = observed.min().normalize()
            sample_end = observed.max().normalize() + pd.Timedelta(days=1)
            sample_days = max(1, int((sample_end - sample_start).total_seconds() // 86400))
            rows_per_day = len(df) / sample_days
            sample_month_days = calendar.monthrange(sample_start.year, sample_start.month)[1]
            sample_year_days = 366 if calendar.isleap(sample_start.year) else 365
            result["projection"] = {
                "rows_per_day": rows_per_day,
                "rows_per_month": rows_per_day * sample_month_days,
                "rows_per_year": rows_per_day * sample_year_days,
                "sample_days": sample_days,
            }
            seconds_per_day = fetch_seconds / sample_days if sample_days else fetch_seconds
            result["download_projection"] = {
                "seconds_per_day": seconds_per_day,
                "seconds_per_month": seconds_per_day * sample_month_days,
                "seconds_per_year": seconds_per_day * sample_year_days,
                "rows_per_second": len(df) / fetch_seconds if fetch_seconds > 0 else None,
            }
    return result
