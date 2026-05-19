"""
cdh.spatial.raster_ops
=======================

Pure spatial operations: bbox clipping, geometry masking, reprojection, and
ROI extraction. All functions work with lazy xr.Datasets (Dask-backed or not)
and accept ``lat``/``lon`` dimension names as the canonical form, with
fallbacks to ``y``/``x`` and ``latitude``/``longitude``.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Any

import geopandas as gpd
import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRS helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _resolve_crs(crs_string: str) -> Any:
    """Parse and cache a CRS string."""
    from pyproj import CRS
    return CRS.from_user_input(crs_string)


def _get_dataset_crs(ds: xr.Dataset) -> str | None:
    """Extract the CRS string from a dataset via rioxarray."""
    try:
        import rioxarray  # noqa: F401
        crs = ds.rio.crs
        return str(crs) if crs is not None else None
    except Exception:  # noqa: BLE001
        return None


def _spatial_dims(ds: xr.Dataset) -> tuple[str, str]:
    """Return (x_dim, y_dim) from a Dataset, checking common naming conventions."""
    dims = list(ds.dims)
    x = next((d for d in dims if d in {"lon", "x", "longitude"}), None)
    y = next((d for d in dims if d in {"lat", "y", "latitude"}), None)
    if x is None or y is None:
        try:
            import rioxarray  # noqa: F401
            x = ds.rio.x_dim
            y = ds.rio.y_dim
        except Exception:  # noqa: BLE001
            pass
    if x is None:
        x = dims[-1]
    if y is None:
        y = dims[-2]
    return x, y


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def set_encoding(xrdata: xr.Dataset, compress_method: str = "zlib") -> dict[str, dict]:
    """Build a NetCDF encoding dict with zlib compression."""
    encoding: dict[str, dict] = {}
    for var in xrdata.data_vars:
        encoding[var] = {compress_method: True}
        if "grid_mapping" in xrdata[var].attrs:
            encoding[var]["grid_mapping"] = xrdata[var].attrs["grid_mapping"]
            del xrdata[var].attrs["grid_mapping"]
    if "spatial_ref" in xrdata.variables:
        encoding["spatial_ref"] = {}
    return encoding


def check_crs_in_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Ensure ``spatial_ref`` + ``grid_mapping`` are consistent after concat/merge."""
    try:
        import rioxarray  # noqa: F401
        if ds.rio.crs is not None:
            ds = ds.rio.write_crs(ds.rio.crs, inplace=True)
            for var in ds.data_vars:
                if var != "spatial_ref":
                    ds[var].attrs["grid_mapping"] = "spatial_ref"
    except Exception as exc:  # noqa: BLE001
        logger.warning("check_crs_in_dataset: could not fix CRS — %s", exc)
    return ds


# ---------------------------------------------------------------------------
# Clipping / masking
# ---------------------------------------------------------------------------

def clip_to_bbox(
    ds: xr.Dataset,
    xyxy: tuple[float, float, float, float],
    crs: str = "EPSG:4326",
) -> xr.Dataset:
    """Clip a Dataset to a bounding box using rioxarray (lazy operation).

    Parameters
    ----------
    ds : xr.Dataset
    xyxy : tuple
        ``(xmin, ymin, xmax, ymax)`` in the dataset's CRS.
    crs : str
        CRS of the bounding box coordinates.  Default: EPSG:4326.
    """
    import rioxarray  # noqa: F401

    x1, y1, x2, y2 = xyxy
    src_crs = _get_dataset_crs(ds)
    if src_crs is None:
        ds = ds.rio.write_crs(crs)
    return ds.rio.clip_box(minx=x1, miny=y1, maxx=x2, maxy=y2)


def reproject_dataset(
    ds: xr.Dataset,
    target_crs: str,
    resampling: str = "nearest",
) -> xr.Dataset:
    """Reproject a Dataset to a new CRS via rioxarray."""
    import rioxarray  # noqa: F401
    from rasterio.enums import Resampling as _R

    method = _R.nearest if resampling == "nearest" else _R.bilinear
    return ds.rio.reproject(target_crs, resampling=method)


