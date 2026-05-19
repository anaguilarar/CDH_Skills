"""
cdh.config.schemas
==================

Pydantic v2 models for climate data requests.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Per-source variable specs
# ---------------------------------------------------------------------------

class ChirpsConfig(BaseModel):
    """CHIRPS has only one variable: daily precipitation."""
    enabled: bool = True


class ChirtsConfig(BaseModel):
    """CHIRTS temperature variables."""
    variables: Annotated[
        list[Literal["tmax", "tmin"]],
        Field(default=["tmax", "tmin"], description="Temperature variables to download"),
    ]


class AgEra5Config(BaseModel):
    """AgERA5 variable selection.

    Valid keys (map to CDS API variables via the internal AGERA5_VARIABLE_MAP):
      temperature_tmax, temperature_tmin, solar_radiation, wind_speed,
      vapour_pressure, vapour_pressure_defficit, relative_humidity_max,
      relative_humidity_min, relative_humidity_06/09/12/15/18,
      reference_evapotranspiration, dew_point_temperature
    """
    variables: Annotated[
        list[str],
        Field(
            default=["temperature_tmax", "temperature_tmin", "solar_radiation", "wind_speed"],
            description="AgERA5 variable keys (see cdh.ingestion.agera5.AGERA5_VARIABLE_MAP)",
        ),
    ]
    version: Annotated[str, Field(default="2_0", description="AgERA5 dataset version")]


class NasaPowerConfig(BaseModel):
    """NASA POWER variable selection.

    Common parameters (community AG):
      ALLSKY_SFC_SW_DWN  — all-sky surface shortwave downward irradiance (MJ/m²/day)
      T2M                — temperature at 2m (°C)
      T2M_MAX / T2M_MIN  — daily max/min temperature at 2m
      RH2M               — relative humidity at 2m (%)
      WS2M               — wind speed at 2m (m/s)
      WS10M              — wind speed at 10m (m/s)
      PRECTOTCORR        — precipitation corrected (mm/day)
    """
    variables: Annotated[
        list[str],
        Field(
            default=["ALLSKY_SFC_SW_DWN", "T2M_MAX", "T2M_MIN", "RH2M", "WS2M"],
            description="NASA POWER parameter codes (community=AG)",
        ),
    ]
    community: Annotated[str, Field(default="AG", description="NASA POWER community code")]


# ---------------------------------------------------------------------------
# Root request model
# ---------------------------------------------------------------------------

class ClimateRequest(BaseModel):
    """Complete specification for a climate data retrieval job.

    Attributes
    ----------
    country : str
        Full country name or ISO 3166-1 alpha-3 code (e.g. 'Honduras' or 'HND').
    start_date : str
        ISO 8601 start date 'YYYY-MM-DD'.
    end_date : str
        ISO 8601 end date 'YYYY-MM-DD'.
    adm_level : int
        Administrative level to clip to (0 = full country).
    feature_name : str | None
        Specific admin unit name (must match GeoBoundaries data). Required when
        adm_level > 0.
    output_folder : str | None
        Root folder for downloaded raw files and cached NetCDF cubes.
        None → system temp directory.
    ncores : int
        Parallel download workers.
    chirps : ChirpsConfig | None
        CHIRPS download settings. None → skip CHIRPS.
    chirts : ChirtsConfig | None
        CHIRTS download settings. None → skip CHIRTS.
    agera5 : AgEra5Config | None
        AgERA5 download settings. None → skip AgERA5.
    nasa_power : NasaPowerConfig | None
        NASA POWER download settings. None → skip NASA POWER.
    """

    country: Annotated[str, Field(description="Country name or ISO3 code")]
    start_date: Annotated[str, Field(description="Start date YYYY-MM-DD")]
    end_date: Annotated[str, Field(description="End date YYYY-MM-DD")]
    adm_level: Annotated[int, Field(default=0, ge=0, le=3)]
    feature_name: Annotated[str | None, Field(default=None)]
    output_folder: Annotated[str | None, Field(default=None)]
    ncores: Annotated[int, Field(default=4, ge=1)]

    chirps: ChirpsConfig | None = None
    chirts: ChirtsConfig | None = None
    agera5: AgEra5Config | None = None
    nasa_power: NasaPowerConfig | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        from datetime import datetime
        datetime.strptime(v, "%Y-%m-%d")
        return v

    @model_validator(mode="after")
    def end_after_start(self) -> ClimateRequest:
        from datetime import datetime
        s = datetime.strptime(self.start_date, "%Y-%m-%d")
        e = datetime.strptime(self.end_date, "%Y-%m-%d")
        if e <= s:
            raise ValueError("end_date must be after start_date")
        return self

    @model_validator(mode="after")
    def feature_requires_adm_level(self) -> ClimateRequest:
        if self.feature_name and self.adm_level == 0:
            raise ValueError(
                "feature_name requires adm_level >= 1. "
                "Set adm_level=1 or 2 to select a specific admin unit."
            )
        return self
