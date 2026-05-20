---
name: notebook-plots
description: >
  Expert at writing interactive climate data visualization functions directly into the user's
  Jupyter notebook or Google Colab. Use this skill whenever the user wants to visualize, chart,
  plot, or map climate data — time series by region, spatial raster maps, admin unit bar comparisons,
  seasonal climatology (wet/dry season), or multi-variable dashboards. Works with pandas DataFrames
  produced by geospatial-cube-processor's summarize_by_admin, and xarray Datasets from mask_to_admin
  or stack_datasets. Also triggers for any gridded climate variable (precipitation, temperature, solar
  radiation, humidity, wind) that the user wants to see plotted.
  IMPORTANT: invoke this skill any time the user says "plot", "chart", "visualize", "show me",
  "map this", "compare regions", "seasonal pattern", "dashboard", or mentions making any kind of
  climate chart — even casually. Phrases like "can you make a graph of the rainfall?", "I want to
  see how temperature varies by district", or "show me the spatial distribution" are all strong
  signals. Also triggers when the user has xarray or pandas climate data and wants to see it, even
  if they don't use plotting terminology.
---

# Notebook Plots

This skill writes interactive plot functions **directly into the user's existing Jupyter notebook
or Colab cell**. No standalone script or package is created.

All plots use **plotly** as the primary tool — it works in Colab, JupyterLab, and classic Notebook
without any extra setup. **hvplot** is used for spatial raster maps when available, with a plotly
fallback for Colab users who haven't installed it.

---

## Before writing any code

Ask only what you don't already know from context:

- **What data do they have?** pandas DataFrame (from `summarize_by_admin`) or xarray Dataset
  (from `mask_to_admin` / `stack_datasets`)? Both? 
- **Which plot type?** Time series, spatial map, admin comparison, seasonal pattern, or dashboard?
- **Which variable(s)?** e.g. `precip`, `tmax`, `tmin`, `srad`, `rh`
- **Any time filter?** A specific year, month, or averaged period? (for spatial maps and comparisons)

If the user's message already answers these, skip to writing the function immediately.

---

## Setup cell (write once at the top of the notebook)

Include this setup cell whenever you introduce plot functions. It is idempotent — safe to run
multiple times.

```python
# ── Climate visualization setup ──────────────────────────────────────────────
try:
    import google.colab
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

import pandas as pd
import xarray as xr
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
warnings.filterwarnings("ignore")

# hvplot is optional — used for spatial quadmesh when available
try:
    import hvplot.xarray
    import hvplot.pandas
    import panel as pn
    pn.extension("bokeh")
    HAS_HVPLOT = True
except ImportError:
    HAS_HVPLOT = False
```

---

## Colormap helper

Place this near the top of the plotting section. All plot functions call it to pick an
appropriate colormap automatically from the variable name.

```python
def _get_cmap(var_name: str) -> str:
    v = var_name.lower()
    if any(k in v for k in ["precip", "rain", "chirps", "pr", "ppt"]):
        return "Blues"
    elif any(k in v for k in ["tmax", "tmin", "temp", "tmean", "t2m", "tas"]):
        return "RdYlBu_r"
    elif any(k in v for k in ["srad", "solar", "radiation", "rsds", "dswrf"]):
        return "YlOrRd"
    elif any(k in v for k in ["rh", "humidity", "hum", "hurs"]):
        return "BuGn"
    elif any(k in v for k in ["wind", "ws", "wv", "sfcwind"]):
        return "PuBu"
    else:
        return "Viridis"
```

---

## Plot Type 1 — Time Series

**Input:** pandas DataFrame with columns `admin_unit`, `variable`, `time`, `value`
(the standard output of `summarize_by_admin`).

**When to use:** The user has data over time and wants to see trends — monthly, seasonal, or
annual. One line per admin unit. Multiple variables → one subplot per variable.

