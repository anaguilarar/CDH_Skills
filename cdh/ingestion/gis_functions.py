"""
cdh.ingestion.gis_functions
============================

Spatial utility functions: bbox helpers, numpy → xarray conversion,
raster clipping/masking, and basic reprojection.
"""

from __future__ import annotations

import os
from typing import List, Optional, Union

import geopandas as gpd
import math
import numpy as np
import rasterio
import xarray
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_bounds
from rasterio.warp import reproject
from shapely.geometry import mapping, Polygon
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# Bbox / polygon converters
# ---------------------------------------------------------------------------

def from_polygon_2bbox(pol: Polygon, factor: float | None = None) -> List[float]:
    """Return [xmin, ymin, xmax, ymax] from a Shapely polygon."""
    points = list(pol.exterior.coords)
    xs, ys = zip(*points)
    l, b, r, t = min(xs), min(ys), max(xs), max(ys)
    if factor:
        l = l - factor if l > 0 else l + factor
        b -= factor
        r = r + factor if r > 0 else r - factor
        t += factor
    return [l, b, r, t]


def from_xyxy_2polygon(x1: float, y1: float, x2: float, y2: float) -> Polygon:
    """Create a bounding-box polygon from two opposite corners."""
    return Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])


# ---------------------------------------------------------------------------
# Transform / coordinate helpers
# ---------------------------------------------------------------------------

def get_transform_fromxy(x: np.ndarray, y: np.ndarray) -> Affine:
    height = width = len(y)
    return from_bounds(np.sort(x)[0], np.sort(y)[0], np.sort(x)[-1], np.sort(y)[-1], width, height)


def coordinates_fromtransform(transform: Affine, imgsize: list) -> list:
    """Return (lon, lat) meshgrid arrays from an affine transform."""
    rows, cols = np.meshgrid(np.arange(imgsize[0]), np.arange(imgsize[1]))
    T1 = transform * Affine.translation(0, 0)
    rc2en = lambda r, c: T1 * (c, r)
    cols, rows = np.vectorize(rc2en, otypes=[np.float64, np.float64])(rows, cols)
    return [cols, rows]


def get_new_coords_for_newshape(
    oldx: np.ndarray, oldy: np.ndarray, newheight: int, newwidth: int
) -> tuple:
    sprx = abs(oldx[0] - oldx[1])
    spry = abs(oldy[0] - oldy[1])
    xmin = oldx[0] - sprx / 2 if oldx[0] < oldx[1] else oldx[-1] - sprx / 2
    ymin = oldy[0] - spry / 2 if oldy[0] < oldy[1] else oldy[-1] - spry / 2
    xmax = oldx[-1] + sprx / 2 if oldx[0] < oldx[1] else oldx[0] + sprx / 2
    ymax = oldy[-1] + spry / 2 if oldy[0] < oldy[1] else oldy[0] + spry / 2
    newx = np.linspace(xmin + (xmax - xmin) / newwidth / 2, xmax - (xmax - xmin) / newwidth / 2, newwidth)
    newy = np.linspace(ymin + (ymax - ymin) / newheight / 2, ymax - (ymax - ymin) / newheight / 2, newheight)
    newx = newx if oldx[0] < oldx[1] else newx[::-1]
    newy = newy if oldy[0] < oldy[1] else newy[::-1]
    new_transform = get_transform_fromxy(newx, newy)
    return [(newx, newy), new_transform]


# ---------------------------------------------------------------------------
# numpy → xarray
# ---------------------------------------------------------------------------

def numpy_to_xarray(
    stacked_arrays: list | np.ndarray,
    transform: Affine,
    crs: str,
    var_name: str | list = "precipitation",
) -> xarray.Dataset:
    """Convert numpy array(s) with a rasterio transform to an xarray Dataset."""
    import rioxarray  # noqa: F401

    data = np.stack(stacked_arrays, axis=0).squeeze()
    if data.ndim == 2:
        data = data[np.newaxis, ...]
    depth, height, width = data.shape
    xs = np.round((np.arange(width) + 0.5) * transform.a + transform.c, 8)
    ys = np.round((np.arange(height) + 0.5) * transform.e + transform.f, 8)

    metadata = {"transform": transform, "crs": crs, "width": width, "height": height}

    if isinstance(var_name, list):
        da = xarray.Dataset()
        for i, var in enumerate(var_name):
            da[var] = xarray.DataArray(data=data[i], dims=["y", "x"])
            da[var] = da[var].assign_coords({"y": ys, "x": xs})
        da = da.rio.write_crs(crs).rio.write_transform(transform)
        da.attrs = metadata
        return da
    else:
        da = xarray.DataArray(
            data=data,
            dims=["date", "y", "x"],
            coords={"date": np.arange(depth), "y": ys, "x": xs},
            name=var_name,
        )
        da = da.rio.write_crs(crs).rio.write_transform(transform)
        da.attrs = metadata
        return da.to_dataset()


