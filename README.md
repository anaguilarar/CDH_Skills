# GeoAgri Skills — AI Skills for Climate & Agriculture Research

A curated collection of [Claude Code](https://claude.ai/code) skills for geospatial data processing
in agriculture and climate change research. Each skill turns a natural-language request into a
ready-to-run workflow — from downloading raw climate rasters to building spatial datacubes and
extracting agronomic indicators.

---

## What are skills?

Skills are domain-specific instruction sets loaded into Claude Code. When you describe what you need,
Claude reads the relevant skill and acts as a specialist — calling the right APIs, writing correct
code, and following established workflows. You don't need to know the tool names or parameters.

Skills live in `skills/`. Install or update them via Claude Code's skill system.

---

## Skills catalog

| Skill | Domain | What it does |
|-------|--------|--------------|
| [`climate-data-download`](#climate-data-download) | Data ingestion | Downloads CHIRPS, CHIRTS-ERA5, AgERA5, and NASA POWER climate data for any country or bounding box |
| [`geospatial-cube-processor`](#geospatial-cube-processor) | Spatial processing | Clips rasters to admin boundaries, stacks multi-source datasets, and computes zonal statistics |
| [`gcf-pipeline`](#gcf-pipeline) | End-to-end pipeline | Full download → process → visualize workflow in one conversation |

---

## climate-data-download

**Skill path:** `skills/climate-data-download/`

Downloads climate and agrometeorological data from multiple sources. You say what variable and region
you need; the skill routes each variable to the correct source, shows you the plan, and downloads
in sequence.

**Supported sources and variables:**

| Variable | Source | Notes |
|----------|--------|-------|
| Precipitation | CHIRPS v3 | Daily 0.05°, 1981–present |
| Tmax / Tmin | CHIRTS-ERA5 | Daily 0.05°, 1983–present |
| Solar radiation | NASA POWER | S3 Zarr, no API key, 1981–2029 |
| Daily mean RH | NASA POWER | S3 Zarr |
| Hourly RH (06/09/12/15/18 UTC) | AgERA5 | CDS API key required |
| Wind speed, VPD, Ref. ET, dew point | AgERA5 | CDS API key required |

**Powered by:** [`aggeodata`](https://github.com/anaguilarar/aggeodata)

**Example:**

```
User: I need 6 AM relative humidity and reference ET for the Ashanti region of Ghana,
      January–June 2021. Output to D:/data/ashanti

Skill: Here's what I'll download (all via AgERA5):

       | Variable    | Key                           |
       |-------------|-------------------------------|
       | RH 06:00    | relative_humidity_06          |
       | Ref. ET     | reference_evapotranspiration  |

       Region: Ashanti (admin level 1) | Period: 2021-01-01 → 2021-06-30
       CDS API key required — do you have one configured?

       Shall I proceed?
```

**Setup:**

```bash
# Install aggeodata with download + MCP extras
pip install "aggeodata[download,mcp] @ git+https://github.com/anaguilarar/aggeodata.git"

# For AgERA5: create ~/.cdsapirc with your Copernicus CDS key
# Register free at https://cds.climate.copernicus.eu/
```

---

## geospatial-cube-processor

**Skill path:** `skills/geospatial-cube-processor/`

Writes geospatial processing functions directly into your existing Python script or notebook.
Works with any gridded dataset (xarray, lat/lon, EPSG:4326) — climate, soil, elevation, or land use.

**Functions this skill can write:**

| Function | What it does |
|----------|--------------|
| `mask_to_admin` | Clip a raster to a country or admin boundary (GADM v4.1) |
| `stack_datasets` | Merge multi-source datasets onto a common grid (rioxarray reproject_match) |
| `summarize_by_admin` | Zonal statistics per admin unit → CSV (mean, sum, max, min, by month/year) |
| `export_cog` | Write Cloud Optimized GeoTIFF per variable |

**Example:**

```
User: Clip my CHIRPS dataset to Ethiopia at province level and compute
      monthly mean precipitation per province.

Skill: [writes mask_to_admin and summarize_by_admin into your notebook]

# Result:
eth_chirps = mask_to_admin(chirps, "ETH", admin_level=1)
df = summarize_by_admin(eth_chirps, boundaries, agg_method="mean",
                        temporal_freq="ME", output_csv="eth_precip_monthly.csv")
```

---

## gcf-pipeline

**Skill path:** `skills/gcf-pipeline/`

Full end-to-end pipeline orchestrator: **download → spatial processing → visualization** in one
conversation. Use this when you want to go from a country name and date range to a map or time-series
plot without intermediate steps.

**Example:**

```
User: Show me accumulated precipitation for Kenya for the 2021 long rains season
      (March–May) at district level.

Skill: [downloads CHIRPS, clips to Kenya admin level 2, aggregates,
        writes a choropleth map into your notebook]
```

---

## Using skills together

Skills chain naturally across a research workflow:

```
1. climate-data-download  →  fetch precipitation and temperature rasters
2. geospatial-cube-processor  →  clip, stack, compute zonal stats
3. (gcf-pipeline runs all three in one go)
```

---

## MCP server configuration

The `climate-data-download` skill requires the `aggeodata` MCP server. It is already registered
in `.claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "aggeodata": {
      "command": "python",
      "args": ["-m", "aggeodata.mcp_server"],
      "description": "Download CHIRPS, CHIRTS-ERA5, AgERA5, and NASA POWER climate data."
    }
  }
}
```

---

## Contributing a new skill

Each skill is a folder inside `skills/` containing a single `SKILL.md` file (plus optional
`scripts/`, `references/`, and `assets/` subdirectories). To add a skill:

1. Create `skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and
   Markdown instructions.
2. Add an entry to `skills-lock.json`.
3. Open a pull request with a brief description of what the skill does and when it should trigger.

Good candidates for new skills: soil data download (SoilGrids), crop model configuration
(DSSAT, AquaCrop), satellite image retrieval (Sentinel-2, HLS), and climate index computation.