```python
def plot_time_series(df: pd.DataFrame, variable: str = None, title: str = None):
    """
    Interactive time series from summarize_by_admin output.
    One line per admin unit; one panel per variable when multiple exist.
    """
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")

    variables = [variable] if variable else sorted(df["variable"].unique().tolist())

    if len(variables) == 1:
        var = variables[0]
        df_var = df[df["variable"] == var]
        fig = px.line(
            df_var, x="time", y="value", color="admin_unit",
            title=title or f"{var} — Time Series",
            labels={"value": var, "time": "Date", "admin_unit": "Region"},
            markers=True,
        )
        fig.update_layout(hovermode="x unified", legend_title="Admin Unit")
    else:
        colors = px.colors.qualitative.Set2
        admin_units = df["admin_unit"].unique()
        fig = make_subplots(
            rows=len(variables), cols=1,
            shared_xaxes=True,
            subplot_titles=variables,
            vertical_spacing=0.08,
        )
        for row_i, var in enumerate(variables, start=1):
            df_var = df[df["variable"] == var]
            for j, unit in enumerate(admin_units):
                df_unit = df_var[df_var["admin_unit"] == unit]
                fig.add_trace(
                    go.Scatter(
                        x=df_unit["time"], y=df_unit["value"],
                        name=unit, line=dict(color=colors[j % len(colors)]),
                        legendgroup=unit, showlegend=(row_i == 1),
                        mode="lines+markers",
                    ),
                    row=row_i, col=1,
                )
            fig.update_yaxes(title_text=var, row=row_i, col=1)
        fig.update_layout(
            title=title or "Climate Variables — Time Series",
            height=300 * len(variables),
            hovermode="x unified",
        )

    fig.show()
```

---

## Plot Type 2 — Spatial Map

**Input:** xarray Dataset (from `mask_to_admin` or `stack_datasets`).

**When to use:** The user wants to see the geographic distribution of a variable — a raster map
over the study area. Optionally filter to a specific time step; otherwise average over time.

```python
def plot_spatial_map(ds: xr.Dataset, variable: str = None, time: str = None, title: str = None):
    """
    Interactive raster map for one variable.
    Averages over all time steps when time=None.
    Uses hvplot.quadmesh when available; falls back to plotly Heatmap.
    """
    var = variable or list(ds.data_vars)[0]
    da = ds[var]

    if "time" in da.dims:
        if time:
            da = da.sel(time=time, method="nearest")
            time_label = str(da.time.values)[:10]
        else:
            da = da.mean(dim="time")
            time_label = "mean"
    else:
        time_label = ""

    cmap = _get_cmap(var)
    plot_title = title or (f"{var} ({time_label})" if time_label else var)

    # Resolve coordinate names (some datasets use latitude/longitude or x/y)
    x_dim = next((c for c in ["lon", "longitude", "x"] if c in da.coords), da.dims[-1])
    y_dim = next((c for c in ["lat", "latitude", "y"] if c in da.coords), da.dims[-2])

    if HAS_HVPLOT:
        p = da.hvplot.quadmesh(
            x=x_dim, y=y_dim, cmap=cmap,
            title=plot_title,
            xlabel="Longitude", ylabel="Latitude",
            colorbar=True, width=700, height=450,
        )
        return p  # display by placing at end of cell or wrapping in pn.pane.HoloViews(p)
    else:
        fig = px.imshow(
            da.values,
            x=da[x_dim].values,
            y=da[y_dim].values,
            color_continuous_scale=cmap,
            title=plot_title,
            labels={"color": var, "x": "Longitude", "y": "Latitude"},
            origin="lower",
            aspect="auto",
        )
        fig.update_coloraxes(colorbar_title=var)
        fig.show()
```

---

## Plot Type 3 — Admin Comparison

**Input:** pandas DataFrame with columns `admin_unit`, `variable`, `time`, `value`.

**When to use:** The user wants to compare a single metric across admin units — ranking regions,
spotting outliers, or summarizing a time period.

