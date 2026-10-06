"""Tests for the periodic update stage."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from dmi_geoparquet.update import refresh_start_date, run_update_cycle, run_update_loop


def _fake_fetch_json_factory(value: float):
    """Create a fake DMI fetcher returning one July 2025 observation per parameter."""

    def fake_fetch_json(url: str) -> dict[str, object]:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        parameter_id = query["parameterId"][0]
        if not query["datetime"][0].startswith("2025-07"):
            return {"features": [], "links": []}
        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
            "properties": {
                "stationId": "06156",
                "parameterId": parameter_id,
                "observed": "2025-07-02T00:00:00Z",
                "created": "2025-07-02T00:05:00Z",
                "value": value,
            },
        }
        return {"features": [feature], "links": []}

    return fake_fetch_json


def test_refresh_start_date_is_month_aligned() -> None:
    """Refreshes start at the month containing the lookback date."""

    assert refresh_start_date(date(2025, 7, 15), 3) == date(2025, 7, 1)
    assert refresh_start_date(date(2025, 7, 2), 3) == date(2025, 6, 1)


def test_update_cycle_refreshes_current_month(tmp_path: Path) -> None:
    """A second cycle replaces the current month's data with fresh values."""

    cache_root = tmp_path / "cache"
    output_root = tmp_path / "final"
    kwargs = {
        "start": "2025-06-01",
        "cache_root": cache_root,
        "output_dir": output_root,
        "parameters": ["temp_dry"],
        "today": date(2025, 7, 10),
    }
    target = "dmi_geoparquet.estimate.partitioning._fetch_json"

    with patch(target, _fake_fetch_json_factory(15.0)):
        written = run_update_cycle(**kwargs)
    with patch(target, _fake_fetch_json_factory(16.0)):
        written = run_update_cycle(**kwargs)

    assert len(written) == 1
    frame = pd.read_parquet(written[0])
    assert frame["value"].tolist() == [16.0]
    assert not list(output_root.rglob("*.tmp"))


def test_update_loop_survives_failures() -> None:
    """A failing cycle is logged and the loop continues."""

    calls: list[int] = []

    def cycle() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    run_update_loop(interval_hours=0, cycle=cycle, max_cycles=2)
    assert len(calls) == 2


def test_update_cycle_writes_block_status(tmp_path: Path) -> None:
    """Every parameter/month block gets a final state in the status file."""

    import json

    output_root = tmp_path / "final"
    with patch("dmi_geoparquet.estimate.partitioning._fetch_json", _fake_fetch_json_factory(15.0)):
        run_update_cycle(
            start="2025-06-01",
            cache_root=tmp_path / "cache",
            output_dir=output_root,
            parameters=["temp_dry", "wind_speed"],
            today=date(2025, 7, 10),
        )

    status = json.loads((output_root / "_status.json").read_text())
    states = {key: block["state"] for key, block in status["blocks"].items()}
    assert states == {
        "temp_dry/2025-06": "empty",
        "wind_speed/2025-06": "empty",
        "temp_dry/2025-07": "done",
        "wind_speed/2025-07": "done",
    }
    assert status["cycle"]["state"] == "ok"
    assert status["partitions"]["2025-06"]["state"] == "empty"
    assert status["partitions"]["2025-07"]["state"] == "done"
    assert status["partitions"]["2025-07"]["rows"] == 2


def test_update_cycle_records_block_errors(tmp_path: Path) -> None:
    """A failing download marks its block as an error and other blocks still run."""

    import json

    good = _fake_fetch_json_factory(15.0)

    def flaky_fetch_json(url: str) -> dict[str, object]:
        if "parameterId=wind_speed" in url:
            raise OSError("DMI unavailable")
        return good(url)

    output_root = tmp_path / "final"
    with patch("dmi_geoparquet.estimate.partitioning._fetch_json", flaky_fetch_json):
        run_update_cycle(
            start="2025-07-01",
            cache_root=tmp_path / "cache",
            output_dir=output_root,
            parameters=["temp_dry", "wind_speed"],
            today=date(2025, 7, 10),
        )

    status = json.loads((output_root / "_status.json").read_text())
    assert status["blocks"]["temp_dry/2025-07"]["state"] == "done"
    assert status["blocks"]["wind_speed/2025-07"]["state"] == "error"
    assert "DMI unavailable" in status["blocks"]["wind_speed/2025-07"]["error"]
    assert status["partitions"]["2025-07"]["state"] == "done"
    assert status["cycle"]["state"] == "error"


def test_month_final_file_is_built_before_next_month_downloads(tmp_path: Path) -> None:
    """Each month's final file exists before the following month is fetched."""

    output_root = tmp_path / "final"
    july_final = output_root / "raw" / "year=2025" / "month=07" / "part-00000.parquet"
    good = _fake_fetch_json_factory(15.0)
    july_final_seen_when_fetching_august: list[bool] = []

    def recording_fetch_json(url: str) -> dict[str, object]:
        if "2025-08" in url.split("datetime=")[1][:7]:
            july_final_seen_when_fetching_august.append(july_final.exists())
        return good(url)

    with patch("dmi_geoparquet.estimate.partitioning._fetch_json", recording_fetch_json):
        run_update_cycle(
            start="2025-07-01",
            cache_root=tmp_path / "cache",
            output_dir=output_root,
            parameters=["temp_dry", "wind_speed"],
            today=date(2025, 8, 10),
        )

    assert july_final_seen_when_fetching_august == [True, True]
