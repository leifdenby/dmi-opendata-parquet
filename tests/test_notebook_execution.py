"""Test notebook execution for the final GeoParquet notebook.

The notebook is executed against a mocked cache so the test is deterministic
and does not depend on live DMI access.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dmi_geoparquet.cache import build_cache
from nbclient import NotebookClient
from nbformat import read as read_notebook


def test_notebook_executes(tmp_path: Path, monkeypatch) -> None:
    """Execute the demo notebook and ensure it writes plots."""

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

    def fake_fetch_json(url: str) -> dict[str, object]:
        query = parse_qs(urlparse(url).query)
        parameter_id = query["parameterId"][0]
        return {"features": features_by_parameter[parameter_id], "links": []}

    monkeypatch.setattr("dmi_geoparquet.partitioning._fetch_json", fake_fetch_json)

    cache_root = Path("tmp") / "cache"
    build_cache(
        start="2025-07-01",
        end="2025-08-01",
        output_dir=cache_root,
        parameters=["temp_dry", "temp_dew", "wind_speed"],
        partition_size="monthly",
        overwrite=True,
    )
    plots_root = tmp_path / "plots"
    monkeypatch.setenv("DMI_GEO_PARQUET_PLOTS", str(plots_root))
    monkeypatch.setenv("DMI_GEO_PARQUET_CACHE_ROOT", str(cache_root))

    notebook_path = Path("notebooks/final_geoparquet_demo.ipynb")
    with notebook_path.open("r", encoding="utf-8") as handle:
        notebook = read_notebook(handle, as_version=4)

    client = NotebookClient(notebook, timeout=120, kernel_name="python3")
    client.execute()

    assert any(plots_root.glob("*.png"))
