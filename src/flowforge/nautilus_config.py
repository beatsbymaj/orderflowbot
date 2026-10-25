"""Builds Nautilus engine configs from FlowForge's risk limits.

The Nautilus RiskEngine is one layer of FlowForge's risk stack (DECISIONS.md D7). Its
bypass flag is always False; CI also checks that no source file enables it.
"""

from __future__ import annotations

from nautilus_trader.live import LiveRiskEngineConfig

from flowforge.config.risk_limits import RiskLimits


def risk_engine_config(limits: RiskLimits) -> LiveRiskEngineConfig:
    return LiveRiskEngineConfig(
        bypass=False,
        max_order_submit_rate=limits.trading.max_order_submit_rate,
        max_order_modify_rate=limits.trading.max_order_submit_rate,
    )
