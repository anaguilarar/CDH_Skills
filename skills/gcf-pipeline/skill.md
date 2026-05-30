---
name: gcf-pipeline
description: >
  Full end-to-end climate data pipeline orchestrator: download → spatial processing → visualization,
  all in one workflow. Invoke this skill whenever the user wants to get AND see/visualize climate data
  together — even if they don't use technical terms. Strong trigger phrases: "download and visualize",
  "get and show", "I want to see [variable] for [country/year]", "full pipeline", "gcf-pipeline",
  "accumulated [variable] in [country]", "map [variable] over [country]", "show me [variable] for [year]".
  Also triggers for any prompt that clearly implies all three steps at once: fetching climate data,
  processing it spatially (masking, aggregating), AND plotting the results. Do NOT invoke if the user
  only wants to download (no visualization) or only wants to plot (data already loaded) — use the
  individual climate-data-download or notebook-plots skills for those cases instead.
---

# gcf-pipeline — Get, Clip, Figure Pipeline

You are a climate data pipeline orchestrator. When this skill is invoked, you run all three stages — **download → process → visualize** — as one seamless workflow. The user gives a natural-language request; you handle the routing, execution, and notebook generation.

**Key principle:** Download and processing run HERE in this session (via Python/Bash). The notebook receives ONLY visualization cells — it does not download or process anything.

---

## Stage overview

```
1. COLLECT    → gather all parameters in one turn (ask only what's missing)
2. PLAN       → show the full 3-step plan, get ONE confirmation
3. DOWNLOAD   → run Python via Bash to fetch and save data to disk
4. PROCESS    → run Python via Bash: mask + aggregate → save CSV
5. VISUALIZE  → write ONLY plot cells into the notebook (load CSV + plot)
6. SUMMARIZE  → show a completion table
```

---

## STAGE 1 — Collect parameters

Extract from the user's prompt first — ask only for what's genuinely missing. Accept vague answers and apply defaults rather than blocking.

| Parameter | Required | Default | Notes |
|-----------|----------|---------|-------|
| Country | yes | — | Full name or ISO3 code |
| Variable(s) | yes | — | e.g. solar radiation, precipitation, temperature |
| Date range | yes | — | YYYY-MM-DD start and end |
| Output folder | yes | — | **No spaces allowed in path** |
| Admin level | no | 0 | 0 = full country, 1 = province, 2 = district |
| Aggregation | no | auto | "sum" if user says "accumulated/total"; "mean" otherwise |
| Temporal frequency | no | "YE" | monthly = "ME", annual = "YE", none = None |
| Plot type | no | auto | spatial map, time series, admin comparison, seasonal, dashboard |

**Auto-detect rules (apply silently):**
- "accumulated", "total" → `agg_method="sum"`
- "average", "mean" → `agg_method="mean"`
- Single variable + single year, no temporal freq → `plot_type="spatial_map"` (default)
- Multiple variables → `plot_type="dashboard"`
- Multi-year range + admin level ≥ 1 → `plot_type="time_series"`
- Monthly frequency → `plot_type="seasonal_pattern"`

---

## STAGE 2 — Show the full plan (ONE confirmation)

Before running any code, show this table and wait for the user's "yes":

```
Here's what I'll do:

STEP 1 — DOWNLOAD  (runs now in this session)
| Variable        | Source     | Python call         | API variable       |
|-----------------|------------|---------------------|--------------------|
| Solar radiation | NASA POWER | fetch_nasa_power    | ALLSKY_SFC_SW_DWN |

STEP 2 — PROCESS  (runs now in this session)
- Mask to: Malawi (ISO3: MWI, admin level 0 — full country)
- Aggregate: sum per month (temporal_freq="ME")
- Save: D:/data/malawi_solar/summary_monthly.csv

STEP 3 — VISUALIZE  (written into notebook)
- Notebook: D:/data/malawi_solar/malawi_solar_2012.ipynb
- Plot type: seasonal_pattern — monthly accumulated solar radiation
- Notebook will only contain: load CSV + plot (no download or processing code)

Country: Malawi | Period: 2012-01-01 → 2012-12-31
Output folder: D:/data/malawi_solar

Shall I proceed?
```

Mention any defaults you applied so the user can correct them before you start.

---

## STAGE 3 — Variable → source routing

Route variables automatically using the `cdh` Python package — never ask the user to choose a source.

| Variable requested | Source | Python function | API variable / arg |
|--------------------|--------|-----------------|-------------------|
| Precipitation / rainfall | CHIRPS | `from cdh import fetch_chirps` | — |
| Temperature (Tmax / Tmin) | CHIRTS-ERA5 | `from cdh import fetch_chirts` | `variables=["tmax"]` / `["tmin"]` |
| Solar radiation | NASA POWER | `from cdh import fetch_nasa_power` | `variables=["ALLSKY_SFC_SW_DWN"]` |
| Relative humidity | NASA POWER | `from cdh import fetch_nasa_power` | `variables=["RH2M"]` |
| Wind speed | NASA POWER | `from cdh import fetch_nasa_power` | `variables=["WS2M"]` |
| Any other variable | NASA POWER | `from cdh import fetch_nasa_power` | matching parameter code |

