"""Shared pytest fixtures for the repository."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from dmi_geoparquet.build import build_final_geoparquet
from dmi_geoparquet.download import build_cache


def _fake_fetch_json_factory() -> Callable[[str], dict[str, object]]:
    """Create a deterministic fake DMI JSON fetcher."""

    features_by_parameter = {
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
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "temp_dry",
                    "observed": "2025-07-01T00:10:00Z",
                    "created": "2025-07-01T00:15:00Z",
                    "value": 15.0,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06156",
                    "parameterId": "temp_dry",
                    "observed": "2025-07-02T00:00:00Z",
                    "created": "2025-07-02T00:05:00Z",
                    "value": 14.8,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "temp_dry",
                    "observed": "2025-07-02T00:10:00Z",
                    "created": "2025-07-02T00:15:00Z",
                    "value": 14.7,
                },
            },
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
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "temp_dew",
                    "observed": "2025-07-01T00:10:00Z",
                    "created": "2025-07-01T00:15:00Z",
                    "value": 13.1,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06156",
                    "parameterId": "temp_dew",
                    "observed": "2025-07-02T00:00:00Z",
                    "created": "2025-07-02T00:05:00Z",
                    "value": 13.0,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "temp_dew",
                    "observed": "2025-07-02T00:10:00Z",
                    "created": "2025-07-02T00:15:00Z",
                    "value": 12.9,
                },
            },
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
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "wind_speed",
                    "observed": "2025-07-01T00:10:00Z",
                    "created": "2025-07-01T00:15:00Z",
                    "value": 1.7,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06156",
                    "parameterId": "wind_speed",
                    "observed": "2025-07-02T00:00:00Z",
                    "created": "2025-07-02T00:05:00Z",
                    "value": 1.6,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
                "properties": {
                    "stationId": "06135",
                    "parameterId": "wind_speed",
                    "observed": "2025-07-02T00:10:00Z",
                    "created": "2025-07-02T00:15:00Z",
                    "value": 1.8,
                },
            },
        ],
    }

    def fake_fetch_json(url: str) -> dict[str, object]:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        parameter_id = query["parameterId"][0]
        return {"features": features_by_parameter[parameter_id], "links": []}

    return fake_fetch_json


@pytest.fixture(scope="session")
def final_dataset_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build and return a final GeoParquet dataset for notebook tests."""

    cache_root = tmp_path_factory.mktemp("cache")
    final_root = Path("data") / "dmi"
    with patch("dmi_geoparquet.estimate.partitioning._fetch_json", _fake_fetch_json_factory()):
        build_cache(
            start="2025-07-01",
            end="2025-08-01",
            output_dir=cache_root,
            parameters=["temp_dry", "temp_dew", "wind_speed"],
            partition_size="monthly",
            overwrite=True,
        )
        build_final_geoparquet(cache_root=cache_root, output_dir=final_root, overwrite=True)
    return final_root


@pytest.fixture(scope="session", autouse=True)
def ensure_final_dataset_root(final_dataset_root: Path) -> None:
    """Ensure the shared final dataset exists before notebook execution."""

    _ = final_dataset_root
