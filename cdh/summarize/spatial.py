"""
cdh.summarize.spatial
======================

Zonal statistics — aggregate a climate Dataset by administrative unit.

``aggregate_by_admin`` fetches admin boundaries from GeoBoundaries, applies
a boolean mask for each unit, and computes per-unit statistics across the
spatial extent.  The result is a long-format ``pd.DataFrame`` suitable for
time-series analysis and visualisation.
"""

from __future__ import annotations

import logging
from typing import Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)


def aggregate_by_admin(
    ds: xr.Dataset,
    country_code: str,
    adm_level: int = 1,
    stat: Literal["mean", "sum", "max", "min", "std", "median"] = "mean",
    lat_dim: str = "lat",
    lon_dim: str = "lon",
    time_dim: str = "time",
    name_column: str | None = None,
) -> pd.DataFrame:
    """Compute zonal statistics for each administrative unit.

    For each admin unit at ``adm_level``, the function:

    1. Creates a raster mask from the unit's polygon.
    2. Applies the mask to every variable in ``ds``.
    3. Aggregates over the masked pixels using ``stat``.
    4. Returns a long-format DataFrame with columns:
       ``[admin_name, time (if present), variable, value]``.

    Parameters
    ----------
    ds : xr.Dataset
        Input Dataset.  Spatial dims should be ``lat`` / ``lon``.
    country_code : str
        ISO 3166-1 alpha-3 code (e.g. ``'HND'``).
    adm_level : int
        Administrative level to aggregate to.  0 = full country, 1 = region,
        2 = district/department.  Default: 1.
    stat : str
        Aggregation statistic.  Default: ``'mean'``.
    lat_dim : str
        Latitude dimension name.  Default: ``'lat'``.
    lon_dim : str
        Longitude dimension name.  Default: ``'lon'``.
    time_dim : str
        Time dimension name.  Default: ``'time'``.
    name_column : str | None
        Column in the admin GeoDataFrame that holds unit names.
        Auto-detected when None.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame:
        ``admin_name``, ``time`` (if ``time_dim`` in ds.dims),
        one column per variable.
    """
    import rasterio.features
    from shapely.geometry import mapping

    from cdh.ingestion.boundaries import (
        _detect_name_column,
        _fetch_geojson_cached,
        get_country_boundary,
    )

    # Fetch boundaries
    if adm_level == 0:
        gdf = get_country_boundary(country_code)
        col = country_code.upper()
        gdf["_name"] = country_code.upper()
    else:
        gdf = _fetch_geojson_cached(country_code, adm_level)
        col = name_column or _detect_name_column(gdf)
        gdf["_name"] = gdf[col]

    if gdf.crs is None or str(gdf.crs).upper() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")

    lats = ds[lat_dim].values
    lons = ds[lon_dim].values
    has_time = time_dim in ds.dims

    # Derive raster transform from coordinate arrays
    from rasterio.transform import from_bounds
    dlat = abs(float(lats[1] - lats[0])) if len(lats) > 1 else 0.05
    dlon = abs(float(lons[1] - lons[0])) if len(lons) > 1 else 0.05
    xmin = float(lons.min()) - dlon / 2
    xmax = float(lons.max()) + dlon / 2
    ymin = float(lats.min()) - dlat / 2
    ymax = float(lats.max()) + dlat / 2
    transform = from_bounds(xmin, ymin, xmax, ymax, len(lons), len(lats))

    stat_fn = {
        "mean": np.nanmean,
        "sum": np.nansum,
        "max": np.nanmax,
        "min": np.nanmin,
        "std": np.nanstd,
        "median": np.nanmedian,
    }.get(stat, np.nanmean)

    records: list[dict] = []

    for _, row in gdf.iterrows():
        unit_name = row["_name"]
        geom_shapes = [mapping(row.geometry)]

        mask = rasterio.features.geometry_mask(
            geom_shapes,
            out_shape=(len(lats), len(lons)),
            transform=transform,
            all_touched=True,
            invert=True,
        )

        if not mask.any():
            logger.debug("Unit '%s': empty mask, skipping.", unit_name)
            continue

        if has_time:
            time_vals = ds[time_dim].values
            for t_idx, t_val in enumerate(time_vals):
                rec: dict = {"admin_name": unit_name, time_dim: pd.Timestamp(t_val)}
                for var in ds.data_vars:
                    arr = ds[var].isel({time_dim: t_idx}).values
                    arr = arr[::-1] if lats[0] > lats[-1] else arr  # flip if needed
                    masked_vals = arr[mask]
                    rec[var] = float(stat_fn(masked_vals)) if masked_vals.size > 0 else np.nan
                records.append(rec)
        else:
            rec = {"admin_name": unit_name}
            for var in ds.data_vars:
                arr = ds[var].values
                if arr.ndim == 2:
                    arr = arr[::-1] if lats[0] > lats[-1] else arr
                    masked_vals = arr[mask]
                    rec[var] = float(stat_fn(masked_vals)) if masked_vals.size > 0 else np.nan
                else:
                    rec[var] = np.nan
            records.append(rec)

    df = pd.DataFrame(records)
    if has_time and time_dim in df.columns:
        df = df.sort_values(["admin_name", time_dim]).reset_index(drop=True)
    logger.info(
        "aggregate_by_admin: %s ADM%d  %d units  stat=%s  rows=%d",
        country_code, adm_level, len(gdf), stat, len(df),
    )
    return df


def aggregate_country(
    ds: xr.Dataset,
    country_code: str,
    stat: str = "mean",
    lat_dim: str = "lat",
    lon_dim: str = "lon",
    time_dim: str = "time",
) -> pd.DataFrame:
    """Convenience wrapper — aggregate the whole country as a single unit (ADM0).

    Returns a DataFrame with one row per time step and one column per variable.
    """
    return aggregate_by_admin(
        ds, country_code, adm_level=0, stat=stat,
        lat_dim=lat_dim, lon_dim=lon_dim, time_dim=time_dim,
    )
