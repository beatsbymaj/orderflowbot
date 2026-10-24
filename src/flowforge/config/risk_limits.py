"""Loader for config/risk_limits.yaml.

The limits are parsed once into frozen models, validated against hard ceilings, and
hashed. Nothing in the running system can modify them; changing a limit means editing
the YAML file and committing it. The pre-market checklist compares the hash taken at
startup with the file on disk so an edit mid-session is detected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SUPPORTED_VERSION = 1

# Ceilings no config value may exceed. They exist to catch typos such as 50 instead of
# 0.5, not to express preferences. Raising one is a code change with review.
CEILING_RISK_PER_TRADE_PCT = 2.0
CEILING_POSITION_NOTIONAL_PCT = 50.0
CEILING_GROSS_EXPOSURE_PCT = 100.0
CEILING_DAILY_LOSS_PCT = 10.0
CEILING_WEEKLY_DRAWDOWN_PCT = 20.0


class RiskLimitsError(ValueError):
    """Raised when the risk limits file is missing, malformed, or out of bounds."""


class PdtRegime(StrEnum):
    LEGACY_PDT = "legacy_pdt"
    INTRADAY_MARGIN = "intraday_margin"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PerTradeLimits(_Frozen):
    max_risk_pct: float = Field(gt=0, le=CEILING_RISK_PER_TRADE_PCT)
    max_position_notional_pct: float = Field(gt=0, le=CEILING_POSITION_NOTIONAL_PCT)
    require_stop: bool
    max_slippage_bps: float = Field(ge=0, le=500)
    max_price_deviation_bps: float = Field(gt=0, le=1000)
    max_pct_of_recent_volume: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _stop_is_mandatory(self) -> PerTradeLimits:
        # Sizing is computed from stop distance, so an entry without a stop can't be sized.
        if not self.require_stop:
            raise ValueError("require_stop must be true: position size is derived from the stop")
        return self


class PortfolioLimits(_Frozen):
    max_concurrent_positions: int = Field(ge=1, le=20)
    max_gross_exposure_pct: float = Field(gt=0, le=CEILING_GROSS_EXPOSURE_PCT)
    long_only: bool


class LossLimits(_Frozen):
    max_daily_loss_pct: float = Field(gt=0, le=CEILING_DAILY_LOSS_PCT)
    max_weekly_drawdown_pct: float = Field(gt=0, le=CEILING_WEEKLY_DRAWDOWN_PCT)

    @model_validator(mode="after")
    def _weekly_not_tighter_than_daily(self) -> LossLimits:
        if self.max_weekly_drawdown_pct < self.max_daily_loss_pct:
            raise ValueError("max_weekly_drawdown_pct must be >= max_daily_loss_pct")
        return self


class TradingLimits(_Frozen):
    max_trades_per_day: int = Field(ge=1, le=200)
    max_order_submit_rate: str = Field(pattern=r"^\d+/\d{2}:\d{2}:\d{2}$")
    pdt_regime: PdtRegime
    legacy_pdt_max_day_trades: int = Field(ge=0, le=3)


class UniverseLimits(_Frozen):
    min_price: float = Field(ge=1.0)
    min_avg_daily_dollar_volume: float = Field(gt=0)
    max_spread_bps: float = Field(gt=0, le=200)
    allow_leveraged_etfs: bool
    allow_inverse_etfs: bool


class SessionLimits(_Frozen):
    regular_hours_only: bool
    no_entry_first_minutes: int = Field(ge=0, le=120)
    no_entry_last_minutes: int = Field(ge=0, le=120)
    flatten_at_close: bool
    max_data_staleness_ms: int = Field(gt=0, le=60_000)


class KillSwitchConfig(_Frozen):
    file_flag: str = Field(min_length=1)
    flatten_on_kill: bool


class RiskLimits(_Frozen):
    version: int
    per_trade: PerTradeLimits
    portfolio: PortfolioLimits
    loss: LossLimits
    trading: TradingLimits
    universe: UniverseLimits
    session: SessionLimits
    kill_switch: KillSwitchConfig

    @model_validator(mode="after")
    def _supported_version(self) -> RiskLimits:
        if self.version != SUPPORTED_VERSION:
            raise ValueError(f"unsupported risk limits version {self.version}")
        return self


@dataclass(frozen=True)
class LoadedRiskLimits:
    limits: RiskLimits
    path: Path
    sha256: str

    def unchanged_on_disk(self) -> bool:
        """True if the file still hashes to the value taken at load time."""
        try:
            return _sha256(self.path.read_bytes()) == self.sha256
        except OSError:
            return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_risk_limits(path: str | Path) -> LoadedRiskLimits:
    """Read, validate and hash the risk limits file. Raises RiskLimitsError on any problem."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RiskLimitsError(f"cannot read risk limits at {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RiskLimitsError(f"risk limits at {path} are not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RiskLimitsError(f"risk limits at {path} must be a mapping")

    try:
        limits = RiskLimits.model_validate(data)
    except ValidationError as exc:
        raise RiskLimitsError(f"risk limits at {path} failed validation:\n{exc}") from exc

    return LoadedRiskLimits(limits=limits, path=path, sha256=_sha256(raw))