def list_tif_2xarray(
    listraster: List[np.ndarray],
    transform: Affine,
    crs: str,
    nodata: int = 0,
    bands_names: List[str] | None = None,
    dimsformat: str = "CHW",
    dimsvalues: dict | None = None,
    depth_dim_name: str = "date",
    dtype=None,
    height: int | None = None,
    width: int | None = None,
) -> xarray.Dataset:
    """Convert a list of numpy arrays to an xarray Dataset."""
    import rioxarray  # noqa: F401

    assert len(listraster) > 0
    arr = listraster[0]
    if arr.ndim == 2:
        if dimsformat in ("CHW", "CWH"):
            height = height or arr.shape[0]
            width = width or arr.shape[1]
            dims = ["y", "x"]
    else:
        height = height or arr.shape[-2]
        width = width or arr.shape[-1]
        dims = [depth_dim_name, "y", "x"]

    dim_names = {f"dim_{i}": dims[i] for i in range(len(arr.shape))}
    metadata = {
        "transform": transform,
        "crs": crs,
        "width": width,
        "height": height,
        "count": len(listraster),
        "nodata": nodata,
    }
    if bands_names is None:
        bands_names = [f"band_{i}" for i in range(len(listraster))]

    riolist = [xarray.DataArray(img, name=n) for img, n in zip(listraster, bands_names)]
    multi = xarray.merge(riolist)
    multi.attrs = metadata
    multi = multi.rename(dim_names)

    if dimsvalues:
        multi = multi.assign_coords(dimsvalues)
    else:
        y_arr, x_arr = coordinates_fromtransform(transform, [height, width])
        multi = multi.assign_coords(x=np.sort(np.unique(x_arr)))
        ys = np.sort(np.unique(y_arr))[::-1] if transform[4] < 0 else np.unique(y_arr)
        multi = multi.assign_coords(y=ys)

    if dtype is not None:
        multi = multi.astype(dtype)

    if crs is not None:
        multi = multi.rio.write_crs(crs)
    if transform is not None:
        multi = multi.rio.write_transform(transform)
    return multi


# ---------------------------------------------------------------------------
# Clipping / masking
# ---------------------------------------------------------------------------

def clip_xarraydata(
    xarraydata: xarray.Dataset,
    polygon: Polygon | None = None,
    xyxy: List[float] | None = None,
    xdim_name: str = "x",
    ydim_name: str = "y",
) -> xarray.Dataset:
    """Clip an xarray dataset to a bounding box or polygon."""
    import rioxarray  # noqa: F401

    if xyxy is not None:
        crs = "EPSG:4326" if xarraydata.rio.crs is None else xarraydata.rio.crs
        x1, y1, x2, y2 = xyxy
        return xarraydata.rio.write_crs(crs).rio.clip_box(minx=x1, miny=y1, maxx=x2, maxy=y2)

    if polygon is not None:
        bbox = from_polygon_2bbox(polygon)
        x1, y1, x2, y2 = bbox
        crs = "EPSG:4326" if xarraydata.rio.crs is None else xarraydata.rio.crs
        return xarraydata.rio.write_crs(crs).rio.clip_box(minx=x1, miny=y1, maxx=x2, maxy=y2)

    return xarraydata


