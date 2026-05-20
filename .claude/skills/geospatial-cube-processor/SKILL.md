---
name: geospatial-cube-processor
description: >
  Expert at writing geospatial processing functions inline into the user's existing Python script or notebook.
  Use this skill whenever the user wants to: mask or clip raster data to an admin boundary or country,
  stack or combine multiple xarray Datasets into a single cube, compute zonal statistics or aggregate
  raster values by region or time period, or export raster data as Cloud Optimized GeoTIFF (COG).
  Works with any gridded source — climate, soil, elevation, land use — as long as it uses standard
  lat/lon dimensions and EPSG:4326 CRS. Also triggers when the user mentions GADM boundaries, rioxarray
  clipping, xarray merging, zonal stats, or COG export in the context of spatial analysis.
  IMPORTANT: invoke this skill any time the user is working with xarray Datasets and wants to do
  spatial operations, even if they don't use these exact terms — phrases like "clip to Ethiopia",
  "combine my rasters", "average rainfall per district", or "save as GeoTIFF" are all strong signals.
---

# Geospatial Cube Processor

This skill teaches Claude how to write three fully generic geospatial functions **directly into the user's existing script or notebook**. No new package is scaffolded. All functions use only `xarray`, `rioxarray`, `geopandas`, `pandas`, and `numpy` — already available in the climate-data-download skill environment.

Each function is independent. Write only the ones the user needs.

---

## Before writing any code — confirm with the user

Ask only what is unknown:

- **Masking**: Admin level (0 = country, 1 = province, 2 = district)? Local shapefile or fetch from GADM?
- **Stacking**: Target resolution — "coarsest", "finest", or a specific value in degrees? Resampling method (default: bilinear)?
- **Summarizing**: Aggregation method (mean / sum / min / max)? Temporal frequency — monthly (`"ME"`), seasonal (`"QE"`), annual (`"YE"`), or none? Export to CSV?
- **COG export**: Which variables to export? Output directory?

---

## Output standards (apply to all xarray Dataset outputs)

Every xarray Dataset returned must carry STAC-compliant attributes. Add them via `.attrs.update({...})`:

| Attribute | Value |
|-----------|-------|
| `datetime` | `datetime.utcnow().isoformat() + "Z"` |
| `bbox` | `list(ds.rio.bounds())` — `[min_lon, min_lat, max_lon, max_lat]` |
| `geometry` | GeoJSON dict of spatial footprint (`gdf.unary_union.__geo_interface__` or `box(*ds.rio.bounds()).__geo_interface__`) |
| `platform` | Data source name, e.g. `"CHIRPS"`, `"SoilGrids"`, `"multi-source"` |
| `instruments` | `list(ds.data_vars)` |
| `proj:epsg` | `4326` |

Always include `from datetime import datetime` when using `datetime.utcnow()`.

COG export pattern (per variable):
```python
ds["var_name"].rio.to_raster("output.tif", driver="COG")
```

---

## Function 1 — `mask_to_admin` (Masking)

Clips any xarray Dataset to an admin boundary. Use when the user says "clip to", "mask by", "subset to country/region", or gives a country name or ISO3 code.

```python
from datetime import datetime
import xarray as xr
import rioxarray
import geopandas as gpd

def mask_to_admin(
    ds: xr.Dataset,
    country_iso3: str,
    admin_level: int = 0,
    shapefile_path: str = None
) -> xr.Dataset:
    if shapefile_path:
        gdf = gpd.read_file(shapefile_path)
    else:
        url = f"https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{country_iso3}_{admin_level}.json"
        gdf = gpd.read_file(url)

    gdf = gdf.to_crs("EPSG:4326")

    ds = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)
    ds = ds.rio.write_crs("EPSG:4326", inplace=True)
    masked = ds.rio.clip(gdf.geometry, gdf.crs, drop=True, all_touched=True)

    masked.attrs.update({
        "datetime": datetime.utcnow().isoformat() + "Z",
        "bbox": list(gdf.total_bounds),
        "geometry": gdf.unary_union.__geo_interface__,
        "platform": ds.attrs.get("platform", "unknown"),
        "instruments": list(masked.data_vars),
        "proj:epsg": 4326,
    })
    return masked
```

**Key notes:**
- `admin_level=0` → national boundary, `1` → province/state, `2` → district
- `all_touched=True` preserves pixels touching the boundary (important for coarse grids)
- Static layers (no `time` dim) and time-varying layers are both handled transparently by `rio.clip`

---

## Function 2 — `stack_datasets` (Stacking)

Reprojects and merges multiple xarray Datasets into one spatially aligned cube. Use when the user says "combine", "stack", "merge rasters", "align grids", or "create a multi-variable cube".

