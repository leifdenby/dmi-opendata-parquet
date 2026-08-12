from __future__ import annotations

import argparse
import json
from pathlib import Path

from dmi_geoparquet.partitioning import build_sample_frame, default_sample_window, estimate_from_dmi, estimate_from_frame, estimate_from_parquet


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dmi-geoparquet")
    subparsers = parser.add_subparsers(dest="command", required=True)

    estimate = subparsers.add_parser("estimate-partitions", help="Estimate partitioning for the soft file size target")
    estimate.add_argument("--input", type=Path, help="Parquet file to analyze")
    estimate.add_argument("--dmi", action="store_true", help="Sample live DMI observations instead of using synthetic input")
    estimate.add_argument("--parameters", nargs="+", default=["temp_dry", "temp_dew", "wind_speed"], help="DMI parameter IDs to sample")
    estimate.add_argument("--datetime-start", help="Inclusive DMI datetime range start (RFC3339)")
    estimate.add_argument("--datetime-end", help="Inclusive DMI datetime range end (RFC3339)")
    estimate.add_argument("--sample-rows", type=int, default=10000, help="Synthetic rows to generate if no input is provided")
    estimate.add_argument("--soft-min-mb", type=float, default=10.0)
    estimate.add_argument("--soft-max-mb", type=float, default=50.0)
    estimate.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "estimate-partitions":
        if args.input:
            result = estimate_from_parquet(args.input, soft_min_mb=args.soft_min_mb, soft_max_mb=args.soft_max_mb)
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
            result = estimate_from_frame(frame, soft_min_mb=args.soft_min_mb, soft_max_mb=args.soft_max_mb)

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            chosen = result["chosen"]
            print(f"chosen={chosen['name']} estimated_mb={chosen['estimated_mb']:.2f} rows={chosen['rows']}")
            projection = result.get("projection")
            if projection:
                print(
                    f"projection rows/day={projection['rows_per_day']:.1f} rows/month={projection['rows_per_month']:.1f} rows/year={projection['rows_per_year']:.1f}"
                )
            for candidate in result["candidates"]:
                print(f"{candidate['name']}: {candidate['estimated_mb']:.2f} MB")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