```python
def plot_admin_comparison(
    df: pd.DataFrame,
    variable: str = None,
    year: int = None,
    agg: str = "mean",
    title: str = None,
):
    """
    Sorted bar chart comparing admin units for one variable.
    agg: 'mean' | 'sum' | 'max' | 'min'
    """
    df = df.copy()
    if variable:
        df = df[df["variable"] == variable]
    if year:
        df["time"] = pd.to_datetime(df["time"])
        df = df[df["time"].dt.year == year]

    summary = df.groupby("admin_unit")["value"].agg(agg).reset_index()
    summary = summary.sort_values("value", ascending=False)

    var_label = variable or "value"
    suffix = f" — {year}" if year else ""
    fig = px.bar(
        summary, x="admin_unit", y="value",
        title=title or f"{var_label} by Admin Unit ({agg}){suffix}",
        labels={"value": var_label, "admin_unit": "Admin Unit"},
        color="value",
        color_continuous_scale=_get_cmap(var_label),
    )
    fig.update_layout(xaxis_tickangle=-30, showlegend=False)
    fig.show()
```

---

## Plot Type 4 — Seasonal Pattern

**Input:** pandas DataFrame with monthly data (columns `admin_unit`, `variable`, `time`, `value`).

**When to use:** The user wants to see within-year seasonal cycles, or the classic agroclimate
dual-axis chart combining precipitation bars and temperature line.

Auto-detects which variable is precipitation and which is temperature from the column names. If
only one variable is present, renders a climatology with individual years as faint overlay lines
and the long-term mean as a bold line.

```python
def plot_seasonal_pattern(
    df: pd.DataFrame,
    precip_var: str = None,
    temp_var: str = None,
    admin_unit: str = None,
    title: str = None,
):
    """
    Monthly climatology: precipitation bars + temperature line on secondary axis.
    Falls back to single-variable climatology with year overlays when only one variable exists.
    """
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df["month"] = df["time"].dt.month
    df["year"] = df["time"].dt.year

    if admin_unit:
        df = df[df["admin_unit"] == admin_unit]

    MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    variables = df["variable"].unique().tolist()

    # Auto-detect variable roles from name patterns
    def _is_precip(v):
        return any(k in v.lower() for k in ["precip", "rain", "pr", "chirps", "ppt"])

    def _is_temp(v):
        return any(k in v.lower() for k in ["tmax", "tmin", "temp", "tmean", "t2m", "tas"])

    if not precip_var:
        precip_var = next((v for v in variables if _is_precip(v)), None)
    if not temp_var:
        temp_var = next((v for v in variables if _is_temp(v) and v != precip_var), None)

    area_label = f" — {admin_unit}" if admin_unit else ""

    if precip_var and temp_var:
        # Dual-axis: bars for precip, line for temperature
        p_clim = (df[df["variable"] == precip_var]
                  .groupby("month")["value"].mean().reindex(range(1, 13)))
        t_clim = (df[df["variable"] == temp_var]
                  .groupby("month")["value"].mean().reindex(range(1, 13)))

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(x=MONTHS, y=p_clim.values, name=precip_var,
                   marker_color="#3182bd", opacity=0.75),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(x=MONTHS, y=t_clim.values, name=temp_var,
                       line=dict(color="#e6550d", width=2.5), mode="lines+markers"),
            secondary_y=True,
        )
        fig.update_layout(
            title=title or f"Seasonal Pattern{area_label}",
            hovermode="x unified",
            legend=dict(orientation="h", y=1.08),
        )
        fig.update_yaxes(title_text=f"{precip_var} (mm)", secondary_y=False)
        fig.update_yaxes(title_text=f"{temp_var} (°C)", secondary_y=True)
        fig.show()

    else:
        # Single variable: mean climatology + individual year overlays
        var = precip_var or temp_var or variables[0]
        df_var = df[df["variable"] == var]
        clim_mean = df_var.groupby("month")["value"].mean().reindex(range(1, 13))

        fig = go.Figure()
        for yr in sorted(df_var["year"].unique()):
            yr_data = df_var[df_var["year"] == yr].groupby("month")["value"].mean()
            fig.add_trace(go.Scatter(
                x=MONTHS, y=yr_data.reindex(range(1, 13)).values,
                name=str(yr), mode="lines",
                line=dict(width=1), opacity=0.35,
            ))
        fig.add_trace(go.Scatter(
            x=MONTHS, y=clim_mean.values,
            name="Mean", mode="lines+markers",
            line=dict(width=3, color="black"),
        ))
        fig.update_layout(
            title=title or f"{var} — Monthly Climatology{area_label}",
            yaxis_title=var, xaxis_title="Month",
        )
        fig.show()
```

