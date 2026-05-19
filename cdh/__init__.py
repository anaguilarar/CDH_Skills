"""
cdh — Climate Data Hub
=======================

High-level package for fetching, processing, and analysing gridded climate
data from CHIRPS, CHIRTS-daily, AgERA5, and NASA POWER.

Quick start
-----------
>>> from cdh import fetch_chirps, fetch_chirts, fetch_agera5, fetch_nasa_power
>>> from cdh.summarize import aggregate_temporal, aggregate_by_admin
>>>
>>> chirps_ds = fetch_chirps("Honduras", "2015-01-01", "2020-12-31")
>>> chirts_ds = fetch_chirts("Honduras", "2015-01-01", "2020-12-31",
...                           variables=["tmax", "tmin"])
>>> agera5_ds = fetch_agera5("Honduras", "2015-01-01", "2020-12-31",
...                           variables=["solar_radiation", "wind_speed"])
>>> power_ds  = fetch_nasa_power("Honduras", "2015-01-01", "2020-12-31",
...                               variables=["ALLSKY_SFC_SW_DWN", "T2M_MAX"])
"""

from __future__ import annotations

from cdh._api import (
    fetch_agera5,
    fetch_chirps,
    fetch_chirts,
    fetch_nasa_power,
)

__version__ = "0.1.0"

__all__ = [
    "fetch_chirps",
    "fetch_chirts",
    "fetch_agera5",
    "fetch_nasa_power",
]
