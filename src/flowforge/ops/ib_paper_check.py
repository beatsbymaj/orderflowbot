"""Read-only connection check against an IBKR paper account.

Starts a Nautilus node with only the IB execution client and no strategies, lets it
connect and reconcile, then reports the account balances, open positions and open
orders it found. Nothing in this node can submit an order: there is no strategy, the
gateway should run with READ_ONLY_API=yes, and the guards below refuse anything that
doesn't look like a paper account.

Usage:
    python -m flowforge.ops.ib_paper_check --seconds 60

Connection settings come from the environment (see .env.example): IB_HOST, IB_PORT,
IB_CLIENT_ID, IB_ACCOUNT_ID, IB_TRADING_MODE.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Paper API ports: IB Gateway 4002, TWS 7497, and 4004 (the gnzsnz container relay for paper).
PAPER_PORTS = frozenset({4002, 4004, 7497})
# IBKR paper account ids start with "DU" (live individual accounts start with "U").
PAPER_ACCOUNT_PREFIX = "DU"
IB_VENUE = "IB"


class NotPaperError(RuntimeError):
    """Raised when settings don't clearly point at a paper account."""


@dataclass(frozen=True)
class IbSettings:
    host: str
    port: int
    client_id: int
    account_id: str
    trading_mode: str


@dataclass
class CheckReport:
    started_at: str
    account: str
    connected: bool = False
    balances: list[dict[str, str]] = field(default_factory=list)
    open_positions: list[dict[str, str]] = field(default_factory=list)
    open_orders: list[dict[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.connected and not self.problems


def mask_account(account_id: str) -> str:
    """Keep only the prefix and last two characters, so reports never carry the full id."""
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return account_id[:2] + "*" * (len(account_id) - 4) + account_id[-2:]


def settings_from_env(env: dict[str, str] | os._Environ[str] = os.environ) -> IbSettings:
    missing = [k for k in ("IB_ACCOUNT_ID",) if not env.get(k)]
    if missing:
        raise NotPaperError(f"missing environment variables: {', '.join(missing)}")
    return IbSettings(
        host=env.get("IB_HOST", "127.0.0.1"),
        port=int(env.get("IB_PORT", "4002")),
        client_id=int(env.get("IB_CLIENT_ID", "91")),
        account_id=env["IB_ACCOUNT_ID"],
        trading_mode=env.get("IB_TRADING_MODE", "paper").lower(),
    )


def assert_paper(settings: IbSettings) -> None:
    """Fail closed unless every signal says this is a paper account."""
    problems = []
    if settings.trading_mode != "paper":
        problems.append(f"IB_TRADING_MODE is {settings.trading_mode!r}, expected 'paper'")
    if settings.port not in PAPER_PORTS:
        problems.append(f"IB_PORT {settings.port} is not a paper port {sorted(PAPER_PORTS)}")
    if not settings.account_id.upper().startswith(PAPER_ACCOUNT_PREFIX):
        problems.append(
            f"account {mask_account(settings.account_id)} does not start with "
            f"{PAPER_ACCOUNT_PREFIX!r} (paper accounts do)"
        )
    if problems:
        raise NotPaperError("refusing to connect: " + "; ".join(problems))


def summarize_cache(cache: Any, report: CheckReport) -> None:
    """Fill the report from a Nautilus cache. Only reads; never mutates the cache."""
    from nautilus_trader.model import Venue

    account = cache.account_for_venue(Venue(IB_VENUE))
    if account is None:
        report.problems.append("no IB account state received")
    else:
        report.connected = True
        free = account.balances_free()
        for currency, total in account.balances_total().items():
            report.balances.append(
                {"currency": str(currency), "total": str(total), "free": str(free.get(currency, ""))}
            )

    for position in cache.positions_open():
        report.open_positions.append(
            {
                "instrument": str(position.instrument_id),
                "side": str(position.side),
                "quantity": str(position.quantity),
            }
        )
    for order in cache.orders_open():
        report.open_orders.append(
            {"instrument": str(order.instrument_id), "side": str(order.side), "quantity": str(order.quantity)}
        )


def build_node(settings: IbSettings, risk_limits_path: Path) -> Any:
    from nautilus_trader.adapters.interactive_brokers import (
        InteractiveBrokersExecutionClientConfig,
        InteractiveBrokersExecutionClientFactory,
    )
    from nautilus_trader.common import Environment
    from nautilus_trader.live import LiveNode
    from nautilus_trader.model import TraderId

    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.nautilus_config import risk_engine_config

    limits = load_risk_limits(risk_limits_path).limits
    exec_config = InteractiveBrokersExecutionClientConfig(
        host=settings.host,
        port=settings.port,
        client_id=settings.client_id,
        account_id=settings.account_id,
    )
    return (
        LiveNode.builder("ib-paper-check", TraderId("FLOWFORGE-CHECK"), Environment.LIVE)
        .with_risk_engine_config(risk_engine_config(limits))
        .with_reconciliation(True)
        .add_exec_client(IB_VENUE, InteractiveBrokersExecutionClientFactory(), exec_config)
        .build()
    )


async def _run_for(node: Any, seconds: float) -> None:
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(seconds)
    finally:
        node.handle().stop()
        await task


def run_check(settings: IbSettings, seconds: float, risk_limits_path: Path) -> CheckReport:
    assert_paper(settings)
    report = CheckReport(started_at=datetime.now(UTC).isoformat(), account=mask_account(settings.account_id))
    node = build_node(settings, risk_limits_path)
    try:
        asyncio.run(_run_for(node, seconds))
        summarize_cache(node.cache, report)
    except Exception as exc:  # report every failure instead of a traceback
        report.problems.append(f"{type(exc).__name__}: {exc}")
    finally:
        node.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to stay connected")
    parser.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)

    try:
        settings = settings_from_env()
        report = run_check(settings, args.seconds, args.risk_limits)
    except NotPaperError as exc:
        print(f"ib_paper_check: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / f"ib_paper_check_{report.started_at[:19].replace(':', '')}.json"
    out_file.write_text(json.dumps(asdict(report) | {"ok": report.ok}, indent=2))
    print(json.dumps(asdict(report) | {"ok": report.ok}, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
