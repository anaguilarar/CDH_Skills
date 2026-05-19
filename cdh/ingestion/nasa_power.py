"""
cdh.ingestion.nasa_power
=========================

NASA POWER daily data downloader.

Two backends are available:

* **S3 Zarr** (default, ``NASAPowerS3Downloader``) — reads directly from the
  public NASA POWER Zarr store on Amazon S3.  No rate limits; fast spatial/
  temporal slicing; no API key required.  Requires ``s3fs`` and ``zarr``.

* **REST API** (``NASAPowerDownloader``) — uses the NASA POWER LARC regional
  API (10° × 10° tile limit, slower, subject to rate limits).

Data source:
    https://power.larc.nasa.gov/

Rule: this module ONLY downloads files to disk.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import requests
import xarray as xr

logger = logging.getLogger(__name__)

_POWER_REGIONAL_URL = "https://power.larc.nasa.gov/api/temporal/daily/regional"
_S3_ZARR_PATH = "nasa-power/merra2/temporal/power_merra2_daily_temporal_lst.zarr"
_MAX_DEGREE = 10.0  # REST API tile limit
_TIMEOUT = 300  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tile_bbox(
    xmin: float, ymin: float, xmax: float, ymax: float, max_size: float = _MAX_DEGREE
) -> list[tuple[float, float, float, float]]:
    """Split a large bounding box into tiles of at most max_size°."""
    tiles = []
    x = xmin
    while x < xmax:
        y = ymin
        while y < ymax:
            tiles.append((
                round(x, 6),
                round(y, 6),
                round(min(x + max_size, xmax), 6),
                round(min(y + max_size, ymax), 6),
            ))
            y += max_size
        x += max_size
    return tiles


def _download_tile(
    xmin: float, ymin: float, xmax: float, ymax: float,
    start: str, end: str,
    parameters: list[str],
    community: str,
) -> xr.Dataset | None:
    """Download one tile from the NASA POWER regional API, return xr.Dataset."""
    params = {
        "parameters": ",".join(parameters),
        "community": community,
        "longitude-min": xmin,
        "longitude-max": xmax,
        "latitude-min": ymin,
        "latitude-max": ymax,
        "start": start,
        "end": end,
        "format": "netcdf",
        "user": "cdhuser",
        "header": "true",
        "time-standard": "UTC",
    }
    logger.debug(
        "NASA POWER tile request: bbox=[%.2f,%.2f,%.2f,%.2f] %s→%s vars=%s",
        xmin, ymin, xmax, ymax, start, end, parameters,
    )
    resp = requests.get(_POWER_REGIONAL_URL, params=params, timeout=_TIMEOUT, stream=True)
    if resp.status_code == 404:
        logger.warning("NASA POWER: 404 for tile [%.2f,%.2f,%.2f,%.2f]", xmin, ymin, xmax, ymax)
        return None
    resp.raise_for_status()

    # Write NetCDF bytes to a temp file then open lazily
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        for chunk in resp.iter_content(chunk_size=65536):
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        ds = xr.open_dataset(tmp_path, engine="netcdf4").load()
        return ds
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _yyyymmdd(date_str: str) -> str:
    """Convert 'YYYY-MM-DD' → 'YYYYMMDD' for the POWER API."""
    return date_str.replace("-", "")


def _normalize_power_dataset(ds: xr.Dataset, parameters: list[str]) -> xr.Dataset:
    """Rename POWER dims to lat/lon/time and drop fill-value sentinels."""
    rename: dict[str, str] = {}
    for d in ds.dims:
        if d.lower() in ("lon", "longitude"):
            rename[d] = "lon"
        elif d.lower() in ("lat", "latitude"):
            rename[d] = "lat"
        elif d.lower() in ("time", "date"):
            rename[d] = "time"
    if rename:
        ds = ds.rename(rename)

    # Replace POWER fill value (-999) with NaN
    for var in ds.data_vars:
        if var in parameters:
            ds[var] = ds[var].where(ds[var] > -990)

    return ds


# ---------------------------------------------------------------------------
# Public downloader class
# ---------------------------------------------------------------------------

class NASAPowerDownloader:
    """Download daily regional climate data from the NASA POWER API.

    Parameters
    ----------
    parameters : list[str]
        NASA POWER parameter codes (community AG).  Common examples:
        ``ALLSKY_SFC_SW_DWN``, ``T2M``, ``T2M_MAX``, ``T2M_MIN``,
        ``RH2M``, ``WS2M``, ``WS10M``, ``PRECTOTCORR``.
    community : str
        NASA POWER community.  ``"AG"`` (agriculture, default) or ``"RE"``
        (renewable energy) or ``"SB"`` (sustainability).

    Examples
    --------
    >>> dl = NASAPowerDownloader(parameters=["T2M_MAX", "T2M_MIN", "RH2M"])
    >>> path = dl.download(
    ...     extent=[-90.5, 13.0, -88.5, 15.5],
    ...     starting_date="2015-01-01",
    ...     ending_date="2015-12-31",
    ...     output_folder="data/raw/nasa_power",
    ... )
    """

    def __init__(
        self,
        parameters: list[str] | None = None,
        community: str = "AG",
    ) -> None:
        self.parameters = parameters or ["T2M_MAX", "T2M_MIN", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN"]
        self.community = community

    def download(
        self,
        extent: list[float],
        starting_date: str,
        ending_date: str,
        output_folder: str,
        force: bool = False,
    ) -> str:
        """Download NASA POWER data for the given extent and date range.

        Large extents are tiled automatically (max 10° × 10° per request).

        Parameters
        ----------
        extent : list[float]
            ``[xmin, ymin, xmax, ymax]`` in EPSG:4326.
        starting_date : str
            ISO 8601 start ``"YYYY-MM-DD"``.
        ending_date : str
            ISO 8601 end ``"YYYY-MM-DD"``.
        output_folder : str
            Folder where the output NetCDF file will be saved.
        force : bool
            Re-download even if the output file already exists.  Default: False.

        Returns
        -------
        str
            Path to the saved NetCDF file:
            ``{output_folder}/nasa_power_{starting_date}_{ending_date}.nc``
        """
        Path(output_folder).mkdir(parents=True, exist_ok=True)
        fname = f"nasa_power_{starting_date}_{ending_date}.nc"
        out_nc = os.path.join(output_folder, fname)

        if os.path.exists(out_nc) and not force:
            logger.info("NASA POWER: using cached file %s", out_nc)
            return out_nc

        xmin, ymin, xmax, ymax = extent
        start = _yyyymmdd(starting_date)
        end = _yyyymmdd(ending_date)

        tiles = _tile_bbox(xmin, ymin, xmax, ymax)
        logger.info(
            "NASA POWER: %d tile(s) for bbox=[%.2f,%.2f,%.2f,%.2f]  params=%s",
            len(tiles), xmin, ymin, xmax, ymax, self.parameters,
        )

        tile_datasets: list[xr.Dataset] = []
        for i, (tx1, ty1, tx2, ty2) in enumerate(tiles):
            logger.info(
                "NASA POWER: downloading tile %d/%d [%.2f,%.2f,%.2f,%.2f]",
                i + 1, len(tiles), tx1, ty1, tx2, ty2,
            )
            ds_tile = _download_tile(
                tx1, ty1, tx2, ty2, start, end, self.parameters, self.community
            )
            if ds_tile is not None:
                ds_tile = _normalize_power_dataset(ds_tile, self.parameters)
                tile_datasets.append(ds_tile)
            else:
                logger.warning("NASA POWER: tile %d returned no data, skipping.", i + 1)

        if not tile_datasets:
            raise RuntimeError(
                "NASA POWER: no data downloaded for any tile. "
                "Check that the extent, date range, and parameter names are valid."
            )

        if len(tile_datasets) == 1:
            merged = tile_datasets[0]
        else:
            # Merge spatial tiles; overlapping edges are averaged
            merged = xr.combine_by_coords(tile_datasets, combine_attrs="override")

        # Write with zlib compression
        encoding = {
            v: {"zlib": True, "complevel": 4}
            for v in merged.data_vars
            if v in self.parameters
        }
        merged.to_netcdf(out_nc, encoding=encoding, engine="netcdf4")
        logger.info("NASA POWER saved → %s", out_nc)
        return out_nc


# ---------------------------------------------------------------------------
# S3 Zarr backend (default)
# ---------------------------------------------------------------------------

class NASAPowerS3Downloader:
    """Download NASA POWER data directly from the public S3 Zarr store.

    Faster than the REST API: no tiling, no rate limits, direct spatial/
    temporal slicing over the full 1981–2029 daily global archive.

    Requires ``s3fs`` and ``zarr`` (``pip install s3fs zarr``).

    Parameters
    ----------
    parameters : list[str] | None
        NASA POWER variable codes.  Must exist in the S3 Zarr store (74
        variables available).  Default: T2M_MAX, T2M_MIN, RH2M, WS2M,
        ALLSKY_SFC_SW_DWN.
    """

    _DEFAULT_PARAMS = ["T2M_MAX", "T2M_MIN", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN"]

    def __init__(self, parameters: list[str] | None = None) -> None:
        self.parameters = parameters or self._DEFAULT_PARAMS

    def download(
        self,
        extent: list[float],
        starting_date: str,
        ending_date: str,
        output_folder: str,
        force: bool = False,
    ) -> str:
        """Slice the S3 Zarr store to the given extent/dates and save to NetCDF.

        Parameters
        ----------
        extent : list[float]
            ``[xmin, ymin, xmax, ymax]`` in EPSG:4326.
        starting_date : str
            ISO 8601 start ``"YYYY-MM-DD"``.
        ending_date : str
            ISO 8601 end ``"YYYY-MM-DD"``.
        output_folder : str
            Folder where the output NetCDF will be saved.
        force : bool
            Re-download even if a cached file exists.  Default: False.

        Returns
        -------
        str
            Path to the saved NetCDF:
            ``{output_folder}/nasa_power_{starting_date}_{ending_date}.nc``
        """
        try:
            import s3fs
        except ImportError as e:
            raise ImportError(
                "s3fs is required for the S3 backend: pip install s3fs"
            ) from e

        Path(output_folder).mkdir(parents=True, exist_ok=True)
        fname = f"nasa_power_{starting_date}_{ending_date}.nc"
        out_nc = os.path.join(output_folder, fname)

        if os.path.exists(out_nc) and not force:
            logger.info("NASA POWER S3: using cached file %s", out_nc)
            return out_nc

        xmin, ymin, xmax, ymax = extent

        logger.info("NASA POWER S3: opening Zarr store")
        fs = s3fs.S3FileSystem(anon=True)
        store = s3fs.S3Map(_S3_ZARR_PATH, s3=fs)
        ds_full = xr.open_zarr(store, consolidated=True)

        available = set(ds_full.data_vars)
        missing = [v for v in self.parameters if v not in available]
        if missing:
            raise ValueError(
                f"Variables not found in S3 Zarr store: {missing}. "
                f"Available: {sorted(available)}"
            )

        ds = (
            ds_full[self.parameters]
            .sel(time=slice(starting_date, ending_date))
            .sel(lat=slice(ymin, ymax), lon=slice(xmin, xmax))
        )

        for var in ds.data_vars:
            ds[var] = ds[var].where(ds[var] > -990)

        logger.info(
            "NASA POWER S3: loading %d vars × %d time steps, "
            "bbox=[%.2f,%.2f,%.2f,%.2f]",
            len(self.parameters), ds.dims.get("time", 0),
            xmin, ymin, xmax, ymax,
        )
        ds = ds.compute()

        encoding = {v: {"zlib": True, "complevel": 4} for v in ds.data_vars}
        ds.to_netcdf(out_nc, encoding=encoding, engine="netcdf4")
        logger.info("NASA POWER S3 saved → %s", out_nc)
        return out_nc
