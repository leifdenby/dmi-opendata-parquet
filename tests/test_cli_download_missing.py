"""CLI tests for backfilling missing cache data before final build."""

from __future__ import annotations

from pathlib import Path

from dmi_geoparquet.cli import main


def test_build_final_can_download_missing_data(tmp_path: Path) -> None:
    """The final build CLI can backfill missing cache data before building."""

    def fake_fetch_json(url: str) -> dict[str, object]:
        from urllib.parse import parse_qs, urlparse

        parameter_id = parse_qs(urlparse(url).query)["parameterId"][0]
        features = {
            "temp_dry": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                    "properties": {
                        "stationId": "06156",
                        "parameterId": "temp_dry",
                        "observed": "2025-07-01T00:00:00Z",
                        "created": "2025-07-01T00:05:00Z",
                        "value": 15.1,
                    },
                }
            ],
            "temp_dew": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                    "properties": {
                        "stationId": "06156",
                        "parameterId": "temp_dew",
                        "observed": "2025-07-01T00:00:00Z",
                        "created": "2025-07-01T00:05:00Z",
                        "value": 13.2,
                    },
                }
            ],
            "wind_speed": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                    "properties": {
                        "stationId": "06156",
                        "parameterId": "wind_speed",
                        "observed": "2025-07-01T00:00:00Z",
                        "created": "2025-07-01T00:05:00Z",
                        "value": 1.5,
                    },
                }
            ],
        }
        return {"features": features[parameter_id], "links": []}

    from unittest.mock import patch

    with patch("dmi_geoparquet.estimate.partitioning._fetch_json", fake_fetch_json):
        cache_root = tmp_path / "cache"
        output_root = tmp_path / "final"

        exit_code = main(
            [
                "build-final",
                "--cache",
                str(cache_root),
                "--download-missing-data",
                "--start",
                "2025-07-01",
                "--end",
                "2025-08-01",
                "--output",
                str(output_root),
            ]
        )

    assert exit_code == 0
    assert any(output_root.rglob("*.parquet"))
