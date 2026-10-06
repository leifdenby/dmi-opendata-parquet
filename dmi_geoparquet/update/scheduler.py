"""Periodically refresh the DMI cache and rebuild the final GeoParquet dataset."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..build import build_final_partition, final_partition_path
from ..download import CacheSlice, build_cache, cache_slice_path, iter_cache_slices
from .status import STATUS_FILENAME, StatusTracker, block_key, file_summary, utc_now

logger = logging.getLogger(__name__)


def refresh_start_date(today: date, refresh_days: int) -> date:
    """Return the first day of the month containing ``today - refresh_days``.

    Monthly cache slices are keyed by their start date, so refreshes must
    begin on a month boundary to rewrite whole slices.

    Parameters
    ----------
    today
        Current UTC date.
    refresh_days
        Number of trailing days whose observations may still change upstream.

    Returns
    -------
    datetime.date
        Month-aligned date from which cache slices are re-downloaded.
    """

    return (today - timedelta(days=refresh_days)).replace(day=1)


def run_update_cycle(
    start: str,
    cache_root: str | Path,
    output_dir: str | Path,
    parameters: Iterable[str],
    refresh_days: int = 3,
    today: date | None = None,
    status: StatusTracker | None = None,
) -> list[Path]:
    """Backfill missing cache slices, refresh recent ones, and rebuild the final dataset.

    Months are processed in order: each parameter/month block is downloaded,
    then that month's final file is built straight away. Progress is written to
    the status file, and one failing block does not stop the others.

    Parameters
    ----------
    start
        Inclusive backfill start date (``YYYY-MM-DD``), ideally the first of a month.
    cache_root
        Cache root directory.
    output_dir
        Final GeoParquet output directory.
    parameters
        DMI parameter IDs to download.
    refresh_days
        Trailing days whose monthly cache slices are always re-downloaded.
    today
        Current UTC date; defaults to ``datetime.now(timezone.utc).date()``.
    status
        Progress tracker; defaults to one writing ``<output_dir>/_status.json``.

    Returns
    -------
    list[pathlib.Path]
        Final GeoParquet partition paths.
    """

    today = today or datetime.now(timezone.utc).date()
    parameters = list(parameters)
    cache_root = Path(cache_root)
    output_dir = Path(output_dir)
    status = status or StatusTracker(output_dir / STATUS_FILENAME)
    start_date = date.fromisoformat(start)
    end_date = today + timedelta(days=1)
    refresh_date = max(start_date, refresh_start_date(today, refresh_days))

    months: list[tuple[str, CacheSlice, list[str]]] = []
    blocks: dict[str, dict[str, Any]] = {}
    partitions: dict[str, dict[str, Any]] = {}
    for cache_slice in iter_cache_slices(start_date, end_date, "monthly"):
        period = cache_slice.start.strftime("%Y-%m")
        refresh = cache_slice.end > refresh_date
        pending: list[str] = []
        for parameter_id in parameters:
            path = cache_slice_path(cache_root, parameter_id, cache_slice, "monthly")
            block: dict[str, Any] = {
                "parameter": parameter_id,
                "period": period,
                "start": cache_slice.start.isoformat(),
                "end": cache_slice.end.isoformat(),
                "refresh": refresh,
                "state": "waiting",
            }
            if path.exists():
                block.update(file_summary(path))
                if not refresh:
                    block["state"] = "done"
            if block["state"] == "waiting":
                pending.append(parameter_id)
            blocks[block_key(parameter_id, period)] = block

        final_path = final_partition_path(
            output_dir, cache_slice.start.year, cache_slice.start.month
        )
        partition: dict[str, Any] = {"state": "waiting"}
        if final_path.exists():
            partition.update(path=final_path.relative_to(output_dir).as_posix())
            partition.update(file_summary(final_path))
            if not pending:
                partition["state"] = "done"
        partitions[period] = partition
        months.append((period, cache_slice, pending))
    status.start_cycle(parameters, blocks, partitions)

    failed = False
    for period, cache_slice, pending in months:
        for parameter_id in pending:
            failed |= not _download_block(status, cache_root, parameter_id, period, cache_slice)
        if status.data["partitions"][period]["state"] == "waiting":
            failed |= not _build_partition(
                status, cache_root, output_dir, parameters, period, cache_slice
            )

    status.finish_cycle("error" if failed else "ok")
    return sorted(output_dir.glob("raw/year=*/month=*/part-00000.parquet"))


def _download_block(
    status: StatusTracker,
    cache_root: Path,
    parameter_id: str,
    period: str,
    cache_slice: CacheSlice,
) -> bool:
    """Download one parameter/month cache block and record its state.

    Parameters
    ----------
    status
        Progress tracker.
    cache_root
        Cache root directory.
    parameter_id
        DMI parameter ID.
    period
        ``YYYY-MM`` month label.
    cache_slice
        Month to download.

    Returns
    -------
    bool
        ``False`` if the download failed.
    """

    key = block_key(parameter_id, period)
    status.update_block(key, state="processing", started_at=utc_now(), error=None)
    try:
        written = build_cache(
            start=cache_slice.start.isoformat(),
            end=cache_slice.end.isoformat(),
            output_dir=cache_root,
            parameters=[parameter_id],
            partition_size="monthly",
            overwrite=True,
        )
    except Exception as exc:
        logger.exception("failed to download %s", key)
        status.update_block(key, state="error", error=f"{type(exc).__name__}: {exc}")
        return False
    if written:
        status.update_block(key, state="done", **file_summary(written[0]))
    else:
        status.update_block(key, state="empty", rows=0)
    return True


def _build_partition(
    status: StatusTracker,
    cache_root: Path,
    output_dir: Path,
    parameters: list[str],
    period: str,
    cache_slice: CacheSlice,
) -> bool:
    """Build one month's final file from whatever cache blocks exist.

    Parameters
    ----------
    status
        Progress tracker.
    cache_root
        Cache root directory.
    output_dir
        Final dataset root.
    parameters
        DMI parameter IDs whose cache files are combined.
    period
        ``YYYY-MM`` month label.
    cache_slice
        Month to build.

    Returns
    -------
    bool
        ``False`` if the build failed.
    """

    cache_paths = [
        path
        for parameter_id in parameters
        if (path := cache_slice_path(cache_root, parameter_id, cache_slice, "monthly")).exists()
    ]
    status.update_partition(period, state="processing", started_at=utc_now(), error=None)
    try:
        path = build_final_partition(
            cache_paths, output_dir, cache_slice.start.year, cache_slice.start.month
        )
    except Exception as exc:
        logger.exception("failed to build final partition %s", period)
        status.update_partition(period, state="error", error=f"{type(exc).__name__}: {exc}")
        return False
    if path is None:
        status.update_partition(period, state="empty", rows=0)
    else:
        status.update_partition(
            period,
            state="done",
            path=path.relative_to(output_dir).as_posix(),
            **file_summary(path),
        )
    return True


def run_update_loop(
    interval_hours: float,
    cycle: Callable[[], object],
    max_cycles: int | None = None,
    status: StatusTracker | None = None,
) -> None:
    """Run ``cycle`` repeatedly, sleeping ``interval_hours`` between starts.

    Failures are logged and retried on the next interval so a transient DMI
    outage does not stop the service.

    Parameters
    ----------
    interval_hours
        Hours between the start of consecutive cycles.
    cycle
        Zero-argument callable performing one update.
    max_cycles
        Stop after this many cycles; ``None`` runs forever.
    status
        Optional tracker that records the next scheduled run.
    """

    interval_seconds = interval_hours * 3600
    completed = 0
    while max_cycles is None or completed < max_cycles:
        started = time.monotonic()
        logger.info("starting update cycle")
        try:
            cycle()
            logger.info("update cycle finished in %.0fs", time.monotonic() - started)
        except Exception:
            logger.exception("update cycle failed")
        completed += 1
        if max_cycles is not None and completed >= max_cycles:
            break
        delay = max(0.0, interval_seconds - (time.monotonic() - started))
        if status is not None:
            status.set_next_run(
                datetime.now(timezone.utc) + timedelta(seconds=delay), interval_hours
            )
        time.sleep(delay)
