"""FlowForge RiskGuard: deterministic entry checks, loss limits, kill switch and order lifecycle.

Nothing in this package imports Nautilus, learns, or reads anything but
config/risk_limits.yaml and its own state files. The Nautilus strategy in
flowforge.execution is a thin adapter around it (DECISIONS.md D7, ADR-0013).
"""
