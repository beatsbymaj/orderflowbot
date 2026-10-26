"""Check the fast simulator against a full Nautilus backtest (docs/research/14).

For one stock-day and one setup/configuration, the real ticks go through a Nautilus
BacktestEngine with the FeatureActor and OrderFlowStrategy, so every entry passes RiskGuard
and the Phase 4 order handling (IOC entry, broker stop, target in code, end-of-day flatten).
The trades it makes are matched to the fast simulator's by signal id and compared: entry
and exit time, price and reason. Share counts differ on purpose (the fast simulator's
"edge" measure has no notional cap), so R per share is compared, not dollars.

    python -m flowforge.research.crosscheck --symbol SPY --day 2026-10-21 --setup H4 --config zone/volume
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from flowforge.research.fastsim import Trade, simulate
from flowforge.research.quotes import QuoteSource
from flowforge.signals.setups import Signal

PRICE_TOLERANCE = 0.02  # $: fills one quote apart
TIME_TOLERANCE_S = 2.0


def nautilus_trades(journal_events: list[dict]) -> dict[str, dict]:
    """Trades by intent from GuardedStrategy journal events."""
    role: dict[str, tuple[str, str]] = {}
    reasons: dict[str, str] = {}
    out: dict[str, dict] = {}
    for e in journal_events:
        kind = e["event"]
        if kind == "order_submit":
            role[e["order"]] = (e["intent"], "entry")
        elif kind == "stop_submit":
            role[e["order"]] = (e["intent"], "stop")
        elif kind == "exit_submit":
            role[e["order"]] = (e["intent"], "exit")
            reasons[e["order"]] = e.get("reason", "")
        elif kind == "fill" and e["order"] in role:
            intent, r = role[e["order"]]
            t = out.setdefault(
                intent, {"intent": intent, "buy_qty": 0, "buy_px": 0.0, "sell_qty": 0, "sell_px": 0.0}
            )
            q, px = e["quantity"], e["price"]
            if r == "entry":
                t["buy_px"] = (t["buy_px"] * t["buy_qty"] + px * q) / (t["buy_qty"] + q)
                t["buy_qty"] += q
                t["entry_ts"] = e["ts_ns"]
            else:
                t["sell_px"] = (t["sell_px"] * t["sell_qty"] + px * q) / (t["sell_qty"] + q)
                t["sell_qty"] += q
                t["exit_ts"] = e["ts_ns"]
                why = reasons.get(e["order"], "")
                t["exit_reason"] = (
                    "stop"
                    if r == "stop"
                    else "target"
                    if why.startswith("target")
                    else "eod"
                    if "end of day" in why
                    else why
                )
    return out


def compare(fast: list[Trade], naut: dict[str, dict]) -> dict:
    rows = []
    for t in fast:
        n = naut.get(f"{t.setup}:{t.symbol}:{t.signal_ts}")
        if n is None:
            rows.append(
                {
                    "intent": f"{t.setup}:{t.symbol}:{t.signal_ts}",
                    "match": False,
                    "why": "not traded in Nautilus",
                }
            )
            continue
        checks = {
            "entry_price": abs(n["buy_px"] - t.entry) <= PRICE_TOLERANCE,
            "exit_price": n.get("exit_ts") is not None and abs(n["sell_px"] - t.exit) <= PRICE_TOLERANCE,
            "exit_reason": n.get("exit_reason") == t.exit_reason,
            "exit_time": n.get("exit_ts") is not None
            and abs(n["exit_ts"] - t.exit_ts) / 1e9 <= TIME_TOLERANCE_S,
        }
        rows.append(
            {
                "intent": n["intent"],
                "match": all(checks.values()),
                "checks": checks,
                "fast": {"entry": t.entry, "exit": t.exit, "reason": t.exit_reason},
                "nautilus": {
                    "entry": round(n["buy_px"], 4),
                    "exit": round(n["sell_px"], 4),
                    "reason": n.get("exit_reason"),
                },
            }
        )
    extra = sorted(set(naut) - {f"{t.setup}:{t.symbol}:{t.signal_ts}" for t in fast})
    return {
        "fast_trades": len(fast),
        "nautilus_trades": len(naut),
        "matched": sum(r["match"] for r in rows),
        "only_in_nautilus": extra,
        "rows": rows,
    }


def fast_trades(signals: list[Signal], setup: str, cfg: tuple[str, str], quotes: QuoteSource) -> list[Trade]:
    out, busy = [], 0
    for sig in sorted(s for s in signals if s.setup == setup):
        if sig.ts < busy:
            continue
        res = simulate(sig, cfg[0], cfg[1], quotes)
        if isinstance(res, Trade):
            out.append(res)
            busy = res.exit_ts
    return out


def run_nautilus(
    ticks: Path, symbol: str, day: date, setups: dict, limits, workdir: Path
) -> tuple[list[dict], list[dict]]:
    """Returns (journal events, feature records)."""
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig, LogLevel
    from nautilus_trader.model import AccountType, Money, OmsType, Venue
    from nautilus_trader.persistence import ParquetDataCatalog
    from nautilus_trader.risk import RiskEngineConfig

    from flowforge.backtest.fees import IbkrFixedFeeModel
    from flowforge.data.ticks import VENUE, make_tick_equity
    from flowforge.execution.journal import Journal
    from flowforge.execution.models import fill_model, latency_model
    from flowforge.features.actor import FeatureActor
    from flowforge.features.config import FeatureConfig
    from flowforge.features.replay import _seed, iter_ticks, load_day, prior_day
    from flowforge.risk.guard import InstrumentInfo, RiskGuard
    from flowforge.risk.killswitch import KillSwitch
    from flowforge.risk.state import RiskState
    from flowforge.strategies.orderflow import OrderFlowStrategy

    config = FeatureConfig()
    d = load_day(ticks, symbol, day)
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(stdout_level=LogLevel.ERROR),
            risk_engine=RiskEngineConfig(
                bypass=False,
                max_order_submit_rate=limits.trading.max_order_submit_rate,
                max_order_modify_rate=limits.trading.max_order_submit_rate,
            ),
        )
    )
    engine.add_venue(
        Venue(VENUE),
        OmsType.NETTING,
        AccountType.CASH,
        [Money.from_str("10000 USD")],
        fill_model=fill_model(prob_slippage=0.0),
        fee_model=IbkrFixedFeeModel(),
        latency_model=latency_model(150),
    )
    engine.add_instrument(make_tick_equity(symbol))
    prior = prior_day(ticks, symbol, day, config)
    adv = {symbol: prior.close * prior.volume if prior else 0.0}
    guard = RiskGuard(limits, RiskState(limits, workdir / "risk_state.json"), KillSwitch(workdir / "KILL"))
    journal = Journal(workdir / "journal", keep=True)
    strat = OrderFlowStrategy(
        [symbol], guard, {symbol: InstrumentInfo(symbol)}, journal, setups=setups, avg_daily_dollar_volume=adv
    )
    records: list[dict] = []

    def on_record(rec: dict) -> None:
        records.append(rec)
        strat.on_feature(rec)

    actor = FeatureActor([symbol], config, conditions=d.conditions, on_record=on_record)
    _seed(actor.engine, ticks, d, config)
    engine.add_actor(actor)
    engine.add_strategy(strat)
    catalog = ParquetDataCatalog(str(ticks / "catalog"))
    for quotes, trades in iter_ticks(catalog, d):
        if quotes or trades:
            engine.add_data(quotes + trades)
            engine.run(streaming=True)
            engine.clear_data()
    engine.end()
    engine.dispose()
    events = [
        e for name in ("order_submit", "stop_submit", "exit_submit", "fill") for e in journal.events(name)
    ]
    events.sort(key=lambda e: e["ts_ns"])
    return events, records


def main(argv: list[str] | None = None) -> int:
    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.research.quotes import CatalogQuotes
    from flowforge.research.walkforward import stock_day
    from flowforge.strategies.orderflow import setups_from_text

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticks", type=Path, default=Path("data/ticks"))
    p.add_argument("--out", type=Path, default=Path("reports/phase5"))
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--day", type=date.fromisoformat, required=True)
    p.add_argument("--setup", default="H4")
    p.add_argument("--config", default="zone/volume")
    p.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    a = p.parse_args(argv)
    limits = load_risk_limits(a.risk_limits).limits
    setups = setups_from_text(f"{a.setup}:{a.config}")
    with tempfile.TemporaryDirectory() as tmp:
        events, records = run_nautilus(a.ticks, a.symbol, a.day, setups, limits, Path(tmp))
    sd = stock_day(a.symbol, a.day.isoformat(), records)
    fast = fast_trades(sd.signals, a.setup, setups[a.setup], CatalogQuotes(a.ticks))
    result = compare(fast, nautilus_trades(events))
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f"crosscheck_{a.symbol}_{a.day}_{a.setup}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"{a.symbol} {a.day} {a.setup}: fast {result['fast_trades']}, Nautilus {result['nautilus_trades']},"
        f" matched {result['matched']}. -> {path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
