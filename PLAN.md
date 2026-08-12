# DMI Weather Station GeoParquet Plan

## Goal
Build a Python package that ingests DMI meteorological observations for weather stations and writes GeoParquet outputs for both hourly and monthly use cases.

## Scope
- Source: DMI `metObs` weather-station observations.
- Initial target: weather stations only.
- Outputs:
  - raw normalized observations
  - hourly materializations
  - monthly materializations
- Format: GeoParquet with station geometry.

## DMI Cadence Model
Use DMI's documented parameter update frequencies as expected cadence, not as a hard guarantee.

Cadence classes:
- `ten_min`
- `hourly`
- `twice_daily`
- `daily`
- `event_driven`

Examples:
- `ten_min`: `temp_dry`, `temp_dew`, `humidity`, `pressure`, `pressure_at_sea`, `wind_dir`, `wind_speed`, `wind_max`, `wind_min`, `precip_past10min`, `visibility`, `cloud_cover`, `weather`, `radia_glob`
- `hourly`: `temp_mean_past1h`, `temp_max_past1h`, `temp_min_past1h`, `humidity_past1h`, `wind_speed_past1h`, `precip_past1h`, `precip_dur_past1h`, `sun_last1h_glob`
- `twice_daily`: `temp_max_past12h`, `temp_min_past12h`
- `daily`: `precip_past24h`, `snow_depth_man`, `snow_cover_man`
- `event_driven`: `precip_past1min`

Important notes:
- Greenland precipitation may be manual and arrive monthly.
- Some Greenland stations only report hourly.
- Cadence should support station-specific overrides.

## Data Model
### Canonical observation row
Suggested fields:
- `station_id`
- `parameter_id`
- `observed_at`
- `value`
- `unit`
- `latitude`
- `longitude`
- `geometry`
- `source_created_at`
- `cadence`
- `cadence_note`

### Station metadata
Separate station table containing:
- `station_id`
- `name`
- `geometry`
- `latitude`
- `longitude`
- `country_or_region`
- `station_type`

## Package Design
Proposed modules:
- `dmi_geoparquet/client.py` for API access
- `dmi_geoparquet/models.py` for typed records
- `dmi_geoparquet/cadence.py` for cadence registry and overrides
- `dmi_geoparquet/normalize.py` for cleaning raw API payloads
- `dmi_geoparquet/aggregate.py` for hourly and monthly rollups
- `dmi_geoparquet/write.py` for GeoParquet output
- `dmi_geoparquet/cli.py` for command-line entry points

## Materialization Strategy
1. Fetch raw DMI observations.
2. Normalize into a canonical table.
3. Join station geometry.
4. Aggregate into:
   - hourly rows by `station_id + hour`
   - monthly rows by `station_id + month`
5. Write partitioned GeoParquet, preferably by `year` and `month`.

## V1 Scope
The first version should focus on a minimal, useful subset of DMI weather-station data.

Initial parameters:
- `temp_dry` for 2 m air temperature
- `temp_dew` for dew point temperature
- `wind_speed` for wind speed

V1 goals:
- download the three parameters
- store them in a wide observation table
- estimate partitioning that fits the file size soft target
- write GeoParquet files using the chosen partitioning scheme

Recommended v1 table shape:
- one row per `station_id + observed_at`
- one column per selected parameter
- station geometry included in each row

Partitioning estimator:
- write a representative sample to Parquet with the real writer settings
- measure compressed bytes per row
- estimate resulting file sizes for candidate partitions
- choose the coarsest partitioning that stays near the soft target

Candidate partition schemes to test:
- `year/month`
- `year/month/day`
- `year/month/week`
- `year/month/station_bucket`
- combinations of `week` and `station_bucket` if needed

Expected v1 preference:
- start with `year/month`
- split more finely only if estimates exceed the soft file limit

## Partition Estimation CLI
Implement a separate CLI that can be run independently before the rest of the package is built.

Purpose:
- estimate which partitioning scheme best fits the soft whole-file size target
- run on representative or synthetic input before any production export exists

