"""Phase 5 walk-forward tests (docs/research/14 and 17). Offline, no orders.

    python -m flowforge.research.walkforward --features data/features --ticks data/ticks \\
        --out reports/phase5
    python -m flowforge.research.walkforward --study 5b      # Phase 5b: its own days and folders

Reads the Phase 3 feature files (data/features/<SYMBOL>/<day>.jsonl.gz) and the Phase 2
quotes, runs every hypothesis in every grid configuration through the fast simulator, picks a
configuration per train window, joins the out-of-sample windows and applies the pass bar.
Writes walkforward.md (plain words + tables), walkforward.json and trades.csv.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import random
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import fmean

from flowforge.backtest.fees import ibkr_fixed_commission
from flowforge.config.risk_limits import RiskLimits, load_risk_limits
from flowforge.data.ticks import parse_ts_ns
from flowforge.gate.evaluate import CachedGate
from flowforge.gate.evaluate import evaluate as evaluate_gate
from flowforge.research import stats
from flowforge.research.fastsim import Costs, Refusal, Trade, simulate
from flowforge.research.quotes import QuoteSource
from flowforge.risk.state import PDT_WINDOW_DAYS, trading_days_back
from flowforge.signals.setups import (
    BOOK_DEFAULT,
    ENTRY_TF,
    SETUPS,
    STOP_MODES,
    TARGET_MODES,
    Signal,
    SignalEngine,
    entry_window,
)
from flowforge.signals.setups_b import (
    CONTROL_B,
    DEFAULT_B,
    NAMES_B,
    SETUPS_B,
    STOP_MODES_B,
    TARGET_MODES_B,
    SignalEngineB,
)

CONFIGS = tuple((s, t) for s in STOP_MODES for t in TARGET_MODES)
N_TRIALS = len(SETUPS) * len(CONFIGS)  # 20, pre-registered
TRAIN_DAYS, TEST_DAYS = 20, 10
MIN_TRAIN_TRADES = 10
MIN_OOS_TRADES = 30
ALPHA = 0.05
MIN_DSR = 0.95
SEED = 20261026
START_EQUITY = 10_000.0
PDT_EQUITY = 25_000.0


@dataclass(frozen=True)
class Study:
    """One pre-registered test: its setups, grid, trial count, control and default folders."""

    name: str
    plan_doc: str
    setups: tuple[str, ...]
    configs: tuple[tuple[str, str], ...]
    default: tuple[str, str]
    n_trials: int  # for the deflated Sharpe
    seed: int
    control: tuple[str, str]
    engine: Callable[[], object]
    names: dict
    gate_fallback: str
    first: str
    last: str
    features: str
    ticks: str
    out: str


PHASE5 = Study(
    name="Phase 5",
    plan_doc="docs/research/14-signal-hypotheses.md",
    setups=SETUPS,
    configs=CONFIGS,
    default=BOOK_DEFAULT,
    n_trials=N_TRIALS,
    seed=SEED,
    control=("atr", "2r"),
    engine=SignalEngine,
    names={},  # filled below from NAMES
    gate_fallback="H4",
    first="2026-07-31",
    last="2026-10-23",
    features="data/features",
    ticks="data/ticks",
    out="reports/phase5",
)
# Phase 5b (docs/research/17): 4 setups x 2 exits; the deflated Sharpe counts Phase 5's 20 too.
CONFIGS_B = tuple((s, t) for s in STOP_MODES_B for t in TARGET_MODES_B)
PHASE5B = Study(
    name="Phase 5b",
    plan_doc="docs/research/17-phase5b-hypotheses.md",
    setups=SETUPS_B,
    configs=CONFIGS_B,
    default=DEFAULT_B,
    n_trials=len(SETUPS_B) * len(CONFIGS_B) + N_TRIALS,  # 8 + 20 = 28
    seed=20261029,
    control=CONTROL_B,
    engine=SignalEngineB,
    names=NAMES_B,
    gate_fallback="B2",
    first="2026-04-01",
    last="2026-07-29",
    features="data/features-b",
    ticks="data/ticks-b",
    out="reports/phase5b",
)
STUDIES = {"5": PHASE5, "5b": PHASE5B}


def cfg_name(c: tuple[str, str]) -> str:
    return f"{c[0]}/{c[1]}"


# -- inputs ----------------------------------------------------------------------------------
def read_records(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@dataclass
class StockDay:
    symbol: str
    day: str
    signals: list[Signal]
    bar_ends: list[int]  # 5-min bar closes inside the entry window (control entries)
    atr: float | None
    row: float


def stock_day(symbol: str, day: str, records: Iterable[dict], study: Study = PHASE5) -> StockDay:
    engine = study.engine()
    signals: list[Signal] = []
    ends: list[int] = []
    atr, row = None, 0.0
    for rec in records:
        signals += engine.on_record(rec)
        if rec.get("type") == "bar" and rec["tf"] == ENTRY_TF and rec["trades"] and not rec.get("gap"):
            end = parse_ts_ns(rec["end"])
            if entry_window(end):
                ends.append(end)
            row = rec["row"] or row
            prior = (rec.get("context") or {}).get("prior_day") or {}
            if prior.get("high") is not None:
                atr = prior["high"] - prior["low"]
    return StockDay(symbol, day, signals, ends, atr, row)


def load_stock_days(
    features: Path, symbols: list[str], days: list[str] | None, study: Study = PHASE5
) -> list[StockDay]:
    out = []
    for symbol in symbols:
        for path in sorted((features / symbol).glob("*.jsonl.gz")):
            day = path.name.split(".")[0]
            if days is None or day in days:
                out.append(stock_day(symbol, day, read_records(path), study))
    return out


# -- simulation ------------------------------------------------------------------------------
LIVE_TRADES = "live_trades.jsonl"


class LiveTrades:
    """Each simulated trade as one JSON line, written as it happens, so the Lab screen can draw
    the running total in R. Started fresh for every run; the final numbers are in the report."""

    FIELDS = ("setup", "config", "symbol", "day", "exit_ts", "exit_reason", "r", "net_usd")

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> LiveTrades:
        self.fh = self.path.open("w", encoding="utf-8")
        return self

    def write(self, t: Trade) -> None:
        row = {k: getattr(t, k) for k in self.FIELDS}
        self.fh.write(json.dumps(row) + "\n")
        self.fh.flush()

    def __exit__(self, *exc) -> None:
        self.fh.close()


def run_signals(
    signals: list[Signal],
    config: tuple[str, str],
    quotes: QuoteSource,
    costs: Costs,
    on_trade: Callable[[Trade], None] | None = None,
) -> tuple[list[Trade], list[Refusal]]:
    """One setup, one configuration: one trade per symbol at a time. `on_trade` sees each trade
    as soon as it is simulated (the dashboard's Lab screen draws them live)."""
    trades, refused = [], []
    busy_until: dict[str, int] = {}
    for sig in sorted(signals, key=lambda s: (s.ts, s.symbol)):
        if sig.ts < busy_until.get(sig.symbol, 0):
            continue
        res = simulate(sig, config[0], config[1], quotes, costs)
        if isinstance(res, Trade):
            trades.append(res)
            busy_until[sig.symbol] = res.exit_ts
            if on_trade is not None:
                on_trade(res)
        else:
            refused.append(res)
    return trades, refused


def control_signals(sd: StockDay, count: int, rng: random.Random) -> list[Signal]:
    if not sd.bar_ends or not sd.atr or count <= 0:
        return []
    picks = sorted(rng.sample(sd.bar_ends, min(count, len(sd.bar_ends))))
    return [
        Signal("CTRL", sd.symbol, ts, sd.day, 0.0, 0.0, sd.row or 0.01, sd.atr, (), (), "random")
        for ts in picks
    ]


def folds(days: list[str]) -> list[tuple[list[str], list[str]]]:
    out = []
    start = 0
    while start + TRAIN_DAYS + TEST_DAYS <= len(days):
        out.append(
            (days[start : start + TRAIN_DAYS], days[start + TRAIN_DAYS : start + TRAIN_DAYS + TEST_DAYS])
        )
        start += TEST_DAYS
    return out


def choose(
    trades_by_cfg: dict[tuple[str, str], list[Trade]], train: set[str], study: Study = PHASE5
) -> tuple[str, str]:
    best, best_mean = study.default, None
    for cfg in study.configs:
        rs = [t.r for t in trades_by_cfg.get(cfg, []) if t.day in train]
        if len(rs) < MIN_TRAIN_TRADES:
            continue
        m = fmean(rs)
        if (
            best_mean is None
            or m > best_mean + 1e-12
            or (abs(m - best_mean) <= 1e-12 and cfg == study.default)
        ):
            best, best_mean = cfg, m
    return best


# -- account check ---------------------------------------------------------------------------
def account_replay(trades: list[Trade], limits: RiskLimits, equity: float = START_EQUITY) -> dict:
    """The same trades on a cash account with every limit in risk_limits.yaml and IBKR Fixed
    commissions (incl. the $1 minimum). Fills and exits as in the fast simulator."""
    pt, pf, lo, tr = limits.per_trade, limits.portfolio, limits.loss, limits.trading
    open_pos: list[tuple[int, str, float]] = []  # exit_ts, symbol, notional
    pending: list[tuple[int, float]] = []  # exit_ts, pnl (realised at exit)
    day = None
    day_start = week_peak = equity
    week = None
    halted_day = halted_week = False
    taken_by_day: dict[str, int] = {}
    spent_by_day: dict[str, float] = {}  # cash account: buys today, paid from settled cash
    skipped: dict[str, int] = {}
    curve: list[tuple[str, float]] = []
    pnls: list[float] = []
    fees_total = 0.0

    def realise(until: int) -> None:
        nonlocal equity, halted_day, halted_week, week_peak
        for item in sorted(p for p in pending if p[0] <= until):
            pending.remove(item)
            equity += item[1]
            if equity <= day_start * (1 - lo.max_daily_loss_pct / 100):
                halted_day = True
            week_peak = max(week_peak, equity)
            if equity <= week_peak * (1 - lo.max_weekly_drawdown_pct / 100):
                halted_week = True

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for t in sorted(trades, key=lambda x: (x.entry_ts, x.symbol)):
        realise(t.entry_ts)
        d = t.day
        if d != day:
            if day is not None:
                curve.append((day, round(equity, 2)))
            day, day_start, halted_day = d, equity, False
            iso = datetime.fromisoformat(d).isocalendar()
            if (iso.year, iso.week) != week:
                week, week_peak = (iso.year, iso.week), equity
        open_pos[:] = [p for p in open_pos if p[0] > t.entry_ts]
        if halted_week:
            skip("weekly drawdown halt")
            continue
        if halted_day:
            skip("daily loss halt")
            continue
        if any(p[1] == t.symbol for p in open_pos):
            skip("already holding symbol")
            continue
        if len(open_pos) >= pf.max_concurrent_positions:
            skip("max positions")
            continue
        if taken_by_day.get(d, 0) >= tr.max_trades_per_day:
            skip("max trades per day")
            continue
        if tr.pdt_regime == "legacy_pdt" and equity < PDT_EQUITY:
            window = set(x.isoformat() for x in trading_days_back(date.fromisoformat(d), PDT_WINDOW_DAYS))
            if sum(n for k, n in taken_by_day.items() if k in window) >= tr.legacy_pdt_max_day_trades:
                skip("legacy PDT day-trade limit")
                continue
        per_share_risk = t.entry - t.stop
        shares = int(
            min(
                pt.max_risk_pct / 100 * equity / per_share_risk,
                pt.max_position_notional_pct / 100 * equity / t.entry,
            )
        )
        gross_room = pf.max_gross_exposure_pct / 100 * equity - sum(p[2] for p in open_pos)
        shares = min(shares, int(max(gross_room, 0) // t.entry))
        if tr.pdt_regime == "cash":
            # Flat every night, so the day's settled cash is the day's starting equity; sales
            # today settle T+1 and can't fund another buy today.
            settled = day_start - spent_by_day.get(d, 0.0)
            if int(max(settled, 0) // t.entry) < 1:
                skip("settled cash used up for the day")
                continue
            shares = min(shares, int(max(settled, 0) // t.entry))
        if shares < 1:
            skip("size below one share")
            continue
        fees = ibkr_fixed_commission(shares, t.entry, False) + ibkr_fixed_commission(shares, t.exit, True)
        pnl = (t.exit - t.entry) * shares - fees
        fees_total += fees
        pnls.append(pnl)
        taken_by_day[d] = taken_by_day.get(d, 0) + 1
        spent_by_day[d] = spent_by_day.get(d, 0.0) + shares * t.entry
        open_pos.append((t.exit_ts, t.symbol, shares * t.entry))
        pending.append((t.exit_ts, pnl))
    realise(2**62)
    if day is not None:
        curve.append((day, round(equity, 2)))
    eq = [START_EQUITY] + [v for _, v in curve]
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    return {
        "start_equity": START_EQUITY,
        "end_equity": round(equity, 2),
        "net_pnl": round(equity - START_EQUITY, 2),
        "trades_taken": len(pnls),
        "trades_skipped": dict(sorted(skipped.items())),
        "fees": round(fees_total, 2),
        "max_drawdown_pct": round(dd * 100, 3),
        "losing_streak": stats.losing_streak(pnls),
        "equity_by_day": curve,
    }


# -- the test ----------------------------------------------------------------------------------
@dataclass
class Result:
    days: list[str]
    study: Study = PHASE5
    folds: list[dict] = field(default_factory=list)
    full: dict = field(default_factory=dict)  # setup -> cfg -> summary on all days
    oos: dict = field(default_factory=dict)  # setup -> summary + tests
    control: dict = field(default_factory=dict)
    account: dict = field(default_factory=dict)
    refusals: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)


def run(
    stock_days: list[StockDay],
    quotes: QuoteSource,
    limits: RiskLimits,
    costs: Costs | None = None,
    reps: int = 10_000,
    progress: Callable[[str], None] = lambda _: None,
    gate: CachedGate | None = None,
    study: Study = PHASE5,
    on_trade: Callable[[Trade], None] | None = None,
) -> Result:
    costs = costs or Costs()
    days = sorted({sd.day for sd in stock_days})
    res = Result(days, study)
    SETUPS, CONFIGS = study.setups, study.configs  # noqa: N806 (the study's, shadowing Phase 5's)
    by_setup: dict[str, list[Signal]] = {s: [] for s in SETUPS}
    for sd in stock_days:
        for sig in sd.signals:
            by_setup[sig.setup].append(sig)
    trades: dict[str, dict[tuple[str, str], list[Trade]]] = {}
    for setup in SETUPS:
        trades[setup] = {}
        for cfg in CONFIGS:
            ts, refused = run_signals(by_setup[setup], cfg, quotes, costs, on_trade)
            trades[setup][cfg] = ts
            for r in refused:
                key = f"{setup} {cfg_name(cfg)}: {r.reason}"
                res.refusals[key] = res.refusals.get(key, 0) + 1
            progress(f"{setup} {cfg_name(cfg)}: {len(by_setup[setup])} signals, {len(ts)} trades")
    res.full = {s: {cfg_name(c): stats.summary([t.r for t in trades[s][c]]) for c in CONFIGS} for s in SETUPS}
    trial_sharpes = [
        sh for s in SETUPS for c in CONFIGS if (sh := stats.sharpe([t.r for t in trades[s][c]])) is not None
    ]
    oos: dict[str, list[Trade]] = {s: [] for s in SETUPS}
    picked_folds: dict[str, list[tuple[list[str], list[str], list[Trade]]]] = {s: [] for s in SETUPS}
    test_days: list[str] = []
    for i, (train, test) in enumerate(folds(days)):
        fold = {"fold": i + 1, "train": [train[0], train[-1]], "test": [test[0], test[-1]], "chosen": {}}
        for s in SETUPS:
            cfg = choose(trades[s], set(train), study)
            picked_folds[s].append((train, test, trades[s][cfg]))
            picked = [t for t in trades[s][cfg] if t.day in test]
            oos[s] += picked
            fold["chosen"][s] = {"config": cfg_name(cfg)} | stats.summary([t.r for t in picked])
        test_days += test
        res.folds.append(fold)
    pvals = {s: stats.bootstrap_p([(t.day, t.r) for t in oos[s]], reps) if oos[s] else None for s in SETUPS}
    adjusted = stats.holm(pvals)
    # Control: random entries, as many per OOS stock-day as all hypotheses' signals there.
    rng = random.Random(study.seed)  # noqa: S311 - reproducible, not security
    ctrl: list[Signal] = []
    test_set = set(test_days)
    for sd in sorted(stock_days, key=lambda x: (x.day, x.symbol)):
        if sd.day in test_set:
            ctrl += control_signals(sd, len(sd.signals), rng)
    ctrl_trades, _ = run_signals(ctrl, study.control, quotes, costs, on_trade)
    ctrl_mean = fmean([t.r for t in ctrl_trades]) if ctrl_trades else None
    res.control = stats.summary([t.r for t in ctrl_trades]) | {"days": len(test_set)}
    for s in SETUPS:
        rs = [t.r for t in oos[s]]
        summ = stats.summary(rs)
        d = stats.dsr(rs, trial_sharpes, study.n_trials) if rs else None
        checks = {
            "enough_trades": len(rs) >= MIN_OOS_TRADES,
            "significant_after_holm": adjusted[s] is not None
            and adjusted[s] < ALPHA
            and (summ["mean_r"] or 0) > 0,
            "deflated_sharpe_ok": d is not None and d >= MIN_DSR,
            "beats_control": ctrl_mean is not None and bool(rs) and fmean(rs) > ctrl_mean,
        }
        by_symbol: dict[str, list[float]] = {}
        for t in oos[s]:
            by_symbol.setdefault(t.symbol, []).append(t.r)
        res.oos[s] = summ | {
            "p_value": _r(pvals[s]),
            "p_holm": _r(adjusted[s]),
            "deflated_sharpe": _r(d),
            "checks": checks,
            "passes": all(checks.values()),
            "by_symbol": {k: stats.summary(v) for k, v in sorted(by_symbol.items())},
            "exit_reasons": _count(t.exit_reason for t in oos[s]),
        }
    every = [t for s in SETUPS for t in oos[s]]
    passing = [t for s in SETUPS if res.oos[s]["passes"] for t in oos[s]]
    res.account = {
        "all_hypotheses": account_replay(every, limits),
        "passing_only": account_replay(passing, limits) if passing else None,
        "per_hypothesis": {s: account_replay(oos[s], limits) for s in SETUPS},
    }
    res.trades = sorted(every, key=lambda t: t.entry_ts)
    if gate is not None:
        # Gate tested on what passed, or on the study's fallback (Phase 5: H4) if nothing did.
        targets = [s for s in SETUPS if res.oos[s]["passes"]] or [study.gate_fallback]
        signals = {sig.id: sig for sigs in by_setup.values() for sig in sigs}
        res.gate = {s: evaluate_gate(s, picked_folds[s], signals, gate) for s in targets}
    return res


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 5)


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


# -- report ----------------------------------------------------------------------------------
NAMES = {
    "H1": "Stacked-imbalance pullback",
    "H2": "Large-print pullback",
    "H3": "Multiple-node pullback",
    "H4": "Support zone + confirmation",
    "H5": "Follow a buy-side sweep",
}
PHASE5.names.update(NAMES)


def render(res: Result, missing: list[str]) -> str:
    study = res.study
    SETUPS, CONFIGS, NAMES = study.setups, study.configs, study.names  # noqa: N806
    passed = [s for s in SETUPS if res.oos.get(s, {}).get("passes")]
    test_days = len(res.folds) * TEST_DAYS
    lines = [
        f"# {study.name} walk-forward results",
        "",
        f"Days: {len(res.days)} ({res.days[0] if res.days else '-'} to {res.days[-1] if res.days else '-'}),"
        f" {len(res.folds)} folds, {test_days} out-of-sample days.",
        f"Plan: {study.plan_doc}.",
        "Numbers are R per trade (1R = the loss at the stop), net of spread, slippage and per-share costs.",
        "",
        "## In plain words",
    ]
    if passed:
        lines.append(f"- Passed the pre-registered bar: {', '.join(f'{s} ({NAMES[s]})' for s in passed)}.")
    else:
        lines.append(
            "- **No idea passed the bar.** None showed an edge we can trust after costs on the test days."
        )
    acct = res.account.get("all_hypotheses") or {}
    if acct:
        lines.append(
            "- On a $10,000 account with every risk limit, all ideas together would have gone"
            " from $10,000 to"
            f" ${acct['end_equity']:,.2f} ({acct['trades_taken']} trades, ${acct['fees']:,.2f} fees,"
            f" worst drop {acct['max_drawdown_pct']}%)."
        )
    if missing:
        lines.append(
            f"- Missing stock-days (not downloaded or no features): {len(missing)}: {', '.join(missing[:20])}"
        )
    lines += [
        "",
        "## Out-of-sample results (test windows joined)",
        "| Idea | Trades | Win rate | Mean R | Total R | Max DD (R) | Losing streak | p | p (Holm)"
        " | Deflated Sharpe | Passes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in SETUPS:
        o = res.oos[s]
        lines.append(
            f"| {s} {NAMES[s]} | {o['trades']} | {o['win_rate']} | {o['mean_r']} | {o['total_r']} |"
            f" {o['max_drawdown_r']} | {o['losing_streak']} | {o['p_value']} | {o['p_holm']} |"
            f" {o['deflated_sharpe']} | {'yes' if o['passes'] else 'no'} |"
        )
    c = res.control
    lines += [
        f"| Control (random entries) | {c.get('trades')} | {c.get('win_rate')} | {c.get('mean_r')} |"
        f" {c.get('total_r')} | {c.get('max_drawdown_r')} | {c.get('losing_streak')} | | | | |",
        "",
        "## Folds (configuration picked on the 20 train days, used on the next 10)",
        "| Fold | Train | Test | " + " | ".join(SETUPS) + " |",
        "|---|---|---|" + "---|" * len(SETUPS),
    ]
    for f in res.folds:
        cells = [
            f"{f['chosen'][s]['config']}: {f['chosen'][s]['trades']} tr, {f['chosen'][s]['mean_r']}"
            for s in SETUPS
        ]
        lines.append(
            f"| {f['fold']} | {f['train'][0]}…{f['train'][1]} | {f['test'][0]}…{f['test'][1]} | "
            + " | ".join(cells)
            + " |"
        )
    lines += [
        "",
        "## Every configuration on all days (in-sample, for the trial count)",
        "| Idea | " + " | ".join(cfg_name(c) for c in CONFIGS) + " |",
        "|---|" + "---|" * len(CONFIGS),
    ]
    for s in SETUPS:
        cells = [
            f"{res.full[s][cfg_name(c)]['trades']} tr, {res.full[s][cfg_name(c)]['mean_r']}" for c in CONFIGS
        ]
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## $10,000 account check (every risk limit, $1 minimum commission)",
        "| Ideas | Taken | End equity | Fees | Max DD % | Losing streak | Skipped |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, a in [("all ideas", res.account.get("all_hypotheses"))] + [
        (s, res.account["per_hypothesis"][s]) for s in SETUPS
    ]:
        if a:
            why = ", ".join(f"{k}: {v}" for k, v in a["trades_skipped"].items()) or "-"
            lines.append(
                f"| {name} | {a['trades_taken']} | ${a['end_equity']:,.2f} | ${a['fees']:,.2f} |"
                f" {a['max_drawdown_pct']} | {a['losing_streak']} | {why} |"
            )
    lines += ["", "## Jev gate", ""]
    if not res.gate:
        lines.append("- Not run (no TYPESAFE_API_KEY, or --jev not given). The gate stays off.")
    for s, g in res.gate.items():
        b, gd, cal = g["baseline"], g["gated"], g["calibration"]
        lines.append(
            f"- {s}: {'**keep**' if g['keep_gate'] else 'not kept'}. Without gate {b['trades']} trades,"
            f" mean {b['mean_r']}R, total {b['total_r']}R; with gate {gd['trades']} trades,"
            f" mean {gd['mean_r']}R,"
            f" total {gd['total_r']}R; p = {g['p_value']}. Brier {cal['brier']} vs base rate"
            f" {cal['base_rate_brier']}. Answers: {g['answers']}."
        )
    lines += ["", "## Why signals didn't become trades", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(res.refusals.items())] or ["- none"]
    return "\n".join(lines) + "\n"


def write(res: Result, out: Path, missing: list[str]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "walkforward.md").write_text(render(res, missing), encoding="utf-8")
    keys = ("days", "folds", "full", "oos", "control", "account", "refusals", "gate")
    data = {"study": res.study.name, "plan": res.study.plan_doc} | {k: getattr(res, k) for k in keys}
    data["missing"] = missing
    (out / "walkforward.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    with (out / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(Trade.__dataclass_fields__))
        w.writeheader()
        for t in res.trades:
            w.writerow(t.as_dict())
    return out / "walkforward.md"


def expected_days(first: str, last: str) -> list[str]:
    from flowforge.risk.calendar import is_trading_day

    d, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while d <= end:
        if is_trading_day(d):
            out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--study", choices=sorted(STUDIES), default="5", help="5 (research/14), 5b (research/17)")
    p.add_argument("--features", type=Path, help="default: the study's (data/features, data/features-b)")
    p.add_argument("--ticks", type=Path, help="default: the study's (data/ticks, data/ticks-b)")
    p.add_argument("--out", type=Path, help="default: the study's (reports/phase5, reports/phase5b)")
    p.add_argument("--symbols", default="SPY,QQQ,AAPL,MSFT,NVDA")
    p.add_argument("--first", help="first study day (default: the pre-registered one)")
    p.add_argument("--last", help="last study day (default: the pre-registered one)")
    p.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    p.add_argument("--jev", action="store_true", help="also test the Jev gate (needs TYPESAFE_API_KEY)")
    a = p.parse_args(argv)
    from flowforge.research.quotes import CatalogQuotes

    study = STUDIES[a.study]
    a.features = a.features or Path(study.features)
    a.ticks = a.ticks or Path(study.ticks)
    a.out = a.out or Path(study.out)
    symbols = a.symbols.split(",")
    wanted = expected_days(a.first or study.first, a.last or study.last)
    sds = load_stock_days(a.features, symbols, wanted, study)
    have = {(sd.symbol, sd.day) for sd in sds}
    missing = [f"{s} {d}" for s in symbols for d in wanted if (s, d) not in have]
    if not sds:
        print("No feature files found. Run the features step first (docs/runbooks/walkforward.md).")
        return 1
    limits = load_risk_limits(a.risk_limits).limits
    gate = None
    if a.jev:
        from flowforge.gate.jev import JevConfig, JevGate

        jev = JevGate(JevConfig(enabled=True, shadow=True))  # shadow: always returns P(yes)
        if not jev.api_key:
            print("--jev needs TYPESAFE_API_KEY in .env; the gate test is skipped.")
        else:
            a.out.mkdir(parents=True, exist_ok=True)
            gate = CachedGate(jev.decide, a.out / "jev_answers.jsonl")
    a.out.mkdir(parents=True, exist_ok=True)
    with LiveTrades(a.out / LIVE_TRADES) as live:
        res = run(
            sds,
            CatalogQuotes(a.ticks),
            limits,
            progress=lambda m: print(m, flush=True),
            gate=gate,
            study=study,
            on_trade=live.write,
        )
    path = write(res, a.out, missing)
    print(f"Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
