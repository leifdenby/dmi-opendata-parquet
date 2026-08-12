# GeoParquet Format: Granularity and Structure

This document describes the hierarchical structure of GeoParquet datasets, including the granularity levels of each component and how they relate to one another.

---

## Granularity Levels

The following table outlines the components of a GeoParquet dataset, their granularity levels, and their relationships:

| **Term**               | **Granularity Level** | **Exists Within**                     | **Example**                                                                                     |
|------------------------|-----------------------|---------------------------------------|-------------------------------------------------------------------------------------------------|
| **Dataset (or Dataset URI)** | 0 (Highest)      | -                                     | `s3://bucket/data/` or `station_data/` (root directory containing all partitions).              |
| **Partition**          | 1                     | Dataset                               | `s3://bucket/data/year=2020/` (directory, organized by columns like "year", "month").          |
| **File**               | 2                     | Partition                             | `s3://bucket/data/year=2020/month=01.parquet`.                                                   |
| **File Header**        | 3                     | File                                  | Contains schema and metadata at the start of the file.                                         |
| **File Metadata**      | 3                     | File                                  | Statistics and metadata at the end of the file (e.g., row group offsets).                     |
| **Schema**             | 4                     | File Header                           | Metadata describing columns, types, and `geo_metadata`.                                      |
| **`geo_metadata`**     | 5                     | Schema                                | `{"bbox": [10.0, 20.0, 30.0, 40.0], "crs": "EPSG:4326"}` in the file’s schema.                     |
| **Bounding Box (bbox)**| 6                     | `geo_metadata`                        | `[10.0, 20.0, 30.0, 40.0]` for a file.                                                          |
| **Row Group**          | 3                     | File                                  | A chunk of 100K rows inside `month=01.parquet`.                                                |
| **Row**                | 4                     | Row Group                             | A single observation (e.g., a station measurement).                                           |
| **Space-Filling Curve (SFC)** | 5               | Row (per observation)                | H3 index `8a234bcd9876543` for a point at `(lat, lon)`.                                          |

---

## Hierarchy Diagram

```
Dataset (Level 0, e.g., s3://bucket/data/ or station_data/)
│
└── Partition (Level 1, e.g., year=2020/, organized by columns: "year", "month")
    │
    └── File (Level 2, e.g., month=01.parquet)
        │
        ├── File Header (Level 3)
        │   └── Schema (Level 4)
        │       └── geo_metadata (Level 5)
        │           └── Bounding Box (bbox) (Level 6)
        │
        ├── Row Group (Level 3)
        │   └── Row (Level 4)
        │       └── Space-Filling Curve (SFC) (Level 5)
        │
        └── File Metadata (Level 3)
```

---

## Key Concepts

### Dataset
- The **root directory** containing all partitions and files.
- Example: `s3://bucket/data/` or `station_data/`.

### Partition
- A **directory** that groups files based on one or more columns (e.g., `year`, `month`).
- Used for **partition pruning**: skipping entire directories if they don’t match query filters.

### Partition Column
- The **column(s)** used to organize data into partitions.
- Example: `year=2020/month=01/` is partitioned by the `year` and `month` columns.

### File
- A **single Parquet file** (e.g., `month=01.parquet`).
- Contains a **header**, **metadata**, and one or more **row groups**.

### File Header
- Located at the **start of the file**.
- Contains:
  - **Schema**: Column names, data types, and other structural metadata.

### File Metadata
- Located at the **end of the file**.
- Contains:
  - Row group offsets (for random access).
  - Statistics (e.g., min/max values for columns).

### Schema
- Defines the **structure** of the data (columns, types, etc.).
- Includes **`geo_metadata`** for GeoParquet files.

### `geo_metadata`
- A **GeoParquet-specific** key in the schema.
- Stores:
  - `bbox`: The bounding box of the file’s geometries (`[xmin, ymin, xmax, ymax]`).
  - `crs`: The coordinate reference system (e.g., `EPSG:4326`).

### Bounding Box (bbox)
- The **spatial extent** of the geometries in the file.
- Used for **file-level spatial pruning** (e.g., DuckDB skips files whose bbox doesn’t intersect the query bbox).

### Row Group
- A **chunk of rows** within a file (e.g., 100K rows).
- The smallest unit of I/O in Parquet.
- Tools like PyArrow can read specific row groups, but DuckDB cannot.
- **Physical order**: Row groups appear *after* the File Header and *before* the File Metadata in the file.
- **Contains**: Rows (Level 4).

### Row
- A **single observation** (e.g., a station measurement).
- Contains all columns for that observation, including geometry.

### Space-Filling Curve (SFC)
- A **1D representation** of a 2D coordinate (e.g., H3, S2, Morton).
- Used to **cluster spatially nearby rows** within a file or partition.
- Example: Sorting rows by H3 index groups nearby observations together.

---

## Practical Implications

### Partition Pruning
- **Tools**: DuckDB, PyArrow, Spark.
- **How it works**: Skips entire **partition directories** (Level 1) if they don’t match filters (e.g., `year=2020`).

### File-Level Pruning
- **Tools**: DuckDB (with GeoParquet metadata).
- **How it works**: Skips entire **files** (Level 2) if their `bbox` doesn’t intersect the query bbox.

### Row Group Pruning
- **Tools**: PyArrow (not DuckDB).
- **How it works**: Skips **row groups** (Level 3) if their scalar column stats (e.g., `min(time)`) don’t match filters.