def mask_with_geometry(
    ds: xr.Dataset,
    geometry: gpd.GeoDataFrame,
    clip: bool = True,
    all_touched: bool = True,
    use_rio: bool = False,
) -> xr.Dataset:
    """Mask a Dataset using a GeoDataFrame geometry.

    Parameters
    ----------
    ds : xr.Dataset
    geometry : gpd.GeoDataFrame
        Masking geometry (any CRS; reprojected to match the dataset).
    clip : bool
        Clip bbox after masking.  Default: True.
    all_touched : bool
        Include boundary-touching pixels.  Default: True.
    use_rio : bool
        Use ``rio.clip`` (accurate, slower) instead of the rasterio
        geometry_mask approach (faster for 3-D cubes).  Default: False.
    """
    import rasterio.features
    import rioxarray  # noqa: F401
    from shapely.geometry import mapping

    src_crs = _get_dataset_crs(ds)

    if use_rio:
        geom = geometry.to_crs(src_crs) if src_crs else geometry
        ds = ds.rio.write_crs(src_crs or "EPSG:4326")
        return ds.rio.clip(
            geom.geometry.apply(mapping), geom.crs,
            drop=clip, all_touched=all_touched,
        )

    # Geometry-mask path (preferred for temporal cubes)
    try:
        src_transform = ds.rio.transform()
    except Exception:  # noqa: BLE001
        src_transform = ds.attrs.get("transform")

    x_dim, y_dim = _spatial_dims(ds)

    if isinstance(geometry, gpd.GeoDataFrame):
        geom_shapes = [mapping(g) for g in geometry.geometry]
    else:
        geom_shapes = [mapping(g) if hasattr(g, "__geo_interface__") else g for g in geometry]

    shape_mask = rasterio.features.geometry_mask(
        geom_shapes,
        out_shape=(len(ds[y_dim]), len(ds[x_dim])),
        transform=src_transform,
        all_touched=all_touched,
        invert=True,
    )
    mask_da = xr.DataArray(shape_mask, dims=(y_dim, x_dim))
    masked = ds.where(mask_da)

    if clip:
        x1, y1, x2, y2 = geometry.total_bounds
        masked = clip_to_bbox(masked, (x1, y1, x2, y2))

    return masked


# ---------------------------------------------------------------------------
# High-level ROI extractor
# ---------------------------------------------------------------------------

def get_roi_data(
    ds: xr.Dataset,
    feature_geometry: gpd.GeoDataFrame,
    xyxy: tuple[float, float, float, float] | None = None,
    clip: bool = True,
    all_touched: bool = True,
    use_rio: bool = False,
    target_crs: str | None = None,
) -> xr.Dataset:
    """Extract a Region-Of-Interest from a lazy Dataset.

    Workflow: optional bbox clip → optional reprojection → geometry mask.

    Parameters
    ----------
    ds : xr.Dataset
    feature_geometry : gpd.GeoDataFrame
        ROI geometry (one or more polygons).
    xyxy : tuple | None
        Pre-clip bounding box.  Derived from ``feature_geometry`` when None.
    clip : bool
        Clip after masking.  Default: True.
    all_touched : bool
        Boundary pixel inclusion.  Default: True.
    use_rio : bool
        Use ``rio.clip`` backend.  Default: False.
    target_crs : str | None
        Reproject to this CRS before masking.  None → no reprojection.

    Returns
    -------
    xr.Dataset
        Masked, clipped, lazy Dataset for the ROI.
    """
    import rioxarray  # noqa: F401

    if xyxy is None:
        x1, y1, x2, y2 = feature_geometry.total_bounds
    else:
        x1, y1, x2, y2 = xyxy

    src_crs = _get_dataset_crs(ds)
    roi = clip_to_bbox(ds, (x1, y1, x2, y2), crs=src_crs or "EPSG:4326")

    if target_crs is not None and src_crs is not None:
        if _resolve_crs(src_crs) != _resolve_crs(target_crs):
            roi = reproject_dataset(roi, target_crs)

    return mask_with_geometry(
        roi, feature_geometry, clip=clip, all_touched=all_touched, use_rio=use_rio
    )


# ---------------------------------------------------------------------------
# Rescaling and shapefile helpers
# ---------------------------------------------------------------------------

def rescale_dataset(
    ds: xr.Dataset,
    scale_factor: int,
    method: str = "nearest",
    x_dim: str = "lon",
    y_dim: str = "lat",
) -> xr.Dataset:
    """Spatially rescale a Dataset by a scale factor using xarray interpolation."""
    if scale_factor == 1:
        return ds
    old_x = ds[x_dim].values
    old_y = ds[y_dim].values
    new_x = np.linspace(old_x.min(), old_x.max(), int(len(old_x) * scale_factor))
    new_y = np.linspace(old_y.min(), old_y.max(), int(len(old_y) * scale_factor))
    return ds.interp({x_dim: new_x, y_dim: new_y}, method=method)


def get_boundaries_from_shapefile(
    path: str, crs: str | None = None, round_numbers: bool = False
) -> tuple[float, float, float, float]:
    """Read a shapefile and return its (xmin, ymin, xmax, ymax) bounds."""
    features = gpd.read_file(path)
    if crs:
        features = features.to_crs(crs)
    x1, y1, x2, y2 = features.total_bounds
    if round_numbers:
        x1, y1 = math.floor(x1), math.floor(y1)
        x2, y2 = math.ceil(x2), math.ceil(y2)
    return float(x1), float(y1), float(x2), float(y2)
