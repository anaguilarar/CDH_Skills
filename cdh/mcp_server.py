"""
cdh MCP server
==============

Exposes the cdh climate-data package as MCP tools so an AI assistant
(Claude + climate-data-download skill) can orchestrate full download
workflows via natural language:

  list_admin_units → download_chirps / download_chirts
                   / download_agera5 / download_nasa_power

Start the server:
    python -m cdh.mcp_server

Or register it in .claude/mcp_config.json — see project README.

Requirements:
    pip install -e ".[full]"        # includes cdsapi for AgERA5
    ~/.cdsapirc configured          # AgERA5 only
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "cdh",
    instructions=(
        "Tools for downloading CHIRPS precipitation, CHIRTS-ERA5 temperature, "
        "AgERA5 agrometeorological indicators, and NASA POWER data for any "
        "country or admin unit, returning clipped xr.Datasets saved as NetCDF."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, default=str)


def _err(msg: str) -> str:
    return json.dumps({"status": "error", "message": msg})


def _save_nc(ds, path: str) -> str:
    """Save an xr.Dataset to a compressed NetCDF file and return the path."""
    import xarray as xr
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    encoding = {v: {"zlib": True, "complevel": 4} for v in ds.data_vars}
    ds.to_netcdf(path, encoding=encoding, engine="netcdf4")
    logger.info("Saved → %s", path)
    return path


def _nc_name(source: str, iso3: str, start: str, end: str) -> str:
    """Standardised NetCDF filename: {source}_{ISO3}_{start}_{end}.nc"""
    s = start.replace("-", "")
    e = end.replace("-", "")
    return f"{source}_{iso3.upper()}_{s}_{e}.nc"


# ---------------------------------------------------------------------------
# Tool 1 — list_admin_units
# ---------------------------------------------------------------------------

@mcp.tool()
def list_admin_units(
    country: str,
    adm_level: int = 1,
) -> str:
    """List administrative unit names for a country at a given level.

    Call this before any sub-country download to confirm the exact spelling
    of the *feature_name* parameter.

    Parameters
    ----------
    country : str
        Country name (e.g. ``"Honduras"``) or ISO 3166-1 alpha-3 code
        (e.g. ``"HND"``).
    adm_level : int
        Administrative level.  1 = region/province (default), 2 = district/department.

    Returns
    -------
    JSON with ``country``, ``adm_level``, ``count``, and sorted ``units`` list.
    """
    try:
        from cdh._api import _resolve_iso3
        from cdh.ingestion.boundaries import list_admin_units as _list

        iso3 = _resolve_iso3(country)
        names = _list(iso3, adm_level=adm_level)
        return _ok({
            "country": country,
            "iso3": iso3,
            "adm_level": adm_level,
            "count": len(names),
            "units": names,
        })
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Tool 2 — download_chirps
# ---------------------------------------------------------------------------

@mcp.tool()
def download_chirps(
    country: str,
    start_date: str,
    end_date: str,
    output_folder: str,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    version: str = "3.0",
) -> str:
    """Download CHIRPS daily precipitation and save as a NetCDF datacube.

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date, e.g. ``"2015-01-01"``.
    end_date : str
        ISO 8601 end date, e.g. ``"2020-12-31"``.
    output_folder : str
        Root folder for raw downloads **and** the output NetCDF.
        Must not contain spaces.
    adm_level : int
        Administrative level for boundary clipping.  0 = full country.
    feature_name : str | None
        Admin unit name when *adm_level* > 0 (use ``list_admin_units`` to
        confirm the exact spelling).
    ncores : int
        Parallel download workers (hard-capped at 3 to respect UCSB rate
        limits).  Default: 4.
    version : str
        CHIRPS version: ``"3.0"`` (default) or ``"2.0"``.

    Returns
    -------
    JSON with ``output_path``, ``output_folder``, ``iso3``, ``variables``,
    ``n_times``, ``bbox``.
    """
    try:
        from cdh._api import _resolve_iso3, fetch_chirps

        iso3 = _resolve_iso3(country)
        ds = fetch_chirps(
            country=country,
            start_date=start_date,
            end_date=end_date,
            output_folder=output_folder,
            adm_level=adm_level,
            feature_name=feature_name,
            ncores=ncores,
            version=version,
        )

        nc_path = os.path.join(output_folder, _nc_name("chirps", iso3, start_date, end_date))
        _save_nc(ds, nc_path)

        n_times = int(ds.dims.get("time", 1))
        bbox = [
            float(ds.lon.min()), float(ds.lat.min()),
            float(ds.lon.max()), float(ds.lat.max()),
        ]
        out_vars = list(ds.data_vars)
        ds.close()

        return _ok({
            "output_path": nc_path,
            "output_folder": output_folder,
            "iso3": iso3,
            "feature_name": feature_name,
            "variables": out_vars,
            "start_date": start_date,
            "end_date": end_date,
            "n_times": n_times,
            "bbox": [round(v, 4) for v in bbox],
            "source": f"CHIRPS v{version}",
        })
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Tool 3 — download_chirts
# ---------------------------------------------------------------------------

@mcp.tool()
def download_chirts(
    country: str,
    start_date: str,
    end_date: str,
    output_folder: str,
    variables: list[str] | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    source: str = "era5",
) -> str:
    """Download CHIRTS daily temperature and save as a NetCDF datacube.

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    output_folder : str
        Root folder for raw downloads and the output NetCDF.
    variables : list[str] | None
        One or both of ``["tmax", "tmin"]``.  Default: both.
    adm_level : int
        Administrative level.  0 = full country (default).
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    ncores : int
        Parallel download workers.  Default: 4.
    source : str
        ``"era5"`` (default) — CHIRTS-ERA5 experimental reanalysis blend;
        ``"chirts"`` — original CHIRTS-daily v1.0.

    Returns
    -------
    JSON with ``output_path``, ``variables``, ``n_times``, ``bbox``.
    """
    try:
        from cdh._api import _resolve_iso3, fetch_chirts

        variables = variables or ["tmax", "tmin"]
        iso3 = _resolve_iso3(country)
        ds = fetch_chirts(
            country=country,
            start_date=start_date,
            end_date=end_date,
            variables=variables,
            output_folder=output_folder,
            adm_level=adm_level,
            feature_name=feature_name,
            ncores=ncores,
            source=source,
        )

        nc_path = os.path.join(output_folder, _nc_name("chirts", iso3, start_date, end_date))
        _save_nc(ds, nc_path)

        n_times = int(ds.dims.get("time", 1))
        bbox = [
            float(ds.lon.min()), float(ds.lat.min()),
            float(ds.lon.max()), float(ds.lat.max()),
        ]
        out_vars = list(ds.data_vars)
        ds.close()

        return _ok({
            "output_path": nc_path,
            "output_folder": output_folder,
            "iso3": iso3,
            "feature_name": feature_name,
            "variables": out_vars,
            "start_date": start_date,
            "end_date": end_date,
            "n_times": n_times,
            "bbox": [round(v, 4) for v in bbox],
            "source": f"CHIRTS-{source.upper()}",
        })
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Tool 4 — download_agera5
# ---------------------------------------------------------------------------

@mcp.tool()
def download_agera5(
    country: str,
    start_date: str,
    end_date: str,
    output_folder: str,
    variables: list[str] | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    version: str = "2_0",
) -> str:
    """Download AgERA5 agrometeorological data and save as a NetCDF datacube.

    Requires a configured Copernicus CDS API key (``~/.cdsapirc``).
    Obtain one at https://cds.climate.copernicus.eu/ → your profile.

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    output_folder : str
        Root folder for raw downloads and the output NetCDF.
    variables : list[str] | None
        Keys from ``AGERA5_VARIABLE_MAP``.  Default:
        ``["temperature_tmax", "temperature_tmin", "solar_radiation", "wind_speed"]``.
        Full list: ``temperature_tmax``, ``temperature_tmin``, ``solar_radiation``,
        ``wind_speed``, ``vapour_pressure``, ``relative_humidity_max``,
        ``relative_humidity_min``, ``reference_evapotranspiration``,
        ``dew_point_temperature``.
    adm_level : int
        Administrative level.  0 = full country (default).
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    ncores : int
        Parallel year-downloads via CDS API.  Default: 4.
    version : str
        AgERA5 product version: ``"2_0"`` (default) or ``"1_1"``.

    Returns
    -------
    JSON with ``output_path``, ``variables``, ``n_times``, ``bbox``.
    """
    try:
        from cdh._api import _resolve_iso3, fetch_agera5

        variables = variables or [
            "temperature_tmax", "temperature_tmin", "solar_radiation", "wind_speed"
        ]
        iso3 = _resolve_iso3(country)
        ds = fetch_agera5(
            country=country,
            start_date=start_date,
            end_date=end_date,
            variables=variables,
            output_folder=output_folder,
            adm_level=adm_level,
            feature_name=feature_name,
            ncores=ncores,
            version=version,
        )

        nc_path = os.path.join(output_folder, _nc_name("agera5", iso3, start_date, end_date))
        _save_nc(ds, nc_path)

        n_times = int(ds.dims.get("time", 1))
        bbox = [
            float(ds.lon.min()), float(ds.lat.min()),
            float(ds.lon.max()), float(ds.lat.max()),
        ]
        out_vars = list(ds.data_vars)
        ds.close()

        return _ok({
            "output_path": nc_path,
            "output_folder": output_folder,
            "iso3": iso3,
            "feature_name": feature_name,
            "variables": out_vars,
            "start_date": start_date,
            "end_date": end_date,
            "n_times": n_times,
            "bbox": [round(v, 4) for v in bbox],
            "source": f"AgERA5 v{version}",
        })
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Tool 5 — download_nasa_power
# ---------------------------------------------------------------------------

@mcp.tool()
def download_nasa_power(
    country: str,
    start_date: str,
    end_date: str,
    output_folder: str,
    variables: list[str] | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    community: str = "AG",
) -> str:
    """Download NASA POWER daily data and save as a NetCDF datacube.

    No API key required — uses the public NASA POWER REST API.
    Large extents are tiled automatically (max 10° × 10° per request).

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    output_folder : str
        Root folder for raw downloads and the output NetCDF.
    variables : list[str] | None
        NASA POWER parameter codes.  Default:
        ``["ALLSKY_SFC_SW_DWN", "T2M_MAX", "T2M_MIN", "RH2M", "WS2M"]``.
        Other common codes: ``T2M``, ``T2MDEW``, ``WS10M``, ``PRECTOTCORR``.
    adm_level : int
        Administrative level.  0 = full country (default).
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    community : str
        NASA POWER community: ``"AG"`` (agriculture, default), ``"RE"``
        (renewable energy), or ``"SB"`` (sustainability).

    Returns
    -------
    JSON with ``output_path``, ``variables``, ``n_times``, ``bbox``.
    """
    try:
        from cdh._api import _resolve_iso3, fetch_nasa_power

        variables = variables or ["ALLSKY_SFC_SW_DWN", "T2M_MAX", "T2M_MIN", "RH2M", "WS2M"]
        iso3 = _resolve_iso3(country)
        ds = fetch_nasa_power(
            country=country,
            start_date=start_date,
            end_date=end_date,
            variables=variables,
            output_folder=output_folder,
            adm_level=adm_level,
            feature_name=feature_name,
            community=community,
        )

        nc_path = os.path.join(output_folder, _nc_name("nasa_power", iso3, start_date, end_date))
        _save_nc(ds, nc_path)

        n_times = int(ds.dims.get("time", 1))
        bbox = [
            float(ds.lon.min()), float(ds.lat.min()),
            float(ds.lon.max()), float(ds.lat.max()),
        ]
        out_vars = list(ds.data_vars)
        ds.close()

        return _ok({
            "output_path": nc_path,
            "output_folder": output_folder,
            "iso3": iso3,
            "feature_name": feature_name,
            "variables": out_vars,
            "start_date": start_date,
            "end_date": end_date,
            "n_times": n_times,
            "bbox": [round(v, 4) for v in bbox],
            "source": f"NASA POWER (community={community})",
        })
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