```python
from datetime import datetime
import xarray as xr
import rioxarray
import numpy as np

def stack_datasets(
    datasets: list,
    target_resolution: float | str = "coarsest",
    resampling_method: str = "bilinear"
) -> xr.Dataset:
    prepared = []
    for ds in datasets:
        ds = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)
        ds = ds.rio.write_crs("EPSG:4326", inplace=True)
        prepared.append(ds)

    resolutions = [abs(float(ds.rio.resolution()[0])) for ds in prepared]

    if target_resolution == "coarsest":
        ref_idx = int(np.argmax(resolutions))
    elif target_resolution == "finest":
        ref_idx = int(np.argmin(resolutions))
    else:
        ref_idx = int(np.argmin([abs(r - float(target_resolution)) for r in resolutions]))

    reference = prepared[ref_idx]

    reprojected = []
    for ds in prepared:
        if ds is reference:
            reprojected.append(ds)
        else:
            reprojected.append(
                ds.rio.reproject_match(reference, resampling=resampling_method)
            )

    merged = xr.merge(reprojected, compat="override", join="override")

    merged.attrs.update({
        "datetime": datetime.utcnow().isoformat() + "Z",
        "bbox": list(merged.rio.bounds()),
        "geometry": {"type": "Polygon", "coordinates": [[
            [merged.rio.bounds()[0], merged.rio.bounds()[1]],
            [merged.rio.bounds()[2], merged.rio.bounds()[1]],
            [merged.rio.bounds()[2], merged.rio.bounds()[3]],
            [merged.rio.bounds()[0], merged.rio.bounds()[3]],
            [merged.rio.bounds()[0], merged.rio.bounds()[1]],
        ]]},
        "platform": "multi-source",
        "instruments": list(merged.data_vars),
        "proj:epsg": 4326,
    })
    return merged
```

**Key notes:**
- Static layers (no `time` dim) merge naturally with time-varying layers via `xr.merge`
- `reproject_match` handles reprojection and spatial resampling in one step
- `compat="override"` prevents errors when attributes differ across sources
- The geometry inline above can be replaced with `shapely.geometry.box(*merged.rio.bounds()).__geo_interface__` if shapely is available

---

## Function 3 — `summarize_by_admin` (Zonal Statistics)

Computes per-admin-unit statistics from a Dataset, returning a tidy DataFrame. Use when the user says "average per district", "zonal stats", "summarize by region", "rainfall per province", or wants a table of values.

```python
from datetime import datetime
import xarray as xr
import geopandas as gpd
import pandas as pd
import rioxarray

def summarize_by_admin(
    ds: xr.Dataset,
    boundaries: gpd.GeoDataFrame,
    agg_method: str = "mean",
    temporal_freq: str = None,
    output_csv: str = None
) -> pd.DataFrame:
    agg_fn = agg_method  # "mean", "sum", "min", or "max"
    records = []

    for _, row in boundaries.iterrows():
        unit_name = (
            row.get("NAME_2")
            or row.get("NAME_1")
            or row.get("name")
            or row.get("GID_0", "unknown")
        )
        geom_gdf = gpd.GeoDataFrame([row], crs=boundaries.crs)

        ds_clip = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)
        ds_clip = ds_clip.rio.write_crs("EPSG:4326", inplace=True)
        try:
            ds_unit = ds_clip.rio.clip(geom_gdf.geometry, geom_gdf.crs, all_touched=True)
        except Exception:
            continue

        has_time = "time" in ds_unit.dims

        for var in ds_unit.data_vars:
            da = ds_unit[var]
            if has_time and temporal_freq:
                da_agg = getattr(da.resample(time=temporal_freq), agg_fn)()
                for t in da_agg.time.values:
                    val = float(getattr(da_agg.sel(time=t), agg_fn)(dim=["lat", "lon"]).values)
                    records.append({"admin_unit": unit_name, "variable": var, "time": str(t), "value": val})
            else:
                spatial_agg = getattr(da, agg_fn)(dim=["lat", "lon"])
                if has_time:
                    for t in spatial_agg.time.values:
                        val = float(spatial_agg.sel(time=t).values)
                        records.append({"admin_unit": unit_name, "variable": var, "time": str(t), "value": val})
                else:
                    val = float(spatial_agg.values)
                    records.append({"admin_unit": unit_name, "variable": var, "time": None, "value": val})

    df = pd.DataFrame(records)
    if output_csv:
        df.to_csv(output_csv, index=False)
    return df
```

**Key notes:**
- Static layers (no `time` dim) are handled in the `else` branch — they produce one row per admin unit per variable
- `temporal_freq` uses pandas offset aliases: `"ME"` = month-end, `"QE"` = quarter-end, `"YE"` = year-end
- The returned DataFrame always has columns: `admin_unit`, `variable`, `time`, `value`
- When `output_csv` is provided, the file is written automatically

---

## Common pitfalls to watch for

- **Missing CRS**: Always call `ds.rio.write_crs("EPSG:4326")` before clipping — datasets loaded from NetCDF often lack it
- **Dimension names**: The patterns assume `lat` and `lon`. If the user's data uses `latitude`/`longitude` or `x`/`y`, update `x_dim`/`y_dim` accordingly
- **GADM rate limits**: If fetching many admin boundaries in a loop, encourage caching the GeoDataFrame rather than re-fetching per unit
- **Memory**: For large Datasets, suggest `ds.chunk({"time": 12})` before clipping to enable Dask lazy evaluation
