"""
cdh.conventions
================

xclim meteorological naming convention mappings.

Maps internal/source variable names to xclim standard names and long_name
descriptions.  Applied to all final output cubes via ``apply_xclim_names``.
"""

from __future__ import annotations

import xarray as xr

# source_name → {xclim name, CF long_name}
XCLIM_NAMES: dict[str, dict[str, str]] = {
    # ── Temperature ────────────────────────────────────────────────────────
    "tmax":    {"name": "tasmax", "long_name": "Daily maximum near-surface air temperature"},
    "tmin":    {"name": "tasmin", "long_name": "Daily minimum near-surface air temperature"},
    "T2M":     {"name": "tas",    "long_name": "Mean daily near-surface air temperature"},
    "T2M_MAX": {"name": "tasmax", "long_name": "Daily maximum near-surface air temperature"},
    "T2M_MIN": {"name": "tasmin", "long_name": "Daily minimum near-surface air temperature"},
    # ── Precipitation ──────────────────────────────────────────────────────
    "precipitation": {"name": "pr", "long_name": "Mean daily precipitation flux"},
    "PRECTOTCORR":   {"name": "pr", "long_name": "Mean daily precipitation flux"},
    # ── Relative Humidity ──────────────────────────────────────────────────
    "RH2M":  {"name": "hurs",   "long_name": "Near-surface relative humidity"},
    "rh06":  {"name": "hurs06", "long_name": "Near-surface relative humidity at 06:00 UTC"},
    "rh09":  {"name": "hurs09", "long_name": "Near-surface relative humidity at 09:00 UTC"},
    "rh12":  {"name": "hurs12", "long_name": "Near-surface relative humidity at 12:00 UTC"},
    "rh15":  {"name": "hurs15", "long_name": "Near-surface relative humidity at 15:00 UTC"},
    "rh18":  {"name": "hurs18", "long_name": "Near-surface relative humidity at 18:00 UTC"},
    # ── Wind ───────────────────────────────────────────────────────────────
    "ws":    {"name": "sfcWind", "long_name": "Near-surface wind speed"},
    "WS2M":  {"name": "sfcWind", "long_name": "Near-surface wind speed at 2 m"},
    "WS10M": {"name": "sfcWind", "long_name": "Near-surface wind speed at 10 m"},
    # ── Solar / Shortwave Radiation ────────────────────────────────────────
    "srad":             {"name": "rsds", "long_name": "Surface downwelling shortwave radiation"},
    "ALLSKY_SFC_SW_DWN":{"name": "rsds", "long_name": "Surface downwelling shortwave radiation"},
    # ── Longwave Radiation ─────────────────────────────────────────────────
    "ALLSKY_SFC_LW_DWN":{"name": "rlds", "long_name": "Surface downwelling longwave radiation"},
    # ── Surface Pressure ───────────────────────────────────────────────────
    "PS": {"name": "ps", "long_name": "Surface air pressure"},
    # ── Evapotranspiration ─────────────────────────────────────────────────
    "etr": {"name": "evspsblpot", "long_name": "Potential evapotranspiration"},
    # ── Vapour Pressure (no xclim standard — long_name only) ───────────────
    "vp":  {"name": "vp",  "long_name": "Near-surface vapour pressure"},
    "vpd": {"name": "vpd", "long_name": "Vapour pressure deficit at maximum temperature"},
    "dpt": {"name": "dpt", "long_name": "Near-surface dew point temperature"},
}


def apply_xclim_names(ds: xr.Dataset) -> xr.Dataset:
    """Rename Dataset variables and set ``long_name`` to xclim conventions.

    Variables not present in :data:`XCLIM_NAMES` are left untouched.
    If two source variables would collide on the same xclim name, neither is
    renamed (they keep their original names) but ``long_name`` is still set.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset with source-specific variable names.

    Returns
    -------
    xr.Dataset
        Dataset with xclim variable names and ``long_name`` attributes.
    """
    # Build rename map and detect collisions
    rename_map: dict[str, str] = {}
    target_count: dict[str, int] = {}
    for var in ds.data_vars:
        if var in XCLIM_NAMES:
            target = XCLIM_NAMES[var]["name"]
            if target != var:
                target_count[target] = target_count.get(target, 0) + 1
                rename_map[var] = target

    # Drop colliding renames (keep original names for both conflicting vars)
    safe_rename = {k: v for k, v in rename_map.items() if target_count[v] == 1}
    if safe_rename:
        ds = ds.rename(safe_rename)

    # Reverse map: new_name → original name (for setting long_name)
    orig_for: dict[str, str] = {v: k for k, v in safe_rename.items()}

    for var in ds.data_vars:
        orig = orig_for.get(var, var)
        if orig in XCLIM_NAMES:
            ds[var].attrs["long_name"] = XCLIM_NAMES[orig]["long_name"]

    return ds