**Before the first download, ensure `cdh` is installed by running this Bash command:**

```bash
python -c "import cdh; import s3fs; import zarr" 2>/dev/null || pip install -q git+https://github.com/anaguilarar/CDH_Skills.git s3fs zarr
```

This check is a no-op if all three are already installed; otherwise it installs `cdh`, `s3fs`, and `zarr` together. `s3fs` and `zarr` are required by `fetch_nasa_power` for its S3 Zarr backend lookup even when it falls back to the REST API.

**Download execution — run this Python block via Bash:**

```python
from cdh import fetch_nasa_power   # or fetch_chirps / fetch_chirts
import xarray as xr

ds = fetch_nasa_power(
    country="<COUNTRY>",
    start_date="<START>",
    end_date="<END>",
    variables=["<VAR_CODE>"],
    output_folder="<OUTPUT_FOLDER>",
    adm_level=<LEVEL>,
)

nc_path = "<OUTPUT_FOLDER>/<filename>.nc"
ds.to_netcdf(nc_path)
print("Saved:", nc_path)
print("Variables:", list(ds.data_vars))
print("Dims:", dict(ds.sizes))
print("Time:", str(ds.time.values[0])[:10], "->", str(ds.time.values[-1])[:10])
```

After the Bash call succeeds: report the saved path and variable names (note that `cdh` may rename variables, e.g. `ALLSKY_SFC_SW_DWN` → `rsds`).

**Path check:** If the output folder contains spaces, warn the user and suggest a space-free path before running.

---

## STAGE 4 — Process (run in-session via Bash)

After download completes, run the masking and aggregation immediately via Bash Python.

```python
import xarray as xr
import geopandas as gpd
import rioxarray
import pandas as pd

ds = xr.open_dataset("<NC_PATH>")

# GADM boundary
gdf = gpd.read_file(
    "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_<ISO3>_<LEVEL>.json"
).to_crs("EPSG:4326")

# Clip to country boundary
ds = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)
ds = ds.rio.write_crs("EPSG:4326", inplace=True)
ds_masked = ds.rio.clip(gdf.geometry, gdf.crs, drop=True, all_touched=True)

# Temporal aggregation — use actual agg_method and temporal_freq from user request
records = []
for var in ds_masked.data_vars:
    da = ds_masked[var]
    if "<TEMPORAL_FREQ>" != "None":
        da_agg = getattr(da.resample(time="<TEMPORAL_FREQ>"), "<AGG_METHOD>")()
        for t in da_agg.time.values:
            val = float(getattr(da_agg.sel(time=t), "<AGG_METHOD>")(dim=["lat", "lon"]).values)
            records.append({"variable": var, "time": str(t)[:10], "value": round(val, 4)})
    else:
        val = float(getattr(da, "<AGG_METHOD>")(dim=["lat", "lon", "time"]).values)
        records.append({"variable": var, "time": None, "value": round(val, 4)})

df = pd.DataFrame(records)
csv_path = "<OUTPUT_FOLDER>/summary_<TEMPORAL_FREQ>_<ISO3>_<YEAR>.csv"
df.to_csv(csv_path, index=False)
print(f"Saved CSV: {csv_path}")
print(df.to_string(index=False))
```

Replace all `<…>` placeholders with real values. After the Bash call: report the CSV path and show the first few rows of the summary.

**For admin level ≥ 1:** loop over each row in `gdf` and clip per unit before aggregating, to get per-district/province values. The CSV then has columns: `admin_unit, variable, time, value`.

---

## STAGE 5 — Visualize (write ONLY plot cells into notebook)

Create a new notebook at `<OUTPUT_FOLDER>/<country>_<variable>_<year>.ipynb`. Write exactly three cells:

### Cell 1 — Markdown header

```markdown
# <Country> — <Variable> <Period>

**Source:** <Source> | **Aggregation:** <agg_method> per <freq>
**Data:** `<csv_path>`
```

### Cell 2 — Imports + load data

```python
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings; warnings.filterwarnings("ignore")

# Pre-processed data (downloaded and aggregated externally)
df = pd.read_csv("<CSV_PATH>")
df["time"] = pd.to_datetime(df["time"])
df["month"] = df["time"].dt.month
print(df.to_string(index=False))
```

### Cell 3 — Plot function + call

Write only the plot function that matches the auto-detected `plot_type`. Fill in all values (variable name, title, units) from the actual data. Full implementations:

**plot_seasonal_pattern** (use when `temporal_freq="ME"`)

