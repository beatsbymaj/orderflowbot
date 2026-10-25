# Phase 1 demo note: foundations

What exists:
- `config/risk_limits.yaml` with the spec defaults (Claude-picked values are marked), loaded by `src/flowforge/config/risk_limits.py` into frozen, validated models with hard ceilings and a SHA-256 hash for the pre-market "config unchanged" check. 100% test coverage.
- Nautilus 2.0.0rc7 pinned on Python 3.13; the Nautilus RiskEngine config is built from the limits with `bypass=False`, and CI fails if any source file sets bypass to True.
- `python -m flowforge.ops.ib_paper_check`: starts a Nautilus node with only the IB execution client and no strategies, reconciles against the IBKR paper account, and reports balances, open positions and open orders with the account id masked. It refuses to connect unless the mode is paper, the port is a paper port and the account id starts with DU.
- Docker Compose: IB Gateway 10.50.1f (paper, read-only API, localhost-only ports), Redis 8.6.7, TimescaleDB 2.30.2-pg17, and the check as an on-demand profile.
- 11 subagent definitions in `.claude/agents/`, gitleaks + ruff pre-commit hooks, and CI (gitleaks, lint, bypass guard, tests with coverage gates).

Not done yet (needs the operator):
- Running the paper check against a real IBKR paper account (needs an IBKR Pro account, paper credentials in `.env`, and the Monday 2FA login over VNC).
- The weekly re-auth drill.

How to run it: see "Commands" in CLAUDE.md.