---

## Plot Type 5 — Multi-variable Dashboard

**Input:** xarray Dataset with multiple variables (from `stack_datasets`).

**When to use:** The user wants a single overview of all climate layers — precipitation, max/min
temperature, solar radiation, and humidity in one view.

```python
def plot_dashboard(ds: xr.Dataset, time: str = None, ncols: int = 2, title: str = None):
    """
    Grid of spatial heatmaps, one panel per variable.
    Averages over time when time=None.
    """
    variables = list(ds.data_vars)
    nrows = -(-len(variables) // ncols)  # ceiling division

    fig = make_subplots(
        rows=nrows, cols=ncols,
        subplot_titles=variables,
        vertical_spacing=0.12,
        horizontal_spacing=0.06,
    )

    for idx, var in enumerate(variables):
        row = idx // ncols + 1
        col = idx % ncols + 1
        da = ds[var]

        if "time" in da.dims:
            da = da.sel(time=time, method="nearest") if time else da.mean(dim="time")

        x_dim = next((c for c in ["lon", "longitude", "x"] if c in da.coords), da.dims[-1])
        y_dim = next((c for c in ["lat", "latitude", "y"] if c in da.coords), da.dims[-2])

        colorbar_y = 1 - (row - 0.5) / nrows
        fig.add_trace(
            go.Heatmap(
                z=da.values,
                x=da[x_dim].values,
                y=da[y_dim].values,
                colorscale=_get_cmap(var),
                colorbar=dict(len=0.8 / nrows, y=colorbar_y,
                              title=var, thickness=10, x=col / ncols),
                showscale=True,
                name=var,
            ),
            row=row, col=col,
        )

    fig.update_layout(
        title=title or "Climate Variable Dashboard",
        height=380 * nrows,
    )
    fig.show()
```

---

## Colab-specific tips

- **plotly** renders inline automatically — `fig.show()` works without any extra setup.
- **hvplot** needs installing: run `!pip install hvplot panel` in a cell, then restart the runtime.
  After restart, call `pn.extension("bokeh")` in its own cell before rendering spatial maps.
- If `fig.show()` produces blank output in Colab, try `fig.show(renderer="colab")`.
- For notebooks served behind a proxy (JupyterHub), add `import plotly.io as pio; pio.renderers.default = "notebook"` to the setup cell.

---

## Common pitfalls

- **Time column as string:** always convert with `pd.to_datetime(df["time"])` before plotting —
  the functions do this internally, but if you filter the DataFrame first, convert before filtering.
- **Wrong coordinate names:** some datasets use `latitude`/`longitude` or `x`/`y` instead of
  `lat`/`lon`. The spatial functions auto-detect this, but if a plot is blank, check `list(da.coords)`.
- **Empty plot after masking:** the clipped region may contain mostly NaN. Add `da = da.dropna("lon", how="all").dropna("lat", how="all")` before plotting.
- **hvplot returning an object without rendering:** place the return value at the end of a cell
  (don't assign it to a variable), or wrap in `pn.panel(p).servable()`.
