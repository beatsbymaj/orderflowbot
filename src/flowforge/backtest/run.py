"""Run the opening range breakout backtest on downloaded 1-minute bars.

Offline only: Nautilus BacktestEngine with a simulated venue. No broker connection.

Usage:
    python -m flowforge.backtest.run --data data/bars --equity 10000

Writes summary.json, trades.csv, journal.jsonl and report.html to reports/backtest-<time>/.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.execution import OneTickSlippageFillModel
from nautilus_trader.model import AccountType, Currency, Money, OmsType, Venue
from nautilus_trader.risk import RiskEngineConfig

from flowforge.backtest import report
from flowforge.backtest.bars import (
    VENUE,
    MinuteBar,
    make_equity,
    read_bars_csv,
    regular_hours,
    session_closes,
    to_nautilus,
)
from flowforge.backtest.fees import IbkrFixedFeeModel
from flowforge.backtest.limits import LimitBook
from flowforge.backtest.orb import OpeningRangeBreakout, OrbParams, OrbRecorder
from flowforge.backtest.stats import daily_equity, summarize
from flowforge.config.risk_limits import LoadedRiskLimits, load_risk_limits
from flowforge.data.alpaca_bars import bars_csv_path

DEFAULT_SYMBOLS = "SPY,QQQ,AAPL,MSFT,NVDA"


def benchmark(bars: list[MinuteBar], symbol: str, equity: float) -> dict | None:
    """Buy whole shares at the first open, sell at the last close (fees ignored)."""
    own = [b for b in bars if b.symbol == symbol]
    if not own:
        return None
    shares = int(equity // own[0].open)
    pnl = shares * (own[-1].close - own[0].open)
    return {"symbol": symbol, "pnl": round(pnl, 2), "return_pct": round(pnl / equity * 100, 2)}


def run_backtest(
    bars: list[MinuteBar],
    symbols: list[str],
    loaded: LoadedRiskLimits,
    starting_equity: float,
    params: OrbParams | None = None,
    stress_bps: float = 5.0,
) -> tuple[dict, OrbRecorder]:
    params = params or OrbParams()
    if not loaded.limits.portfolio.long_only:
        raise ValueError("this strategy is long only; risk limits must say long_only: true")
    bars = regular_hours(bars)
    usd = Currency.from_str("USD")
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(stdout_level=LogLevel.WARNING),
            risk_engine=RiskEngineConfig(
                bypass=False,
                max_order_submit_rate=loaded.limits.trading.max_order_submit_rate,
                max_order_modify_rate=loaded.limits.trading.max_order_submit_rate,
            ),
        )
    )
    engine.add_venue(
        Venue(VENUE),
        OmsType.NETTING,
        AccountType.CASH,
        [Money(starting_equity, usd)],
        fee_model=IbkrFixedFeeModel(),
        # Market and stop fills one tick worse than the bar price; targets fill when touched.
        fill_model=OneTickSlippageFillModel(1.0, 1.0, 42),
    )
    for s in symbols:
        engine.add_instrument(make_equity(s))
    engine.add_data(to_nautilus(bars))

    recorder = OrbRecorder()
    book = LimitBook(loaded.limits, starting_equity)
    engine.add_strategy(OpeningRangeBreakout(symbols, params, book, session_closes(bars), recorder))
    engine.run()
    engine.dispose()

    summary = summarize(recorder.trades, recorder.equity, recorder.journal, starting_equity, stress_bps)
    summary["benchmark"] = benchmark(bars, "SPY", starting_equity) if "SPY" in symbols else None
    summary["run"] = {
        "symbols": symbols,
        "strategy": "opening range breakout, long only",
        "params": {k: str(v) for k, v in vars(params).items()},
        "risk_limits_sha256": loaded.sha256,
        "pdt_regime": loaded.limits.trading.pdt_regime.value,
        "nautilus_trader": version("nautilus_trader"),
        "fees": "IBKR Pro Fixed + SEC fee",
        "fills": "bar prices, 1 tick slippage on market/stop orders",
    }
    return summary, recorder


def write_outputs(out_dir: Path, summary: dict, rec: OrbRecorder) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    with (out_dir / "journal.jsonl").open("w") as fh:
        for entry in rec.journal:
            fh.write(json.dumps(entry, default=str) + "\n")
    with (out_dir / "trades.csv").open("w", newline="") as fh:
        fields = ["symbol", "entry_time", "exit_time", "quantity", "entry_price", "exit_price"]
        fields += ["commission", "pnl", "exit_reason"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rec.trades)
    (out_dir / "report.html").write_text(report.render(summary, daily_equity(rec.equity)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data/bars"))
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--feed", default=None, help="sip or iex; default: the feed in manifest.json")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--limits", type=Path, default=Path("config/risk_limits.yaml"))
    parser.add_argument("--stress-bps", type=float, default=5.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    loaded = load_risk_limits(args.limits)
    manifest_path = args.data / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    feed = args.feed or manifest.get("feed", "sip")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    bars: list[MinuteBar] = []
    for s in symbols:
        path = bars_csv_path(args.data, s, feed)
        if not path.exists():
            print(f"ERROR: missing {path}. Download the data first.", file=sys.stderr)
            return 1
        bars.extend(read_bars_csv(path, s))

    summary, rec = run_backtest(bars, symbols, loaded, args.equity, stress_bps=args.stress_bps)
    summary["run"]["feed"] = feed
    summary["run"]["data_downloaded_at"] = manifest.get("downloaded_at")
    out = args.out or Path("reports") / f"backtest-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    write_outputs(out, summary, rec)
    print("\n".join(report.plain_words(summary)))
    print(f"\nFull report: {out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
