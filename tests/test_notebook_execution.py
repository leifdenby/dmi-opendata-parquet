"""Test notebook execution for the final GeoParquet notebook."""

from __future__ import annotations

from pathlib import Path

from nbclient import NotebookClient
from nbformat import read as read_notebook


def test_notebook_executes(
    tmp_path: Path,
    monkeypatch,
    final_dataset_root: Path,
) -> None:
    """Execute the demo notebook and ensure it writes plots."""

    plots_root = tmp_path / "plots"
    monkeypatch.setenv("DMI_GEO_PARQUET_PLOTS", str(plots_root))
    monkeypatch.setenv("DMI_GEO_PARQUET_FINAL_ROOT", str(final_dataset_root))

    notebook_path = Path("notebooks/final_geoparquet_demo.ipynb")
    with notebook_path.open("r", encoding="utf-8") as handle:
        notebook = read_notebook(handle, as_version=4)

    client = NotebookClient(notebook, timeout=120, kernel_name="python3")
    client.execute()

    assert any(plots_root.glob("*.png"))
