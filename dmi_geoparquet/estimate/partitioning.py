"""Estimate DMI fetch volumes and partition sizing."""

from __future__ import annotations

import calendar
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


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
        Wide sample frame with one row per station and observation time.
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
                    "created_at": properties.get("created"),
                    "value": properties.get("value"),
                    "latitude": latitude,
                    "longitude": longitude,
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
    df["week"] = df["observed_at"].dt.isocalendar().week.astype("int32")
    df["station_bucket"] = pd.factorize(df["station_id"])[0] % 8
    return df


def _candidate_frame_columns(df: pd.DataFrame, candidate: PartitionCandidate) -> pd.DataFrame:
    """Return the columns needed for a candidate estimate."""

    columns = [column for column in candidate.columns if column in df.columns]
    return df[columns].copy()


def _estimate_candidate_bytes(frame: pd.DataFrame, candidate: PartitionCandidate) -> int:
    """Estimate compressed bytes for a candidate partitioning layout."""

    if frame.empty:
        return 0
    candidate_frame = _candidate_frame_columns(frame, candidate)
    candidate_frame["_partition_key"] = candidate_frame.astype(str).agg("|".join, axis=1)
    partition_count = max(1, candidate_frame["_partition_key"].nunique())
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False)
    sample_bytes = buffer.tell()
    return int(sample_bytes * partition_count / max(1, len(frame)))


def _candidate_distance(candidate: dict[str, object], target_mb: float) -> float:
    """Return distance from the target size for a candidate estimate."""

    estimated_mb = candidate["estimated_mb"]
    assert isinstance(estimated_mb, (int, float))
    return abs(float(estimated_mb) - target_mb)


def estimate_from_frame(
    frame: pd.DataFrame,
    soft_min_mb: float = 10.0,
    soft_max_mb: float = 50.0,
) -> dict[str, object]:
    """Estimate partition choices from a sample frame.

    Parameters
    ----------
    frame
        Sample data frame.
    soft_min_mb
        Lower bound of the acceptable file size range.
    soft_max_mb
        Upper bound of the acceptable file size range.

    Returns
    -------
    dict[str, object]
        JSON-serializable estimate result.
    """

    candidates: list[dict[str, object]] = []
    for candidate in DEFAULT_CANDIDATES:
        estimated_bytes = _estimate_candidate_bytes(frame, candidate)
        candidates.append(
            {
                "name": candidate.name,
                "columns": list(candidate.columns),
                "estimated_bytes": estimated_bytes,
                "estimated_mb": estimated_bytes / (1024 * 1024),
                "rows": len(frame),
            }
        )

    target_mb = (soft_min_mb + soft_max_mb) / 2
    chosen = min(candidates, key=lambda item: _candidate_distance(item, target_mb))
    return {
        "candidates": candidates,
        "chosen": chosen,
        "source": "frame",
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