def mask_xarray_using_rio(
    xrdata: xarray.DataArray,
    geometry: gpd.GeoDataFrame,
    drop: bool = True,
    all_touched: bool = True,
    reproject_to_raster: bool = True,
) -> xarray.DataArray | None:
    """Mask an xarray object using rioxarray.rio.clip."""
    import rioxarray  # noqa: F401

    if reproject_to_raster:
        geometry = geometry.to_crs(xrdata.rio.crs)
    else:
        xrdata = xrdata.rio.reproject(geometry.crs)
    xrdata = xrdata.rio.write_crs(xrdata.rio.crs)
    try:
        return xrdata.rio.clip(
            geometry.geometry.apply(mapping), geometry.crs,
            drop=drop, all_touched=all_touched,
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("mask_xarray_using_rio failed: %s", exc)
        return None


def mask_xarray_using_gpdgeometry(
    xrdata: xarray.Dataset,
    geometry: gpd.GeoDataFrame,
    xdim_name: str = "x",
    ydim_name: str = "y",
    clip: bool = True,
    all_touched: bool = True,
) -> xarray.Dataset:
    """Mask using a rasterio geometry mask (faster for 3-D cubes)."""
    import rasterio.features
    import rioxarray  # noqa: F401

    try:
        src_transform = xrdata.rio.transform()
    except Exception:
        src_transform = xrdata.attrs["transform"]

    shape_mask = rasterio.features.geometry_mask(
        [mapping(g) for g in geometry.geometry],
        out_shape=(len(xrdata[ydim_name]), len(xrdata[xdim_name])),
        transform=src_transform,
        all_touched=all_touched,
        invert=True,
    )
    mask_da = xarray.DataArray(shape_mask, dims=(ydim_name, xdim_name))
    masked = xrdata.where(mask_da)
    if clip:
        masked = clip_xarraydata(masked, xyxy=geometry.total_bounds)
    return masked


def read_raster_data(
    path: str,
    crop_extent: List[float] | None = None,
    xdim_name: str = "x",
    ydim_name: str = "y",
) -> xarray.Dataset:
    """Open a NetCDF/GeoTIFF file and optionally clip to an extent."""
    assert os.path.exists(path), f"{path} does not exist"
    try:
        xr_data = xarray.open_dataset(path, engine="netcdf4")
    except Exception:
        xr_data = xarray.open_dataset(path, engine="rasterio")
    dims = list(xr_data.sizes.keys())
    rename: dict[str, str] = {}
    if "lon" in dims and xdim_name != "lon":
        rename["lon"] = xdim_name
        rename["lat"] = ydim_name
    elif "longitude" in dims and xdim_name != "longitude":
        rename["longitude"] = xdim_name
        rename["latitude"] = ydim_name
    elif "x" in dims and xdim_name != "x":
        rename["x"] = xdim_name
        rename["y"] = ydim_name
    if rename:
        xr_data = xr_data.rename(rename)
    if crop_extent is not None:
        xr_data = clip_xarraydata(xr_data, xyxy=crop_extent)
    return xr_data


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def re_scale_xarray(
    xrdata: xarray.Dataset,
    scale_factor: float,
    xdim_name: str = "x",
    ydim_name: str = "y",
    method: str = "nearest",
) -> xarray.Dataset:
    """Rescale an xarray dataset by a spatial scale factor."""
    import rioxarray  # noqa: F401

    oldx = xrdata[xdim_name].values
    oldy = xrdata[ydim_name].values
    if len(oldx) == 1:
        oldx = [oldx[0], oldx[0] + xrdata.rio.transform()[0]]
    if len(oldy) == 1:
        oldy = [oldy[0], oldy[0] + xrdata.rio.transform()[4]]

    height = len(np.unique(xrdata[ydim_name].values))
    width = len(np.unique(xrdata[xdim_name].values))
    (newx, newy), new_transform = get_new_coords_for_newshape(
        oldx, oldy, int(height * scale_factor), int(width * scale_factor)
    )
    dst = xrdata.interp({xdim_name: newx, ydim_name: newy}, method=method)
    dst = dst.rio.write_transform(new_transform)
    dst.attrs.update({"transform": new_transform, "height": len(newy), "width": len(newx)})
    return dst


def resample_xarray(
    xarraydata: xarray.Dataset,
    xrreference: xarray.Dataset,
    method: str = "linear",
    xrefdim_name: str = "x",
    yrefdim_name: str = "y",
    target_crs: str | None = None,
) -> xarray.Dataset:
    """Resample xarraydata to match the grid of xrreference."""
    import rioxarray  # noqa: F401

    dims = list(xarraydata.sizes.keys())
    if yrefdim_name in dims:
        xdim_name, ydim_name = "x", "y"
    elif "lat" in dims:
        xdim_name, ydim_name = "lon", "lat"
    else:
        xdim_name, ydim_name = "longitude", "latitude"

    if target_crs is not None and xarraydata.rio.crs is not None:
        if str(target_crs) != str(xarraydata.rio.crs):
            from rasterio.enums import Resampling as _R
            xarraydata = xarraydata.rio.reproject(target_crs, resampling=_R.bilinear)

    xrresampled = xarraydata.interp(
        {xdim_name: xrreference[xrefdim_name].values, ydim_name: xrreference[yrefdim_name].values},
        method=method,
    )
    xrresampled.attrs["transform"] = get_transform_fromxy(
        xrreference[xrefdim_name].values, xrreference[yrefdim_name].values
    )
    for key in xrresampled.data_vars:
        shape = xrresampled[key].shape
        if len(shape) >= 2:
            xrresampled.attrs["height"] = shape[-2]
            xrresampled.attrs["width"] = shape[-1]
            break
    return xrresampled


def get_boundaries_from_path(
    path: str, crs: str | None = None, round_numbers: bool = False
) -> tuple:
    """Read a shapefile and return its (xmin, ymin, xmax, ymax) bounds."""
    features = gpd.read_file(path)
    if crs:
        features = features.to_crs(crs)
    x1, y1, x2, y2 = features.total_bounds
    if round_numbers:
        x1, y1, x2, y2 = math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)
    return float(x1), float(y1), float(x2), float(y2)
