# DMI GeoParquet

DMI GeoParquet turns DMI `metObs` weather-station observations into GeoParquet datasets.

## Purpose

The package exists to make DMI weather-station data easy to:

- estimate before downloading
- cache locally in a deterministic layout
- build final partitioned GeoParquet outputs
- open as `xarray.Dataset` for analysis and plotting

## What It Does

- estimates partition sizes and fetch time from live DMI or local samples
- downloads observations into per-variable GeoParquet cache slices
- builds final GeoParquet partitions from the cache
- provides a reader for cached GeoParquet data as `xarray`
- provides a simple plotting helper for inspecting cached timeseries

Current scope:

- DMI weather stations only
- initial parameters: `temp_dry`, `temp_dew`, `wind_speed`

## How It Works

```mermaid
flowchart LR
  S["1. Estimate"]
  DMI[(DMI backend)]
  C["2. Download"]
  F["3. Build"]
  N[Notebook + plots]

  DMI -->|observations| S
  S -->|partition plan| C
  C -->|cache slices| F
  F -->|final GeoParquet| N
```

The pipeline is split into three stages:

1. `estimate-partitions`
   - samples DMI or local input
   - estimates file sizes for candidate partition schemes
   - estimates download time from the same sample

2. `build-cache`
   - downloads observations from the DMI API
   - writes GeoParquet cache files per parameter and time slice
   - uses a stable folder layout so runs are resumable and deterministic

3. `build-final`
   - reads the cache
   - normalizes year/month partition columns
   - writes the final partitioned GeoParquet dataset

### Design

- The code is organized into stage subpackages:
  - `dmi_geoparquet.estimate`
  - `dmi_geoparquet.download`
  - `dmi_geoparquet.build`
- The cache and final stages use `year` and `month` partition columns so output is predictable.
- Each stage is kept separate so it can be tested and run independently.
- The reader stays separate from the writers so analysis code does not depend on the download path.
- The cache layout is intentionally simple and stable:

```text
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

Example full-year raw backfill:

```bash
uv run dmi-geoparquet build-final \
  --cache tmp/cache \
  --download-missing-data \
  --start 2025-01-01 \
  --end 2026-01-01 \
  --output data/dmi
```

Defaults:

- cache output: `tmp/cache`
- plots output: `tmp/plots`
- final output: `data/dmi`

## Python API

- `dmi_geoparquet.open_dataset(...)` opens cached GeoParquet as `xarray.Dataset`
- `dmi_geoparquet.estimate` contains the estimation helpers
- `dmi_geoparquet.download` contains the cache builder
- `dmi_geoparquet.build` contains the final dataset builder

## Development

```bash
uv sync --dev
uv run ruff check .
uv run ty check
```
