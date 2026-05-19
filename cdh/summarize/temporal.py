"""
cdh.summarize.temporal
=======================

Temporal aggregation of climate Datasets.

Provides ``aggregate_temporal`` which resamples or groups a ``time × lat × lon``
Dataset to a coarser temporal resolution (monthly, seasonal, or annual).
"""

from __future__ import annotations

import logging
from typing import Literal

import xarray as xr

logger = logging.getLogger(__name__)

_FREQ_ALIASES: dict[str, str] = {
    # User-friendly → pandas offset alias
    "monthly": "ME",
    "month": "ME",
    "ME": "ME",
    "MS": "MS",
    "seasonal": "QS-DEC",
    "season": "QS-DEC",
    "QS-DEC": "QS-DEC",
    "annual": "YS",
    "yearly": "YS",
    "year": "YS",
    "YS": "YS",
    "YE": "YE",
}

_METHOD_FUNC = {
    "mean": "mean",
    "sum": "sum",
    "max": "max",
    "min": "min",
    "median": "median",
    "std": "std",
}


def aggregate_temporal(
    ds: xr.Dataset,
    freq: str = "monthly",
    method: str = "mean",
    time_dim: str = "time",
    min_count: int | None = None,
) -> xr.Dataset:
    """Resample a climate Dataset to a coarser temporal frequency.

    Parameters
    ----------
    ds : xr.Dataset
        Input Dataset with a ``time`` (or ``time_dim``) dimension of
        ``datetime64`` values.
    freq : str
        Target frequency.  Accepts pandas offset aliases or human-friendly
        names:  ``'monthly'`` / ``'ME'``, ``'seasonal'`` / ``'QS-DEC'``,
        ``'annual'`` / ``'YS'``.  Default: ``'monthly'``.
    method : str
        Aggregation method: ``'mean'`` (default), ``'sum'``, ``'max'``,
        ``'min'``, ``'median'``, ``'std'``.
    time_dim : str
        Name of the time dimension.  Default: ``'time'``.
    min_count : int | None
        Passed to ``sum`` resampler to require a minimum number of valid
        observations per bin.  Only used when ``method='sum'``.  Default: None.

    Returns
    -------
    xr.Dataset
        Aggregated Dataset with the same spatial dimensions.

    Examples
    --------
    >>> monthly_mean = aggregate_temporal(ds, freq="monthly", method="mean")
    >>> annual_total = aggregate_temporal(ds, freq="annual", method="sum")
    """
    if time_dim not in ds.dims:
        raise ValueError(
            f"Time dimension '{time_dim}' not found. Available dims: {list(ds.dims)}"
        )
    if method not in _METHOD_FUNC:
        raise ValueError(f"method must be one of {list(_METHOD_FUNC)}, got '{method}'")

    freq_key = _FREQ_ALIASES.get(freq, freq)
    resampler = ds.resample({time_dim: freq_key})

    if method == "sum" and min_count is not None:
        agg = resampler.sum(min_count=min_count)
    else:
        agg = getattr(resampler, _METHOD_FUNC[method])()

    try:
        import rioxarray  # noqa: F401
        if ds.rio.crs is not None:
            agg.rio.write_crs(ds.rio.crs, inplace=True)
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "aggregate_temporal: %s → %s (method=%s, n_steps=%d → %d)",
        freq, freq_key, method,
        len(ds[time_dim]), len(agg[time_dim]),
    )
    return agg


def seasonal_climatology(
    ds: xr.Dataset,
    method: str = "mean",
    time_dim: str = "time",
) -> xr.Dataset:
    """Compute mean seasonal climatology (DJF / MAM / JJA / SON).

    Parameters
    ----------
    ds : xr.Dataset
    method : str
        ``'mean'`` (default) or ``'sum'``.
    time_dim : str
        Time dimension name.

    Returns
    -------
    xr.Dataset
        Dataset with a ``season`` coordinate (DJF, MAM, JJA, SON).
    """
    grp = ds.groupby(f"{time_dim}.season")
    return getattr(grp, _METHOD_FUNC.get(method, "mean"))()


def monthly_climatology(
    ds: xr.Dataset,
    method: str = "mean",
    time_dim: str = "time",
) -> xr.Dataset:
    """Compute mean monthly climatology (12 values, one per calendar month).

    Parameters
    ----------
    ds : xr.Dataset
    method : str
        ``'mean'`` (default) or ``'sum'``.
    time_dim : str
        Time dimension name.

    Returns
    -------
    xr.Dataset
        Dataset with a ``month`` coordinate (1–12).
    """
    grp = ds.groupby(f"{time_dim}.month")
    return getattr(grp, _METHOD_FUNC.get(method, "mean"))()
