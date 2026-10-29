from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from flowforge.config.risk_limits import (
    LoadedRiskLimits,
    PdtRegime,
    RiskLimitsError,
    load_risk_limits,
)

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"


def _base() -> dict:
    return yaml.safe_load(REPO_CONFIG.read_text())


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "risk_limits.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_repo_config_loads_with_spec_defaults():
    loaded = load_risk_limits(REPO_CONFIG)
    lim = loaded.limits
    assert lim.per_trade.max_risk_pct == 0.5
    assert lim.per_trade.max_position_notional_pct == 33  # operator, 2026-10-29
    assert lim.portfolio.max_concurrent_positions == 3
    assert lim.loss.max_daily_loss_pct == 2
    assert lim.loss.max_weekly_drawdown_pct == 5
    assert lim.portfolio.long_only is True
    assert lim.universe.allow_leveraged_etfs is False
    assert lim.universe.allow_inverse_etfs is False
    assert lim.trading.pdt_regime is PdtRegime.CASH  # operator, 2026-10-26
    assert len(loaded.sha256) == 64


def test_limits_are_immutable():
    lim = load_risk_limits(REPO_CONFIG).limits
    with pytest.raises(ValidationError):
        lim.per_trade.max_risk_pct = 5  # type: ignore[misc]


def test_hash_detects_edit_on_disk(tmp_path):
    path = _write(tmp_path, _base())
    loaded = load_risk_limits(path)
    assert loaded.unchanged_on_disk()
    data = _base()
    data["per_trade"]["max_risk_pct"] = 0.4
    path.write_text(yaml.safe_dump(data))
    assert not loaded.unchanged_on_disk()


def test_hash_check_fails_closed_when_file_removed(tmp_path):
    path = _write(tmp_path, _base())
    loaded = load_risk_limits(path)
    path.unlink()
    assert not loaded.unchanged_on_disk()


def test_missing_file(tmp_path):
    with pytest.raises(RiskLimitsError, match="cannot read"):
        load_risk_limits(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path):
    path = tmp_path / "risk_limits.yaml"
    path.write_text("per_trade: [unclosed")
    with pytest.raises(RiskLimitsError, match="not valid YAML"):
        load_risk_limits(path)


def test_non_mapping(tmp_path):
    with pytest.raises(RiskLimitsError, match="must be a mapping"):
        load_risk_limits(_write(tmp_path, [1, 2]))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("per_trade", "max_risk_pct", 50),  # typo for 0.5 hits the ceiling
        ("per_trade", "max_risk_pct", 0),
        ("per_trade", "max_risk_pct", -1),
        ("per_trade", "max_position_notional_pct", 80),
        ("per_trade", "require_stop", False),
        ("portfolio", "max_gross_exposure_pct", 150),  # leverage
        ("portfolio", "max_concurrent_positions", 0),
        ("loss", "max_daily_loss_pct", 25),
        ("loss", "max_weekly_drawdown_pct", 1),  # tighter than daily
        ("trading", "max_order_submit_rate", "fast"),
        ("trading", "pdt_regime", "none"),
        ("trading", "legacy_pdt_max_day_trades", 4),
        ("universe", "min_price", 0.5),  # penny stocks
        ("session", "max_data_staleness_ms", 0),
        ("kill_switch", "file_flag", ""),
    ],
)
def test_out_of_bounds_values_are_rejected(tmp_path, section, key, value):
    data = _base()
    data[section][key] = value
    with pytest.raises(RiskLimitsError, match="failed validation"):
        load_risk_limits(_write(tmp_path, data))


def test_unknown_keys_are_rejected(tmp_path):
    data = _base()
    data["per_trade"]["max_risk_pc"] = 0.5  # misspelt key must not be silently ignored
    with pytest.raises(RiskLimitsError, match="failed validation"):
        load_risk_limits(_write(tmp_path, data))


def test_missing_section_is_rejected(tmp_path):
    data = _base()
    del data["loss"]
    with pytest.raises(RiskLimitsError, match="failed validation"):
        load_risk_limits(_write(tmp_path, data))


def test_unsupported_version(tmp_path):
    data = _base()
    data["version"] = 2
    with pytest.raises(RiskLimitsError, match="unsupported risk limits version"):
        load_risk_limits(_write(tmp_path, data))


def test_loaded_result_is_frozen(tmp_path):
    loaded = load_risk_limits(_write(tmp_path, _base()))
    assert isinstance(loaded, LoadedRiskLimits)
    with pytest.raises(FrozenInstanceError):
        loaded.sha256 = "x"  # type: ignore[misc]


def test_cash_account_regime_is_accepted(tmp_path):
    data = _base()
    data["trading"]["pdt_regime"] = "cash"
    assert load_risk_limits(_write(tmp_path, data)).limits.trading.pdt_regime is PdtRegime.CASH
