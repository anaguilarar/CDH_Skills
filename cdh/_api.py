"""
cdh._api
=========

Top-level convenience functions for fetching climate datasets.

Each ``fetch_*`` function:
    1. Resolves the country name → ISO 3166-1 alpha-3 code.
    2. Fetches the administrative boundary from GeoBoundaries.
    3. Computes the bounding box.
    4. Downloads raw files to *output_folder* (temp dir if not given).
    5. Builds a normalised ``xr.Dataset`` (``lat`` / ``lon`` / ``time``, EPSG:4326).
    6. Clips the dataset to the country / admin boundary polygon.
    7. Returns the ready-to-use Dataset.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import geopandas as gpd
import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Country name → ISO3 resolver
# ---------------------------------------------------------------------------

def _resolve_iso3(country: str) -> str:
    """Return an ISO 3166-1 alpha-3 code for *country* (name or code).

    Uses pycountry when available; falls back to treating the input as-is.
    """
    if len(country) == 3 and country.isalpha():
        return country.upper()

    try:
        import pycountry
        result = pycountry.countries.search_fuzzy(country)
        if result:
            return result[0].alpha_3
    except Exception:  # noqa: BLE001
        pass

    logger.warning(
        "_resolve_iso3: could not look up '%s' via pycountry; treating as ISO3.", country
    )
    return country.upper()


# ---------------------------------------------------------------------------
# Boundary helpers
# ---------------------------------------------------------------------------

def _get_boundary(
    iso3: str,
    adm_level: int,
    feature_name: str | None,
) -> gpd.GeoDataFrame:
    """Return the appropriate boundary GeoDataFrame."""
    from cdh.ingestion.boundaries import get_admin_boundary, get_country_boundary

    if adm_level == 0 or feature_name is None:
        return get_country_boundary(iso3)
    return get_admin_boundary(iso3, feature_name, adm_level=adm_level)


def _bbox_from_gdf(gdf: gpd.GeoDataFrame, pad: float = 0.1) -> list[float]:
    """Return ``[xmin, ymin, xmax, ymax]`` with a small outward padding."""
    x1, y1, x2, y2 = gdf.total_bounds
    return [float(x1) - pad, float(y1) - pad, float(x2) + pad, float(y2) + pad]


# ---------------------------------------------------------------------------
# CHIRPS
# ---------------------------------------------------------------------------

def fetch_chirps(
    country: str,
    start_date: str,
    end_date: str,
    output_folder: str | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    version: str = "3.0",
) -> xr.Dataset:
    """Download CHIRPS daily precipitation and return a clipped xr.Dataset.

    Parameters
    ----------
    country : str
        Country name (e.g. ``"Honduras"``) or ISO 3166-1 alpha-3 code
        (e.g. ``"HND"``).
    start_date : str
        ISO 8601 start date, e.g. ``"2015-01-01"``.
    end_date : str
        ISO 8601 end date, e.g. ``"2020-12-31"``.
    output_folder : str | None
        Root folder for raw downloads.  A temporary directory is used when
        ``None`` — note that its contents will survive the process but may be
        cleaned up by the OS.
    adm_level : int
        Administrative level for boundary clipping.  ``0`` = full country
        (default).
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    ncores : int
        Parallel download workers.  Hard-capped at 3 inside the downloader
        to respect UCSB server rate limits.  Default: 4.
    version : str
        CHIRPS version: ``"3.0"`` (default) or ``"2.0"``.

    Returns
    -------
    xr.Dataset
        ``precipitation`` variable, dims ``(time, lat, lon)``, EPSG:4326,
        clipped to the country / admin-unit boundary.
    """
    from cdh.ingestion.chirps import CHIRPSDownloader
    from cdh.spatial.raster_ops import get_roi_data
    from cdh.transform.cube_builder import SourceCubeBuilder

    iso3 = _resolve_iso3(country)
    boundary = _get_boundary(iso3, adm_level, feature_name)
    bbox = _bbox_from_gdf(boundary)

    if output_folder is None:
        output_folder = tempfile.mkdtemp(prefix="cdh_chirps_")

    raw_folder = os.path.join(output_folder, "chirps")
    Path(raw_folder).mkdir(parents=True, exist_ok=True)

    logger.info("fetch_chirps: %s  %s → %s", iso3, start_date, end_date)
    CHIRPSDownloader(version=version).download(
        extent=bbox,
        starting_date=start_date,
        ending_date=end_date,
        output_folder=raw_folder,
        ncores=ncores,
    )

    ds = SourceCubeBuilder(
        directory_paths={"precipitation": raw_folder},
        extent=bbox,
    ).build(start_date, end_date)

    return get_roi_data(ds, boundary, xyxy=tuple(boundary.total_bounds))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CHIRTS
# ---------------------------------------------------------------------------

def fetch_chirts(
    country: str,
    start_date: str,
    end_date: str,
    variables: list[str] | None = None,
    output_folder: str | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    source: str = "era5",
) -> xr.Dataset:
    """Download CHIRTS temperature data and return a clipped xr.Dataset.

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    variables : list[str] | None
        One or both of ``["tmax", "tmin"]``.  Default: both.
    output_folder : str | None
        Root folder for raw downloads.  Temp dir used when ``None``.
    adm_level : int
        Administrative level.  Default: ``0`` (full country).
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    ncores : int
        Parallel download workers.  Default: 4.
    source : str
        Data source:

        * ``"era5"`` *(default)* — CHIRTS-ERA5 experimental reanalysis blend
          (``https://data.chc.ucsb.edu/experimental/CHIRTS-ERA5/``).
        * ``"chirts"`` — Original CHIRTS-daily v1.0 COG product.

    Returns
    -------
    xr.Dataset
        ``tmax`` and/or ``tmin`` variables, dims ``(time, lat, lon)``,
        EPSG:4326, clipped to boundary.
    """
    from cdh.ingestion.chirts import CHIRTSDownloader
    from cdh.spatial.raster_ops import get_roi_data
    from cdh.transform.cube_builder import SourceCubeBuilder

    variables = variables or ["tmax", "tmin"]
    iso3 = _resolve_iso3(country)
    boundary = _get_boundary(iso3, adm_level, feature_name)
    bbox = _bbox_from_gdf(boundary)

    if output_folder is None:
        output_folder = tempfile.mkdtemp(prefix="cdh_chirts_")

    raw_folder = os.path.join(output_folder, "chirts")
    Path(raw_folder).mkdir(parents=True, exist_ok=True)

    logger.info(
        "fetch_chirts: %s  source=%s  vars=%s  %s → %s",
        iso3, source, variables, start_date, end_date,
    )
    CHIRTSDownloader(variables=variables, source=source).download(
        extent=bbox,
        starting_date=start_date,
        ending_date=end_date,
        output_folder=raw_folder,
        ncores=ncores,
    )

    # CHIRTSDownloader saves to {raw_folder}/{variable}/{year}/
    directory_paths = {var: os.path.join(raw_folder, var) for var in variables}
    ds = SourceCubeBuilder(directory_paths=directory_paths, extent=bbox).build(start_date, end_date)

    return get_roi_data(ds, boundary, xyxy=tuple(boundary.total_bounds))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AgERA5
# ---------------------------------------------------------------------------

def fetch_agera5(
    country: str,
    start_date: str,
    end_date: str,
    variables: list[str] | None = None,
    output_folder: str | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    ncores: int = 4,
    version: str = "2_0",
) -> xr.Dataset:
    """Download AgERA5 agrometeorological data and return a clipped xr.Dataset.

    Requires a configured Copernicus CDS API key (``~/.cdsapirc`` or the
    ``CDSAPI_KEY`` / ``CDSAPI_URL`` environment variables).

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    variables : list[str] | None
        Keys from ``AGERA5_VARIABLE_MAP``.  Default:
        ``["temperature_tmax", "temperature_tmin", "solar_radiation",
        "wind_speed"]``.
    output_folder : str | None
        Root folder for raw downloads.  Temp dir used when ``None``.
    adm_level : int
        Administrative level.  Default: ``0``.
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    ncores : int
        Parallel year-downloads via CDS API.  Default: 4.
    version : str
        AgERA5 product version: ``"2_0"`` (default) or ``"1_1"``.

    Returns
    -------
    xr.Dataset
        One variable per selected indicator, dims ``(time, lat, lon)``,
        EPSG:4326, clipped to boundary.
    """
    from cdh.ingestion.agera5 import AGERA5_SHORT_NAMES, AGERA5_VARIABLE_MAP, AgEra5Downloader
    from cdh.spatial.raster_ops import get_roi_data
    from cdh.transform.cube_builder import SourceCubeBuilder

    variables = variables or [
        "temperature_tmax", "temperature_tmin", "solar_radiation", "wind_speed"
    ]
    iso3 = _resolve_iso3(country)
    boundary = _get_boundary(iso3, adm_level, feature_name)
    bbox = _bbox_from_gdf(boundary)

    if output_folder is None:
        output_folder = tempfile.mkdtemp(prefix="cdh_agera5_")

    agera5_root = os.path.join(output_folder, "agera5")
    Path(agera5_root).mkdir(parents=True, exist_ok=True)

    dl = AgEra5Downloader(version=version)
    directory_paths: dict[str, str] = {}

    for var_key in variables:
        if var_key not in AGERA5_VARIABLE_MAP:
            logger.warning("fetch_agera5: unknown variable '%s', skipping.", var_key)
            continue

        cfg = AGERA5_VARIABLE_MAP[var_key]
        short_name = AGERA5_SHORT_NAMES.get(var_key, var_key)
        var_folder = os.path.join(agera5_root, short_name)
        Path(var_folder).mkdir(parents=True, exist_ok=True)

        logger.info(
            "fetch_agera5: %s  var='%s' (→ '%s')  %s → %s",
            iso3, var_key, short_name, start_date, end_date,
        )
        dl.download(
            variable=cfg["variable"],
            starting_date=start_date,
            ending_date=end_date,
            output_folder=var_folder,
            aoi_extent=bbox,
            statistic=cfg.get("statistic"),
            time=cfg.get("time"),
            ncores=ncores,
        )
        directory_paths[short_name] = var_folder

    if not directory_paths:
        raise ValueError("fetch_agera5: no valid variables to download.")

    ds = SourceCubeBuilder(directory_paths=directory_paths, extent=bbox).build(start_date, end_date)

    return get_roi_data(ds, boundary, xyxy=tuple(boundary.total_bounds))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# NASA POWER
# ---------------------------------------------------------------------------

def fetch_nasa_power(
    country: str,
    start_date: str,
    end_date: str,
    variables: list[str] | None = None,
    output_folder: str | None = None,
    adm_level: int = 0,
    feature_name: str | None = None,
    community: str = "AG",
    force: bool = False,
) -> xr.Dataset:
    """Download NASA POWER daily data and return a clipped xr.Dataset.

    Parameters
    ----------
    country : str
        Country name or ISO3 code.
    start_date : str
        ISO 8601 start date.
    end_date : str
        ISO 8601 end date.
    variables : list[str] | None
        NASA POWER parameter codes.  Default:
        ``["ALLSKY_SFC_SW_DWN", "T2M_MAX", "T2M_MIN", "RH2M", "WS2M"]``.
    output_folder : str | None
        Root folder for raw downloads.  Temp dir used when ``None``.
    adm_level : int
        Administrative level.  Default: ``0``.
    feature_name : str | None
        Admin unit name when *adm_level* > 0.
    community : str
        NASA POWER community: ``"AG"`` (agriculture, default), ``"RE"``
        (renewable energy), or ``"SB"`` (sustainability).
    force : bool
        Re-download even if a cached file exists.  Default: ``False``.

    Returns
    -------
    xr.Dataset
        One variable per parameter code, dims ``(time, lat, lon)``,
        EPSG:4326, clipped to boundary.
    """
    from cdh.ingestion.nasa_power import NASAPowerS3Downloader
    from cdh.spatial.raster_ops import get_roi_data
    from cdh.transform.cube_builder import build_nasa_power_cube

    iso3 = _resolve_iso3(country)
    boundary = _get_boundary(iso3, adm_level, feature_name)
    bbox = _bbox_from_gdf(boundary)

    if output_folder is None:
        output_folder = tempfile.mkdtemp(prefix="cdh_nasa_power_")

    power_folder = os.path.join(output_folder, "nasa_power")
    Path(power_folder).mkdir(parents=True, exist_ok=True)

    logger.info("fetch_nasa_power: %s  %s → %s", iso3, start_date, end_date)
    nc_path = NASAPowerS3Downloader(parameters=variables).download(
        extent=bbox,
        starting_date=start_date,
        ending_date=end_date,
        output_folder=power_folder,
        force=force,
    )

    ds = build_nasa_power_cube(nc_path, parameters=variables)
    return get_roi_data(ds, boundary, xyxy=tuple(boundary.total_bounds))  # type: ignore[arg-type]
