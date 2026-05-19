"""
cdh.ingestion.utils
====================

Shared xarray/rasterio utilities: co-registration, CRS helpers, compression.
"""

from __future__ import annotations

import copy
import glob
import logging
import os
import zipfile
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import rioxarray  # noqa: F401
import xarray
from rasterio.enums import Resampling

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRS helpers
# ---------------------------------------------------------------------------

def set_crs(xrdata: xarray.Dataset, crs: object) -> xarray.Dataset:
    """Write CRS string into dataset attrs."""
    if hasattr(crs, "to_epsg") and crs.to_epsg() is not None:
        xrdata.attrs["crs"] = f"EPSG:{crs.to_epsg()}"
    elif hasattr(crs, "to_string"):
        xrdata.attrs["crs"] = crs.to_string()
    return xrdata


# ---------------------------------------------------------------------------
# Temporal dimension helpers
# ---------------------------------------------------------------------------

def check_depth_name_dims(xrdata: xarray.Dataset) -> xarray.Dataset:
    """Select first slice along a time/band dimension, returning a 2-D dataset."""
    for dim in ("date", "time", "band"):
        if dim in xrdata.sizes:
            xrdata = xrdata.isel({dim: 0})
            xrdata = xrdata.drop_vars([dim], errors="ignore")
            return xrdata
    keys = list(xrdata.sizes.keys())
    if keys:
        xrdata = xrdata.isel({keys[0]: 0})
        xrdata = xrdata.drop_vars([keys[0]], errors="ignore")
    return xrdata


# ---------------------------------------------------------------------------
# Co-registration
# ---------------------------------------------------------------------------

def resample_variables(
    dict_xr: dict[str, xarray.Dataset],
    reference_variable: str | None = None,
    only_use_first_date: bool = True,
    verbose: bool = False,
    method: str = "linear",
    target_crs: str | None = None,
) -> xarray.Dataset:
    """Co-register all variables to a single spatial grid.

    Parameters
    ----------
    dict_xr : dict[str, xarray.Dataset]
        ``{variable_name: dataset}``
    reference_variable : str | None
        Variable whose grid is used as the spatial reference. Defaults to
        the first key.
    only_use_first_date : bool
        Collapse time dimension before reprojecting. Default: True.
    method : str
        Interpolation / resampling method. Default: 'linear' (bilinear).
    target_crs : str | None
        Reproject all datasets to this CRS before merging. None → keep as-is.

    Returns
    -------
    xarray.Dataset
        All variables merged onto the reference grid.
    """
    resampling_methods = {
        "nearest": Resampling.nearest,
        "linear": Resampling.bilinear,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
        "average": Resampling.average,
    }
    resampling_method = resampling_methods.get(method.lower(), Resampling.nearest)

    listvariables = list(dict_xr.keys())
    if not listvariables:
        raise ValueError("resample_variables: no variables loaded.")
    if reference_variable is None:
        reference_variable = listvariables[0]
    if reference_variable not in listvariables:
        raise ValueError(
            f"resample_variables: reference '{reference_variable}' not in {listvariables}"
        )
    listvariables.remove(reference_variable)

    xr_ref = dict_xr[reference_variable].copy()
    if len(xr_ref.sizes) >= 3 and only_use_first_date:
        xr_ref = check_depth_name_dims(xr_ref)

    if isinstance(xr_ref, xarray.Dataset):
        variable_name = next(
            (v for v in xr_ref.data_vars if v != "spatial_ref"), list(xr_ref.data_vars)[0]
        )
        xr_ref = xr_ref[variable_name]
    xr_ref.name = variable_name

    if target_crs is not None and str(target_crs) != str(xr_ref.rio.crs):
        xr_ref = xr_ref.rio.reproject(target_crs, resampling=resampling_method)

    xr_ref.rio.write_crs(xr_ref.rio.crs, inplace=True)
    ref_x_dim = xr_ref.rio.x_dim
    ref_y_dim = xr_ref.rio.y_dim
    if xr_ref.rio.nodata is None:
        xr_ref.rio.write_nodata(np.nan, encoded=True, inplace=True)

    processed: dict[str, xarray.DataArray] = {variable_name: xr_ref}

    for var_name, xr_data in dict_xr.items():
        if var_name == variable_name:
            continue
        if isinstance(xr_data, xarray.Dataset):
            vn = next((v for v in xr_data.data_vars if v != "spatial_ref"), list(xr_data.data_vars)[0])
            xr_data = xr_data[vn]
        if len(xr_data.sizes) >= 3 and only_use_first_date:
            xr_data = check_depth_name_dims(xr_data)

        is_native_soilgrids = abs(xr_data.rio.bounds()[0]) > 1000
        if is_native_soilgrids:
            xr_data.rio.write_crs("+proj=igh +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs", inplace=True)
        else:
            xr_data.rio.write_crs(xr_ref.rio.crs, inplace=True)

        rename_dict = {}
        if xr_data.rio.x_dim != ref_x_dim:
            rename_dict[xr_data.rio.x_dim] = ref_x_dim
        if xr_data.rio.y_dim != ref_y_dim:
            rename_dict[xr_data.rio.y_dim] = ref_y_dim
        if rename_dict:
            xr_data = xr_data.rename(rename_dict)
        if xr_data.rio.nodata is None:
            xr_data.rio.write_nodata(np.nan, encoded=True, inplace=True)

        resampled = xr_data.rio.reproject_match(xr_ref, resampling=resampling_method)
        resampled.name = var_name
        processed[var_name] = resampled

    dataoutput = xarray.merge(list(processed.values()), compat="override")

    # Clean up conflicting grid-mapping metadata from different sources
    gm_vars = [n for n, v in dataoutput.variables.items() if "grid_mapping_name" in v.attrs]
    if gm_vars:
        dataoutput = dataoutput.drop_vars(gm_vars, errors="ignore")
    for name in list(dataoutput.variables):
        dataoutput.variables[name].attrs.pop("grid_mapping", None)
        dataoutput.variables[name].encoding.pop("grid_mapping", None)

    dataoutput.rio.write_crs(xr_ref.rio.crs, inplace=True)
    return dataoutput


# ---------------------------------------------------------------------------
# Encoding / compression
# ---------------------------------------------------------------------------

def set_encoding(xrdata: xarray.Dataset, compress_method: str = "zlib") -> dict:
    """Build a NetCDF encoding dict with zlib compression."""
    return {k: {compress_method: True} for k in xrdata.data_vars}


def compress_file(input_filepath: str, output_zip_filepath: str) -> None:
    with zipfile.ZipFile(output_zip_filepath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(input_filepath, os.path.basename(input_filepath))


# ---------------------------------------------------------------------------
# Generic HTTP download
# ---------------------------------------------------------------------------

def download_file(
    start_date: str,
    end_date: str,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    url: str,
    variable: str,
    download_path: str,
) -> str:
    """POST a spatial/temporal request to a data service and save the response."""
    headers = {"Accept": "application/json"}
    url_params = {
        "startDt": start_date,
        "endDt": end_date,
        "xmin": xmin,
        "xmax": xmax,
        "ymin": ymin,
        "ymax": ymax,
        "variableName": variable,
    }
    response = requests.post(url, headers=headers, json=url_params, stream=True, timeout=120)
    response.raise_for_status()
    file_name = f"{variable}_{start_date}_to_{end_date}.nc"
    file_path = os.path.join(download_path, file_name)
    with open(file_path, "wb") as fh:
        for chunk in response.iter_content(chunk_size=8192):
            fh.write(chunk)
    return file_path