```python
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
var = "<ACTUAL_VAR_NAME>"   # e.g. "rsds", "precipitation", "tmax"

df_var = df[df["variable"] == var].sort_values("month")
monthly_vals = df_var.set_index("month")["value"].reindex(range(1, 13))

fig = go.Figure()
fig.add_trace(go.Bar(
    x=MONTHS,
    y=monthly_vals.values,
    marker=dict(
        color=monthly_vals.values,
        colorscale="<COLORSCALE>",   # YlOrRd=solar, Blues=precip, RdYlBu_r=temp
        showscale=True,
        colorbar=dict(title="<UNITS>"),
    ),
    name="Monthly <agg_method>",
    hovertemplate="%{x}: %{y:.1f} <UNITS><extra></extra>",
))
fig.update_layout(
    title="<VAR> — Monthly <AGG_LABEL> (<Country> <Year>)",
    xaxis_title="Month",
    yaxis_title="<VAR> (<UNITS>)",
    hovermode="x unified",
    template="plotly_white",
    width=800, height=500,
)
fig.show()
```

**plot_spatial_map** (use when no temporal aggregation or `plot_type="spatial_map"`)

```python
import xarray as xr
import plotly.express as px

ds = xr.open_dataset("<NC_PATH>")
var = "<ACTUAL_VAR_NAME>"
da = ds[var].mean(dim="time") if "time" in ds[var].dims else ds[var]

fig = px.imshow(
    da.values,
    x=da.lon.values, y=da.lat.values,
    color_continuous_scale="<COLORSCALE>",
    title="<VAR> — <AGG_LABEL> (<Country> <Period>)",
    labels={"color": "<UNITS>", "x": "Longitude", "y": "Latitude"},
    origin="lower", aspect="auto",
)
fig.update_coloraxes(colorbar_title="<UNITS>")
fig.show()
```

**plot_time_series** (use when `admin_level ≥ 1` and multi-year or `plot_type="time_series"`)

```python
import plotly.express as px

fig = px.line(
    df, x="time", y="value", color="admin_unit",
    title="<VAR> — Time Series (<Country>)",
    labels={"value": "<VAR> (<UNITS>)", "time": "Date", "admin_unit": "Region"},
    markers=True,
)
fig.update_layout(hovermode="x unified", legend_title="Admin Unit")
fig.show()
```

**plot_admin_comparison** (use when `admin_level ≥ 1` and single time step)

```python
import plotly.express as px

summary = df.groupby("admin_unit")["value"].mean().reset_index().sort_values("value", ascending=False)
fig = px.bar(
    summary, x="admin_unit", y="value",
    title="<VAR> by Admin Unit (<Country> <Period>)",
    labels={"value": "<VAR> (<UNITS>)", "admin_unit": "Admin Unit"},
    color="value", color_continuous_scale="<COLORSCALE>",
)
fig.update_layout(xaxis_tickangle=-30, showlegend=False)
fig.show()
```

**Colorscale + units reference:**

| Variable | Colorscale | Units |
|----------|-----------|-------|
| Solar radiation (rsds / ALLSKY_SFC_SW_DWN) | `YlOrRd` | MJ/m²/month |
| Precipitation | `Blues` | mm/month |
| Temperature (tmax / tmin) | `RdYlBu_r` | °C |
| Humidity (RH2M) | `BuGn` | % |
| Wind speed (WS2M) | `PuBu` | m/s |

---

## STAGE 6 — Summary

After the notebook is created:

```
Pipeline complete:

| Stage     | Action                                                        | Status |
|-----------|---------------------------------------------------------------|--------|
| Download  | fetch_nasa_power → rsds → D:/data/malawi/nasa_power_MWI.nc   | ✓ done |
| Process   | mask MWI + monthly sum → summary_ME_MWI_2012.csv             | ✓ done |
| Notebook  | malawi_solar_2012.ipynb — 3 cells (load CSV + bar chart)     | ✓ done |

Open `<OUTPUT_FOLDER>/<notebook>.ipynb` and run all cells — the chart renders inline.
```

---

## Example — the prompt that triggered this skill

**User:** "I want to get and see the accumulated monthly solar radiation in Malawi for 2012"

**Parsed (silently):**
- Country: Malawi → ISO3 `MWI`
- Variable: solar radiation → NASA POWER `ALLSKY_SFC_SW_DWN`
- Period: 2012-01-01 → 2012-12-31
- Agg: **sum** (accumulated)
- Temporal freq: **ME** (monthly)
- Admin level: **0** (no sub-region mentioned)
- Plot type: **seasonal_pattern** (monthly frequency)

**What happens:**
1. Ask for output folder (only missing parameter)
2. Show plan → user confirms
3. **Run Bash Python** → `fetch_nasa_power(...)` → saves `.nc` to disk
4. **Run Bash Python** → mask to MWI, resample monthly sum → saves `.csv`
5. **Create notebook** → 3 cells only: markdown header, load CSV, bar chart
6. Show summary table