Planned behavior:
- accept a parquet file or generated sample data as input
- evaluate candidate partition schemes
- estimate compressed file size for each scheme
- report the best-fit partitioning layout
- optionally emit JSON for scripting and automation

Suggested command:
- `dmi-geoparquet estimate-partitions`

This CLI should be implemented and validated before further package work.

Progress so far:
- implemented a standalone `estimate-partitions` CLI
- added synthetic sampling mode for quick checks
- added live DMI sampling mode using the `metObs` observations endpoint
- switched live sampling to a one-day window one year back by default
- extrapolated rows/day, rows/month, and rows/year from the one-day sample
- kept file-size estimation focused on the soft whole-file target
- added fetch-time estimation from the same live DMI sample
- consolidated storage and runtime planning into one planner CLI flow
- added a `build-cache` CLI that writes GeoParquet cache files per variable and time slice
- validated the cache builder on a two-month run with monthly slices

## Storage Layout
Use a single dataset root with separate subfolders per resolution.

Recommended layout:
```text
data/
  dmi/
    raw/
      year=YYYY/month=MM/part-00000.parquet
    hourly/
      year=YYYY/month=MM/part-00000.parquet
    monthly/
      year=YYYY/month=MM/part-00000.parquet
```

Rules:
- partition first by resolution, then by year/month
- use `part-xxxxx.parquet` file names inside each partition
- keep station geometry in each output file
- keep the layout stable across backfills and reruns

## File Sizing Policy
Optimize for whole-file size, not row-group size.

Target behavior:
- soft target file size: `25MB` compressed
- acceptable range: `10MB-50MB` compressed
- small partitions may produce smaller files
- larger partitions may produce multiple files

Writer behavior:
- buffer rows until the projected file size is near the target
- flush to a new `part-xxxxx.parquet` file when needed
- split within each `resolution/year/month` partition
- leave row-group sizing to the Parquet writer defaults unless tuning is needed later

## Xarray Access Layer
Provide a reader that opens the generated GeoParquet files as an `xarray.Dataset`.

Suggested API:
- `open_dataset(path, resolution="hourly", ...)`

Dataset shape for v1:
- use an `obs` dimension for parquet rows
- coords:
  - `time`
  - `station_id`
  - `latitude`
  - `longitude`
- data variables:
  - `air_temperature`
  - `dew_point_temperature`
  - `wind_speed`

CF-style metadata:
- map Parquet columns to CF-style variable names
- attach `standard_name` and `units` to each data variable
- preserve station and time metadata in the dataset

Mapping examples:
- `temp_dry -> air_temperature`
- `temp_dew -> dew_point_temperature`
- `wind_speed -> wind_speed`

Reader behavior:
- load one or more Parquet files from the dataset root
- allow filtering by resolution and time partition
- keep the reader separate from the writer and aggregator

## Aggregation Rules
- Keep aggregation logic parameter-aware.
- Preserve raw timestamps in the normalized layer.
- Derive hourly/monthly summaries from normalized data, not from already-aggregated outputs.
- Make aggregation function configurable per parameter where needed.

## CLI
Planned command shape:
- `dmi-geoparquet fetch`
- `dmi-geoparquet build hourly`
- `dmi-geoparquet build monthly`
- `dmi-geoparquet build both`

Options should include:
- date range
- station filter
- output directory
- overwrite / append behavior

## Testing
Add tests for:
- cadence registry lookup
- parameter-to-cadence mapping
- normalization of sample API payloads
- station geometry creation
- hourly and monthly grouping logic
- GeoParquet schema and partition naming

## Milestones
1. Create project scaffold and packaging metadata.
2. Implement API client and normalized models.
3. Add cadence registry from DMI documentation.
4. Implement aggregation and GeoParquet writing.
5. Add CLI.
6. Add tests and sample fixtures.

## Open Questions
- Which exact DMI endpoints should be used for station metadata and observations.
- Whether to store all raw observations permanently or only derived hourly/monthly outputs.
- Whether monthly output should be one file per month or a partitioned dataset.
