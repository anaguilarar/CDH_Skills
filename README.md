# CDH Skills — Climate Data Hub AI Assistant Skills

A collection of [Claude Code](https://claude.ai/code) skills that turn natural-language requests into
climate data workflows — from downloading raw rasters to masking, stacking, aggregating, and plotting.

- **Author:** Andres Aguilar (andres.aguilar@cgiar.org)
- **License:** MIT
- **Python:** ≥ 3.10

---

## What are skills?

Skills are prompt-driven AI specialists loaded into Claude Code. Each skill knows a specific domain
deeply and writes correct, ready-to-run code directly into your notebook or script. You activate a
skill by describing what you want — no slash commands needed.

---

## Skills in this repository

| Skill | Trigger phrases | What it produces |
|---|---|---|
| `climate-data-download` | "download precipitation for Ethiopia", "get solar radiation data" | Orchestrated MCP calls that fetch CHIRPS, CHIRTS-ERA5, AgERA5, or NASA POWER data |
| `geospatial-cube-processor` | "clip to Kenya", "combine my rasters", "average rainfall per district" | Python functions for masking, stacking, and zonal statistics written into your file |
| `notebook-plots` | "plot", "visualize", "show me", "map this", "compare regions" | Interactive Plotly/hvplot functions written into your Jupyter notebook |

---

## Skill: `climate-data-download`

Routes each climate variable to the correct data source and downloads it via the `cdh` MCP server.

### Variable → source routing

| Variable | Source | Notes |
|---|---|---|
| Precipitation | CHIRPS | Daily, 0.05° |
| Max/Min temperature | CHIRTS-ERA5 | Daily |
| Solar radiation | NASA POWER (`ALLSKY_SFC_SW_DWN`) | S3 Zarr, 1981–2029 |
| Relative humidity | NASA POWER (`RH2M`) | S3 Zarr, 1981–2029 |
| Wind speed, VPD, Ref. ET | AgERA5 | CDS API, one request per year |

### Example conversation

```
User: I need daily precipitation and maximum temperature for Honduras
      from 2010 to 2022. Save to D:/climate_data

Skill: Here's the plan:
       • precipitation  → CHIRPS         (workers: 2)
       • tmax           → CHIRTS-ERA5    (workers: 2)

       Proceed? (yes/no)

User: yes

Skill: [calls cdh:download_chirps and cdh:download_chirts in sequence]
       Done. Files saved to D:/climate_data/
```

### Example — multi-variable request including solar radiation

```
User: Download precipitation, Tmin, Tmax, and solar radiation for Ethiopia 2015–2020.
      Output folder: D:/eth_climate

Skill: Routing plan:
       • precipitation  → CHIRPS
       • tmin, tmax     → CHIRTS-ERA5
       • solar_rad      → NASA POWER (ALLSKY_SFC_SW_DWN)

       Workers capped at 2 for CHIRPS/CHIRTS (HTTP rate limit).
       NASA POWER uses S3 Zarr — no rate limits.

       Shall I proceed?
```

### Setup — MCP server

```bash
# Install the cdh package with MCP support
pip install -e ".[full]"

# For AgERA5 (CDS API): create ~/.cdsapirc with your key
# https://cds.climate.copernicus.eu/api-how-to
```

The MCP configuration is already in `.claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "cdh": {
      "command": "python",
      "args": ["-m", "cdh.mcp_server"]
    }
  }
}
```

---

## Skill: `geospatial-cube-processor`

Writes three standalone helper functions directly into your existing Python script or notebook.
Call only the ones you need — they are independent of each other.

### Functions written by this skill

#### `mask_to_admin` — clip raster to an admin boundary

```python
def mask_to_admin(ds, country_iso3, admin_level=0, shapefile_path=None):
    """
    Clip an xarray Dataset to an administrative boundary from GADM v4.1.

    Parameters
    ----------
    ds : xr.Dataset
        Input dataset with lat/lon dimensions and CRS EPSG:4326.
    country_iso3 : str
        ISO-3166 alpha-3 country code (e.g. "ETH", "HND").
    admin_level : int
        0 = country, 1 = province/state, 2 = district.
    shapefile_path : str, optional
        Local shapefile; fetches from GADM JSON if None.

    Returns
    -------
    xr.Dataset  clipped to boundary, with STAC-compliant attributes.
    """
```

**Example usage:**

```python
import xarray as xr
from cdh import fetch_chirps

# Download CHIRPS for a wide bounding box
chirps = fetch_chirps("Ethiopia", "2015-01-01", "2020-12-31")

# Tell the skill: "clip my CHIRPS data to Ethiopia at admin level 1"
# The skill writes mask_to_admin into your notebook, then:
eth_chirps = mask_to_admin(chirps, "ETH", admin_level=1)
```

---

#### `stack_datasets` — merge rasters from different sources

```python
def stack_datasets(datasets, target_resolution="coarsest", resampling_method="bilinear"):
    """
    Reproject and merge multiple xarray Datasets to a common grid.

    Parameters
    ----------
    datasets : list[xr.Dataset]
        Datasets from different sources (CHIRPS, CHIRTS, NASA POWER, AgERA5).
    target_resolution : str or float
        "coarsest", "finest", or a resolution in degrees (e.g. 0.05).
    resampling_method : str
        rioxarray resampling method: "bilinear", "nearest", etc.

    Returns
    -------
    xr.Dataset  merged cube with STAC-compliant attributes.
    """
```

**Example usage:**

```python
from cdh import fetch_chirps, fetch_chirts, fetch_nasa_power

chirps  = fetch_chirps("Honduras",  "2018-01-01", "2022-12-31")
chirts  = fetch_chirts("Honduras",  "2018-01-01", "2022-12-31", variables=["tmax", "tmin"])
power   = fetch_nasa_power("Honduras", "2018-01-01", "2022-12-31", variables=["ALLSKY_SFC_SW_DWN"])

# Tell the skill: "stack these three datasets into a single cube"
# The skill writes stack_datasets, then:
cube = stack_datasets([chirps, chirts, power], target_resolution="coarsest")
# cube now has precipitation, tmax, tmin, solar_rad on a common grid
```

---

#### `summarize_by_admin` — zonal statistics per admin unit

```python
def summarize_by_admin(ds, boundaries, agg_method="mean", temporal_freq=None, output_csv=None):
    """
    Compute per-admin-unit statistics from a gridded Dataset.

    Parameters
    ----------
    ds : xr.Dataset
        Clipped dataset (output of mask_to_admin or stack_datasets).
    boundaries : gpd.GeoDataFrame
        Admin polygons with a 'shapeName' column.
    agg_method : str
        "mean", "sum", "max", or "min".
    temporal_freq : str, optional
        Resample time before aggregating: "ME" (month), "QE" (quarter), "YE" (year).
    output_csv : str, optional
        Save result to this path if provided.

    Returns
    -------
    pd.DataFrame  with columns: admin_unit, variable, time, value.
    """
```

**Example usage:**

```python
import geopandas as gpd

# Load admin boundaries (or let mask_to_admin return them)
boundaries = gpd.read_file("eth_admin1.shp")

# Tell the skill: "compute monthly mean precipitation per district"
df = summarize_by_admin(
    eth_chirps,
    boundaries,
    agg_method="mean",
    temporal_freq="ME",
    output_csv="eth_precip_monthly.csv"
)
#    admin_unit        variable       time       value
# 0  Oromia            precip    2015-01-31    45.2
# 1  Amhara            precip    2015-01-31    38.7
```

---

## Skill: `notebook-plots`

Writes interactive Plotly visualization functions into your Jupyter notebook or Google Colab.
Uses hvplot for spatial raster maps when available, with a Plotly fallback for Colab.

### Functions written by this skill

#### `plot_time_series` — trend lines by admin unit

```python
# Tell the skill: "plot monthly precipitation over time for each region"
plot_time_series(df, variable="precip", title="Monthly Precipitation — Ethiopia 2015–2020")
```

Input: DataFrame from `summarize_by_admin` with columns `admin_unit`, `variable`, `time`, `value`.
Output: One Plotly line per admin unit; one subplot per variable if multiple variables are present.

---

#### `plot_spatial_map` — interactive raster map

```python
# Tell the skill: "show me a spatial map of average temperature"
plot_spatial_map(ds, variable="tmax", time=None, title="Mean Daily Tmax — Honduras 2018–2022")
```

Input: xarray Dataset from `mask_to_admin` or `stack_datasets`.
Output: Interactive quadmesh (hvplot) or heatmap fallback — auto-detects coordinate names.

---

#### `plot_admin_comparison` — sorted bar chart across regions

```python
# Tell the skill: "compare average annual rainfall across districts"
plot_admin_comparison(df, variable="precip", year=2020, agg="mean",
                      title="Mean Annual Precipitation by District (2020)")
```

Output: Horizontal bar chart sorted by value, colored by admin unit.

---

#### `plot_seasonal_pattern` — monthly climatology

```python
# Tell the skill: "show the seasonal pattern of rainfall and temperature"
plot_seasonal_pattern(df, precip_var="precip", temp_var="tmax",
                      admin_unit="Oromia", title="Seasonal Climatology — Oromia")
```

Output: Precipitation bars + temperature line on a dual-axis chart.

---

#### `plot_dashboard` — grid of spatial maps per variable

```python
# Tell the skill: "show me all variables as a dashboard of spatial maps"
plot_dashboard(ds, time=None, ncols=2, title="Climate Variables — Ethiopia 2015–2020")
```

Output: Grid of heatmaps, one per variable in the Dataset.

---

### End-to-end example — download, process, and plot

The three skills chain together naturally:

```
User: Download monthly precipitation and Tmax for Kenya 2010–2022.
      Then clip to admin level 1, compute monthly means per county,
      and plot time series and a seasonal pattern.
```

**Step 1 — climate-data-download skill fetches the data:**

```python
from cdh import fetch_chirps, fetch_chirts

chirps = fetch_chirps("Kenya", "2010-01-01", "2022-12-31")
chirts = fetch_chirts("Kenya", "2010-01-01", "2022-12-31", variables=["tmax"])
```

**Step 2 — geospatial-cube-processor skill masks, stacks, and summarizes:**

```python
cube       = stack_datasets([chirps, chirts], target_resolution="coarsest")
ken_cube   = mask_to_admin(cube, "KEN", admin_level=1)

boundaries = gpd.read_file(...)   # returned by mask_to_admin
df         = summarize_by_admin(ken_cube, boundaries, agg_method="mean",
                                temporal_freq="ME", output_csv="kenya_monthly.csv")
```

**Step 3 — notebook-plots skill visualizes:**

```python
plot_time_series(df, variable="precip",
                 title="Monthly Precipitation by County — Kenya 2010–2022")

plot_seasonal_pattern(df, precip_var="precip", temp_var="tmax",
                      title="Seasonal Climatology — Kenya")

plot_dashboard(ken_cube, ncols=2, title="Kenya Climate Cube")
```

---

## Installation

```bash
# Core package
pip install -e .

# With AgERA5 support (requires ~/.cdsapirc)
pip install -e ".[agera5]"

# With MCP server (required for climate-data-download skill)
pip install -e ".[mcp]"

# Everything
pip install -e ".[full]"
```

---

## Python package overview

The `cdh` package is the backend that all three skills use. You can also call it directly without
the skills.

| Module | Description |
|---|---|
| `cdh._api` | High-level fetch functions: `fetch_chirps`, `fetch_chirts`, `fetch_agera5`, `fetch_nasa_power` |
| `cdh.mcp_server` | MCP server exposing download tools to Claude Code |
| `cdh.conventions` | CF-compliant variable name and `long_name` mappings |
| `cdh.ingestion` | Per-source handlers for CHIRPS, CHIRTS, AgERA5, NASA POWER, and boundaries |
| `cdh.spatial` | Raster clip, mask, reproject, rescale (rioxarray) |
| `cdh.transform` | Multi-source xarray cube building and COG export |
| `cdh.summarize` | Temporal (monthly/seasonal) and spatial (zonal) aggregation |

```python
# Direct API usage (no skills needed)
from cdh import fetch_chirps, fetch_nasa_power
from cdh.summarize import aggregate_by_admin, monthly_climatology

chirps  = fetch_chirps("Honduras", "2015-01-01", "2020-12-31")
monthly = monthly_climatology(chirps)
```