### Spatial Queries
- **Without metadata/index**: Spatial filters are applied **after downloading** the data.
- **With GeoParquet metadata**: DuckDB can skip **files** (Level 2) that don’t intersect the query bbox.
- **With SFC sorting**: Rows within a file are clustered by space, improving cache efficiency.

---

## Example Workflow

### Writing Data
1. Assign a **space-filling curve** (e.g., H3) to each row for spatial clustering.
2. Sort the data by the SFC and time columns.
3. Write to **partitioned GeoParquet** (e.g., by `year` and `month`).

### Querying Data
1. DuckDB **prunes partitions** (Level 1) based on time filters (e.g., `year=2020`).
2. DuckDB **prunes files** (Level 2) based on `geo_metadata.bbox`.
3. DuckDB **downloads the remaining files** and applies spatial filters (e.g., `ST_Within`).
4. For finer granularity, use PyArrow to read specific **row groups** (Level 3).

---

## Filtering Capabilities by Tool

The following table outlines the filtering capabilities of **DuckDB** and **DuckDB + PyArrow** at each level of the dataset hierarchy, including the type of subset (e.g., partition directory, file, or byte range) used for filtering:

| **Level**               | **DuckDB**                                                                                     | **DuckDB + PyArrow**                                                                                     |
|-------------------------|-------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| **Dataset (Level 0)**   | ❌ No filtering                                                                                 | ❌ No filtering                                                                                       |
| **Partition (Level 1)** | ✅ **Partition pruning**: Skips entire **partition directories** (e.g., `year=2019/`) if they don’t match scalar filters (e.g., `WHERE year=2020`). | ✅ Same as DuckDB alone.                                                                               |
| **File (Level 2)**      | ✅ **File-level pruning**: Skips entire **files** if their `geo_metadata.bbox` doesn’t intersect the query bbox (e.g., `ST_Within(geometry, ST_Bbox(...))`). | ✅ Same as DuckDB alone.                                                                               |
| **Row Group (Level 3)** | ❌ **No direct support**: Downloads entire file, then filters rows.                          | ✅ **Range request within file**: Uses PyArrow to fetch **only the byte ranges** of selected row groups (e.g., `row_groups=[0, 2]`). |
| **Row (Level 4)**       | ✅ **Row-level filtering**: Applies spatial/scalar filters (e.g., `ST_Within`) *after* downloading the file. | ✅ Same as DuckDB alone.                                                                               |
| **Space-Filling Curve (SFC) (Level 5)** | ✅ **Row-level filtering**: Filters rows by SFC values (e.g., `WHERE h3_index LIKE '8a234%'`). | ✅ Same as DuckDB alone.

### Key Clarifications
- **DuckDB alone**:
  - **Partition (Level 1)**: Skips entire **directories** (no download).
  - **File (Level 2)**: Skips entire **files** (no download) if their bbox doesn’t intersect the query.
  - **Row Group (Level 3)**: ❌ **Downloads entire file**, then filters rows.
  - **Row (Level 4)**: Filters rows *after* download.

  - **DuckDB + PyArrow**:
    - **Row Group (Level 3)**: ✅ **Fetches only the byte ranges** of selected row groups (no full file download).
    - All other levels: Same as DuckDB alone.

---

## Row Group-Level Spatial Metadata (Future Enhancement)

Currently, **GeoParquet only supports file-level bounding boxes** (`geo_metadata.bbox`). However, for advanced use cases (e.g., large files with spatially clustered data), **row group-level bounding boxes** could enable finer-grained spatial pruning. Below is a conceptual outline for how this might work.

### Why It’s Useful
- **Finer pruning**: Skip row groups that don’t intersect a query bbox, reducing I/O.
- **Efficiency**: Avoid downloading entire files when only a subset of row groups are relevant.

### How It Could Work
| Component               | Location               | Example Content                          | Notes                                  |
|-------------------------|------------------------|------------------------------------------|----------------------------------------|
| **Row Group bbox**      | File Metadata          | `[xmin, ymin, xmax, ymax]` per row group | Stored alongside scalar column stats. |
| **Custom Metadata**     | Schema or Sidecar      | `{"row_group_bboxes": {...}}`            | Non-standard; requires tool support. |

### Example Implementation (Pseudocode)
```python
# Hypothetical: Writing a Parquet file with row group-level bboxes
import pyarrow as pa
import pyarrow.parquet as pq

# Compute bboxes for each row group (custom logic)
row_group_bboxes = {
    0: [10.0, 20.0, 30.0, 40.0],  # Row group 0 bbox
    1: [15.0, 25.0, 35.0, 45.0],  # Row group 1 bbox
}

# Add to schema metadata (non-standard)
schema = pa.schema([...])
schema = schema.with_metadata({
    b"row_group_bboxes": str(row_group_bboxes).encode()
})

# Write the file
pq.write_table(table, "output.parquet", schema=schema)
```

### Suggested GeoParquet Enhancement
If you’d like to propose this to the GeoParquet community, consider:
1. **Extending the spec** to include optional `row_group_bboxes` in `geo_metadata`.
2. **Tooling support**: Ensure DuckDB, PyArrow, and other tools can read/use this metadata.
3. **Backward compatibility**: Make it optional to avoid breaking existing files.

### Workaround for Now
Use a **sidecar index** (e.g., R-tree) to track row group bboxes externally. Example:
```json
{
  "file.parquet": {
    "row_group_0": {"bbox": [10, 20, 30, 40], "byte_offset": 0},
    "row_group_1": {"bbox": [15, 25, 35, 45], "byte_offset": 1000000}
  }
}
```
