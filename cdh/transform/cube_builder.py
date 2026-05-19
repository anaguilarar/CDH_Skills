"""
cdh.transform.cube_builder
===========================

Build per-source xarray Datasets from raw downloaded climate files.

All cubes produced here share a consistent contract:
- Spatial dimensions: ``lat`` (y) and ``lon`` (x)
- Time dimension: ``time`` (datetime64)
- CRS: EPSG:4326
- Optional geometry mask applied before returning

Key classes
-----------
* :func:`normalize_dims`      — rename any x/y/longitude/latitude/date dim
                                to lon/lat/time.
* :class:`SourceCubeBuilder`  — generalized multi-variable stacker used for
                                CHIRPS, CHIRTS, and AgERA5 per-day NetCDF folders.
* :func:`build_nasa_power_cube` — thin wrapper for the single-file POWER NetCDF.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rioxarray  # noqa: F401
import tqdm
import xarray as xr

from cdh.ingestion.files_manager import IntervalFolderManager
from cdh.ingestion.utils import resample_variables, set_crs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dim normalization
# ---------------------------------------------------------------------------

_X_ALIASES = {"x", "longitude", "lon"}
_Y_ALIASES = {"y", "latitude", "lat"}
_T_ALIASES = {"date", "Date"}


def normalize_dims(ds: xr.Dataset, inplace: bool = False) -> xr.Dataset:
    """Rename spatial/time dimensions to the canonical ``lon`` / ``lat`` / ``time``.

    Parameters
    ----------
    ds : xr.Dataset
        Input dataset with any combination of x/y/lon/lat/longitude/latitude
        dimension names, and ``date`` or ``Date`` for the time axis.
    inplace : bool
        Not used (xarray rename always returns a new object).  Kept for API
        symmetry.

    Returns
    -------
    xr.Dataset
        Dataset with ``lon``, ``lat``, and optionally ``time`` dimensions.
    """
    rename: dict[str, str] = {}
    for d in ds.dims:
        if d in _X_ALIASES and d != "lon":
            rename[d] = "lon"
        elif d in _Y_ALIASES and d != "lat":
            rename[d] = "lat"
        elif d in _T_ALIASES:
            rename[d] = "time"
    return ds.rename(rename) if rename else ds


def ensure_epsg4326(ds: xr.Dataset) -> xr.Dataset:
    """Write EPSG:4326 CRS if not already set; reproject if a different CRS is found."""
    try:
        if ds.rio.crs is None:
            return ds.rio.write_crs("EPSG:4326")
        if str(ds.rio.crs).upper() != "EPSG:4326":
            logger.debug("Reprojecting from %s to EPSG:4326", ds.rio.crs)
            return ds.rio.reproject("EPSG:4326")
        return ds
    except Exception:  # noqa: BLE001
        return ds.rio.write_crs("EPSG:4326")


# ---------------------------------------------------------------------------
# Temporal stacking helper
# ---------------------------------------------------------------------------

def stack_temporally(
    xrdata_dict: dict[str, xr.Dataset],
    time_dim_name: str = "time",
    parse_dates: bool = True,
) -> xr.Dataset:
    """Concatenate a per-date dict of Datasets along a ``time`` dimension.

    Parameters
    ----------
    xrdata_dict : dict[str, xr.Dataset]
        ``{date_str: single_date_dataset}`` where date strings are ``YYYYMMDD``.
    time_dim_name : str
        Name of the new time coordinate.  Default: ``"time"``.
    parse_dates : bool
        Parse string keys as ``datetime`` objects.  Default: True.

    Returns
    -------
    xr.Dataset
        Multi-temporal Dataset with CRS metadata preserved.
    """
    datasets: list[xr.Dataset] = []
    time_coords: list[Any] = (
        [datetime.strptime(k, "%Y%m%d") for k in xrdata_dict]
        if parse_dates else list(xrdata_dict.keys())
    )
    for t_coord, ds in zip(time_coords, xrdata_dict.values()):
        ds_exp = ds.assign_coords({time_dim_name: t_coord}).expand_dims(time_dim_name)
        datasets.append(ds_exp)

    stacked = xr.concat(datasets, dim=time_dim_name, combine_attrs="override", join="override")
    first = datasets[0]
    try:
        if first.rio.crs is not None:
            stacked.rio.write_crs(first.rio.crs, inplace=True)
    except Exception:  # noqa: BLE001
        pass
    if "spatial_ref" in stacked.variables:
        for var in stacked.data_vars:
            stacked[var].attrs["grid_mapping"] = "spatial_ref"
    stacked.attrs.update(first.attrs)
    return stacked


# ---------------------------------------------------------------------------
# SourceCubeBuilder
# ---------------------------------------------------------------------------

class SourceCubeBuilder:
    """Build a multi-variable, multi-temporal xr.Dataset from per-day NetCDF folders.

    Designed to work with the folder layout produced by :class:`~cdh.ingestion.chirps.CHIRPSDownloader`,
    :class:`~cdh.ingestion.chirts.CHIRTSDownloader`, and
    :class:`~cdh.ingestion.agera5.AgEra5Downloader`:

    .. code-block:: text

        {source_folder}/
            {variable_key}/
                {year}/
                    {source}_{variable}_{YYYYMMDD}.nc

    Parameters
    ----------
    directory_paths : dict[str, str]
        ``{short_variable_name: folder_path}`` — one entry per variable.
        Example::

            {
                "precipitation": "data/raw/chirps_precipitation_raw",
                "tmax":          "data/raw/chirts/tmax",
                "tmin":          "data/raw/chirts/tmin",
            }
    extent : list[float] | None
        Optional pre-clip ``[xmin, ymin, xmax, ymax]`` applied when loading
        each raw file. Ignored when ``None``.

    Examples
    --------
    >>> from cdh.ingestion.files_manager import IntervalFolderManager
    >>> builder = SourceCubeBuilder(
    ...     directory_paths={
    ...         "precipitation": "data/raw/chirps_precipitation_raw",
    ...     },
    ... )
    >>> ds = builder.build("2018-01-01", "2018-12-31")
    >>> ds
    <xarray.Dataset>
    Dimensions:  (time: 365, lat: ..., lon: ...)
    ...
    """

    def __init__(
        self,
        directory_paths: dict[str, str],
        extent: list[float] | None = None,
    ) -> None:
        self.directory_paths = directory_paths
        self._extent = extent
        self._folder_manager = IntervalFolderManager()
        self._query_dates: dict[str, dict[str, str]] = {}

    @property
    def variables(self) -> list[str]:
        return list(self.directory_paths.keys())

    def build(
        self,
        starting_date: str,
        ending_date: str,
        reference_variable: str | None = None,
        ncores: int = 0,
    ) -> xr.Dataset:
        """Build and return the multi-temporal Dataset.

        Parameters
        ----------
        starting_date : str
            ISO 8601 start ``"YYYY-MM-DD"``.
        ending_date : str
            ISO 8601 end ``"YYYY-MM-DD"``.
        reference_variable : str | None
            Variable whose grid is the spatial reference for co-registration.
            Defaults to the first key in ``directory_paths``.
        ncores : int
            Parallel workers for per-date loading.  ``0`` → sequential.

        Returns
        -------
        xr.Dataset
            Dataset with ``time × lat × lon`` dimensions in EPSG:4326.
        """
        ref_var = reference_variable or self.variables[0]
        self._discover_dates(starting_date, ending_date)

        if ncores > 0:
            per_date = self._build_parallel(ref_var, ncores)
        else:
            per_date = self._build_sequential(ref_var)

        cube = stack_temporally(per_date, time_dim_name="time", parse_dates=True)
        cube = normalize_dims(cube)
        cube = ensure_epsg4326(cube)
        return cube

    def build_and_save(
        self,
        output_path: str,
        starting_date: str,
        ending_date: str,
        suffix: str = "",
        reference_variable: str | None = None,
        ncores: int = 0,
    ) -> str:
        """Build the cube and save it as a compressed NetCDF file.

        Returns
        -------
        str
            Path to the saved NetCDF file.
        """
        Path(output_path).mkdir(parents=True, exist_ok=True)
        cube = self.build(starting_date, ending_date, reference_variable, ncores)
        sy, ey = starting_date[:4], ending_date[:4]
        fname = f"cube_{suffix}_{sy}_{ey}.nc" if suffix else f"cube_{sy}_{ey}.nc"
        out_file = os.path.join(output_path, fname)
        encoding = {v: {"zlib": True} for v in cube.data_vars}
        cube.to_netcdf(out_file, encoding=encoding, engine="netcdf4")
        logger.info("Cube saved → %s", out_file)
        return out_file

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _discover_dates(self, starting_date: str, ending_date: str) -> None:
        all_dates: list[list[str]] = []
        all_files: list[list[str]] = []
        for var in self.variables:
            result = self._folder_manager(
                self.directory_paths[var], starting_date, ending_date
            )
            if not result:
                logger.warning("No files found for variable '%s'", var)
                dates_arr, files_arr = np.array([]), np.array([])
            else:
                dates_arr, files_arr = np.array(result).T
            all_dates.append(dates_arr.tolist())
            all_files.append(files_arr.tolist())

        # Intersect dates across all variables
        if all_dates and all(all_dates):
            common = sorted(set(all_dates[0]).intersection(*all_dates[1:]))
        else:
            common = sorted(all_dates[0]) if all_dates else []

        filtered = [
            [all_files[j][i] for i, d in enumerate(all_dates[j]) if d in common]
            for j in range(len(self.variables))
        ]
        self._query_dates = {
            d: {self.variables[j]: filtered[j][i] for j in range(len(self.variables))}
            for i, d in enumerate(common)
        }
        logger.info("Common dates found: %d", len(common))

    def _load_date(self, date: str, reference_variable: str) -> xr.Dataset:
        from cdh.ingestion.gis_functions import read_raster_data

        year = date[:4]
        file_map = self._query_dates[date]
        paths = {
            var: os.path.join(self.directory_paths[var], year, fname)
            for var, fname in file_map.items()
        }
        var_datasets = {
            var: read_raster_data(fp, crop_extent=self._extent)
            for var, fp in paths.items()
        }
        return resample_variables(var_datasets, reference_variable=reference_variable)

    def _build_sequential(self, reference_variable: str) -> dict[str, xr.Dataset]:
        per_date: dict[str, xr.Dataset] = {}
        for date in tqdm.tqdm(self._query_dates, desc="Building cube"):
            ds = self._load_date(date, reference_variable)
            ds = set_crs(ds, ds.attrs.get("crs") or ds.rio.crs)
            per_date[date] = ds
        return per_date

    def _build_parallel(self, reference_variable: str, ncores: int) -> dict[str, xr.Dataset]:
        per_date: dict[str, xr.Dataset] = {}
        dates = list(self._query_dates.keys())
        with tqdm.tqdm(total=len(dates), desc="Building cube (parallel)") as pbar:
            with concurrent.futures.ProcessPoolExecutor(max_workers=ncores) as pool:
                future_map = {
                    pool.submit(self._load_date, d, reference_variable): d
                    for d in dates
                }
                for future in concurrent.futures.as_completed(future_map):
                    date = future_map[future]
                    try:
                        per_date[date] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Date %s failed: %s", date, exc)
                    pbar.update(1)
        return per_date


# ---------------------------------------------------------------------------
# NASA POWER cube (single-file, no stacking needed)
# ---------------------------------------------------------------------------

def build_nasa_power_cube(nc_path: str, parameters: list[str] | None = None) -> xr.Dataset:
    """Load a NASA POWER NetCDF and apply the standard dim normalization.

    Parameters
    ----------
    nc_path : str
        Path to the NetCDF file downloaded by :class:`~cdh.ingestion.nasa_power.NASAPowerDownloader`.
    parameters : list[str] | None
        If provided, only these variables are kept.

    Returns
    -------
    xr.Dataset
        Dataset with ``time × lat × lon`` in EPSG:4326.
    """
    ds = xr.open_dataset(nc_path, engine="netcdf4", chunks="auto")
    if parameters:
        keep = [p for p in parameters if p in ds.data_vars]
        ds = ds[keep]
    ds = normalize_dims(ds)
    ds = ensure_epsg4326(ds)
    return ds
