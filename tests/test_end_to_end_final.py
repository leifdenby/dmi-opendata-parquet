"""End-to-end tests for the cache and final GeoParquet pipeline."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from dmi_geoparquet.build import build_final_geoparquet
from dmi_geoparquet.download import build_cache


def _make_feature(
    station_id: str, parameter_id: str, observed_at: str, value: float
) -> dict[str, object]:
    """Build a synthetic DMI GeoJSON feature."""

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [11.6, 55.7]},
        "properties": {
            "stationId": station_id,
            "parameterId": parameter_id,
            "observed": observed_at,
            "created": f"{observed_at[:10]}T00:05:00Z",
            "value": value,
        },
    }


def test_final_dataset_row_count_matches_mocked_api(monkeypatch, tmp_path: Path) -> None:
    """Build the full pipeline from a mocked API response and verify row counts."""

    expected_rows = 12
    features = [
        _make_feature("06156", "temp_dry", "2025-07-01T00:00:00Z", 15.1),
        _make_feature("06135", "temp_dry", "2025-07-01T00:10:00Z", 15.0),
        _make_feature("06156", "temp_dry", "2025-07-02T00:00:00Z", 14.8),
        _make_feature("06135", "temp_dry", "2025-07-02T00:10:00Z", 14.7),
        _make_feature("06156", "temp_dew", "2025-07-01T00:00:00Z", 13.2),
        _make_feature("06135", "temp_dew", "2025-07-01T00:10:00Z", 13.1),
        _make_feature("06156", "temp_dew", "2025-07-02T00:00:00Z", 13.0),
        _make_feature("06135", "temp_dew", "2025-07-02T00:10:00Z", 12.9),
        _make_feature("06156", "wind_speed", "2025-07-01T00:00:00Z", 1.5),
        _make_feature("06135", "wind_speed", "2025-07-01T00:10:00Z", 1.7),
        _make_feature("06156", "wind_speed", "2025-07-02T00:00:00Z", 1.6),
        _make_feature("06135", "wind_speed", "2025-07-02T00:10:00Z", 1.8),
    ]

    features_by_parameter = {
        "temp_dry": features[0:4],
        "temp_dew": features[4:8],
        "wind_speed": features[8:12],
    }

    def fake_fetch_json(url: str) -> dict[str, object]:
        assert url.startswith("https://opendataapi.dmi.dk/v2/metObs/collections/observation/items?")
        query = parse_qs(urlparse(url).query)
        parameter_id = query["parameterId"][0]
        return {"features": features_by_parameter[parameter_id], "links": []}

    monkeypatch.setattr("dmi_geoparquet.estimate.partitioning._fetch_json", fake_fetch_json)

    cache_root = tmp_path / "cache"
    final_root = tmp_path / "final"

    written_cache = build_cache(
        start="2025-07-01",
        end="2025-08-01",
        output_dir=cache_root,
        parameters=["temp_dry", "temp_dew", "wind_speed"],
        partition_size="monthly",
        overwrite=True,
    )
    assert len(written_cache) == 3

    written_final = build_final_geoparquet(
        cache_root=cache_root,
        output_dir=final_root,
        overwrite=True,
    )
    assert len(written_final) == 1

    frame = pd.read_parquet(written_final[0])
    assert len(frame) == expected_rows
    assert frame["observed_at"].nunique() == 4
    assert set(frame["parameter_id"].unique()) == {"temp_dry", "temp_dew", "wind_speed"}
