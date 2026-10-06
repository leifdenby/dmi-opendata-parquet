"""Command-line interface for DMI GeoParquet planning and cache building."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, cast

from .build import build_final_geoparquet
from .download import build_cache, plot_cache_timeseries
from .estimate import (
    build_sample_frame,
    default_sample_window,
    estimate_from_dmi,
    estimate_from_frame,
    estimate_from_parquet,
)
from .update import STATUS_FILENAME, StatusTracker, run_update_cycle, run_update_loop


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser with estimator and cache subcommands.
    """

    parser = argparse.ArgumentParser(prog="dmi-geoparquet")
    subparsers = parser.add_subparsers(dest="command", required=True)

    estimate = subparsers.add_parser(
        "estimate-partitions", help="Estimate partitioning for the soft file size target"
    )
    estimate.add_argument("--input", type=Path, help="Parquet file to analyze")
    estimate.add_argument(
        "--dmi",
        action="store_true",
        help="Sample live DMI observations instead of using synthetic input",
    )
    estimate.add_argument(
        "--parameters",
        nargs="+",
        default=["temp_dry", "temp_dew", "wind_speed"],
        help="DMI parameter IDs to sample",
    )
    estimate.add_argument("--datetime-start", help="Inclusive DMI datetime range start (RFC3339)")
    estimate.add_argument("--datetime-end", help="Inclusive DMI datetime range end (RFC3339)")
    estimate.add_argument(
        "--sample-rows",
        type=int,
        default=10000,
        help="Synthetic rows to generate if no input is provided",
    )
    estimate.add_argument("--soft-min-mb", type=float, default=10.0)
    estimate.add_argument("--soft-max-mb", type=float, default=50.0)
    estimate.add_argument("--json", action="store_true", help="Emit JSON output")

    cache = subparsers.add_parser(
        "build-cache", help="Download DMI observations into GeoParquet cache slices"
    )
    cache.add_argument("--start", required=True, help="Inclusive start date (YYYY-MM-DD)")
    cache.add_argument("--end", required=True, help="Exclusive end date (YYYY-MM-DD)")
    cache.add_argument(
        "--output",
        type=Path,
        default=Path("tmp") / "cache",
        help="Cache output directory",
    )
    cache.add_argument(
        "--parameters",
        nargs="+",
        default=["temp_dry", "temp_dew", "wind_speed"],
        help="DMI parameter IDs to cache",
    )
    cache.add_argument(
        "--partition-size",
        choices=["daily", "weekly", "monthly"],
        default="monthly",
        help="Cache slice size",
    )
    cache.add_argument("--overwrite", action="store_true", help="Rewrite existing cache files")

    final = subparsers.add_parser(
        "build-final", help="Convert cached observations into final GeoParquet files"
    )
    final.add_argument("--cache", type=Path, required=True, help="Cache root directory")
    final.add_argument(
        "--download-missing-data",
        action="store_true",
        help="Download any missing cache data before building the final dataset",
    )
    final.add_argument(
        "--start",
        help="Inclusive start date to backfill when using --download-missing-data",
    )
    final.add_argument(
        "--end",
        help="Exclusive end date to backfill when using --download-missing-data",
    )
    final.add_argument(
        "--parameters",
        nargs="+",
        default=["temp_dry", "temp_dew", "wind_speed"],
        help="DMI parameter IDs to backfill when using --download-missing-data",
    )
    final.add_argument(
        "--partition-size",
        choices=["daily", "weekly", "monthly"],
        default="monthly",
        help="Cache slice size used when backfilling missing data",
    )
    final.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "dmi",
        help="Final output directory",
    )
    final.add_argument("--overwrite", action="store_true", help="Rewrite existing files")

    update = subparsers.add_parser(
        "update", help="Periodically refresh the cache and rebuild the final dataset"
    )
    update.add_argument("--start", required=True, help="Inclusive backfill start (YYYY-MM-DD)")
    update.add_argument("--cache", type=Path, default=Path("tmp") / "cache", help="Cache root")
    update.add_argument(
        "--output", type=Path, default=Path("data") / "dmi", help="Final output directory"
    )
    update.add_argument(
        "--parameters",
        nargs="+",
        default=["temp_dry", "temp_dew", "wind_speed"],
        help="DMI parameter IDs to download",
    )
    update.add_argument(
        "--refresh-days",
        type=int,
        default=3,
        help="Trailing days whose monthly cache slices are re-downloaded every cycle",
    )
    update.add_argument(
        "--interval-hours", type=float, default=3.0, help="Hours between update cycles"
    )
    update.add_argument("--once", action="store_true", help="Run a single cycle and exit")

    plot = subparsers.add_parser(
        "plot-timeseries", help="Create timeseries plots from cached GeoParquet data"
    )
    plot.add_argument("--cache", type=Path, required=True, help="Cache root or Parquet file")
    plot.add_argument(
        "--output", type=Path, default=Path("tmp") / "plots", help="Plot output directory"
    )
    plot.add_argument("--start", help="Inclusive plot start timestamp (RFC3339)")
    plot.add_argument("--end", help="Exclusive plot end timestamp (RFC3339)")
    plot.add_argument(
        "--variables",
        nargs="+",
        default=["air_temperature", "dew_point_temperature", "wind_speed"],
        help="Plot variables to include",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entry point.

    Parameters
    ----------
    argv
        Optional argument vector.

    Returns
    -------
    int
        Process exit code.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "estimate-partitions":
        if args.input:
            result = estimate_from_parquet(
                args.input,
                soft_min_mb=args.soft_min_mb,
                soft_max_mb=args.soft_max_mb,
            )
        elif args.dmi:
            datetime_start = args.datetime_start
            datetime_end = args.datetime_end
            if datetime_start is None or datetime_end is None:
                datetime_start, datetime_end = default_sample_window()
            result = estimate_from_dmi(
                args.parameters,
                soft_min_mb=args.soft_min_mb,
                soft_max_mb=args.soft_max_mb,
                datetime_start=datetime_start,
                datetime_end=datetime_end,
            )
        else:
            frame = build_sample_frame(args.sample_rows)
            result = estimate_from_frame(
                frame,
                soft_min_mb=args.soft_min_mb,
                soft_max_mb=args.soft_max_mb,
            )

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            chosen = cast(dict[str, Any], result["chosen"])
            print(
                f"chosen={chosen['name']} estimated_mb={chosen['estimated_mb']:.2f} "
                f"rows={chosen['rows']}"
            )
            projection = result.get("projection")
            if isinstance(projection, dict):
                print(
                    f"projection rows/day={projection['rows_per_day']:.1f} "
                    f"rows/month={projection['rows_per_month']:.1f} "
                    f"rows/year={projection['rows_per_year']:.1f}"
                )
            download_projection = result.get("download_projection")
            if isinstance(download_projection, dict):
                rows_per_second = download_projection.get("rows_per_second")
                rows_per_second_text = (
                    f"{rows_per_second:.1f}" if rows_per_second is not None else "n/a"
                )
                print(
                    f"fetch rows/sec={rows_per_second_text} "
                    f"seconds/day={download_projection['seconds_per_day']:.1f} "
                    f"seconds/month={download_projection['seconds_per_month']:.1f} "
                    f"seconds/year={download_projection['seconds_per_year']:.1f}"
                )
            for candidate in cast(list[dict[str, Any]], result["candidates"]):
                print(f"{candidate['name']}: {candidate['estimated_mb']:.2f} MB")
        return 0

    if args.command == "build-cache":
        paths = build_cache(
            start=args.start,
            end=args.end,
            output_dir=args.output,
            parameters=args.parameters,
            partition_size=args.partition_size,
            overwrite=args.overwrite,
        )
        print(f"written={len(paths)}")
        return 0

    if args.command == "update":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        status = StatusTracker(args.output / STATUS_FILENAME)

        def cycle() -> None:
            written = run_update_cycle(
                start=args.start,
                cache_root=args.cache,
                output_dir=args.output,
                parameters=args.parameters,
                refresh_days=args.refresh_days,
                status=status,
            )
            logging.info("written=%d", len(written))

        if args.once:
            cycle()
        else:
            run_update_loop(args.interval_hours, cycle, status=status)
        return 0

    if args.command == "plot-timeseries":
        written = plot_cache_timeseries(
            cache_path=args.cache,
            output_dir=args.output,
            variables=args.variables,
            start=args.start,
            end=args.end,
        )
        print(f"written={len(written)}")
        return 0

    if args.command == "build-final":
        if args.download_missing_data:
            if args.start is None or args.end is None:
                parser.error("--start and --end are required with --download-missing-data")
            build_cache(
                start=args.start,
                end=args.end,
                output_dir=args.cache,
                parameters=args.parameters,
                partition_size=args.partition_size,
                overwrite=False,
            )
        written = build_final_geoparquet(
            cache_root=args.cache,
            output_dir=args.output,
            overwrite=args.overwrite,
        )
        print(f"written={len(written)}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
