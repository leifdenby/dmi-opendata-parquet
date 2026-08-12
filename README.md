# DMI GeoParquet

<svg width="900" height="170" viewBox="0 0 900 170" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Estimate, download, build workflow">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#334155" />
    </marker>
    <style>
      .box { fill: #f8fafc; stroke: #334155; stroke-width: 2; rx: 14; ry: 14; }
      .title { font: 700 22px sans-serif; fill: #0f172a; }
      .body { font: 16px sans-serif; fill: #334155; }
      .arrow { stroke: #334155; stroke-width: 3; fill: none; marker-end: url(#arrow); }
    </style>
  </defs>

  <rect class="box" x="20" y="35" width="240" height="100"/>
  <text class="title" x="140" y="68" text-anchor="middle">1. Estimate</text>
  <text class="body" x="140" y="96" text-anchor="middle">sample DMI once</text>
  <text class="body" x="140" y="118" text-anchor="middle">size + download plan</text>

  <rect class="box" x="330" y="35" width="240" height="100"/>
  <text class="title" x="450" y="68" text-anchor="middle">2. Download</text>
  <text class="body" x="450" y="96" text-anchor="middle">write cached GeoParquet</text>
  <text class="body" x="450" y="118" text-anchor="middle">per variable and slice</text>

  <rect class="box" x="640" y="35" width="240" height="100"/>
  <text class="title" x="760" y="68" text-anchor="middle">3. Build</text>
  <text class="body" x="760" y="96" text-anchor="middle">convert cache to final</text>
  <text class="body" x="760" y="118" text-anchor="middle">GeoParquet dataset</text>

  <path class="arrow" d="M 260 85 L 320 85"/>
  <path class="arrow" d="M 570 85 L 630 85"/>
</svg>

DMI GeoParquet is a small pipeline for turning DMI weather-station observations into GeoParquet datasets.

## Workflow

- `estimate-partitions`: sample DMI, estimate file sizes, and estimate fetch time.
- `build-cache`: download observations into per-variable GeoParquet cache slices.
- `build-final`: convert the cache into the final partitioned GeoParquet dataset.
- `plot-timeseries`: inspect cached data as raw station-by-station timeseries plots.

## Current scope

- DMI weather stations only.
- Initial variables:
  - `temp_dry`
  - `temp_dew`
  - `wind_speed`
- Cache slices are GeoParquet files.
- Final dataset is written as partitioned GeoParquet.

## Layout

```text
cache/
  temp_dry/monthly/year=2025/month=07/part-00000.parquet
  temp_dew/monthly/year=2025/month=07/part-00000.parquet
  wind_speed/monthly/year=2025/month=07/part-00000.parquet

tmp/
  cache/
    temp_dry/monthly/year=2025/month=07/part-00000.parquet
    temp_dew/monthly/year=2025/month=07/part-00000.parquet
    wind_speed/monthly/year=2025/month=07/part-00000.parquet

data/
  dmi/raw/year=2025/month=07/part-00000.parquet
```

## CLI

```text
dmi-geoparquet estimate-partitions
dmi-geoparquet build-cache
dmi-geoparquet build-final
dmi-geoparquet plot-timeseries
```

## Python API

- `dmi_geoparquet.open_dataset(...)` returns an `xarray.Dataset` from cached files.

## Development

Use `uv` for dependency management and command execution.

```bash
uv sync --dev
uv run ruff check .
uv run ty check
```
