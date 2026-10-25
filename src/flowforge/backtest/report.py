"""One self-contained HTML page with the backtest results, written for a non-expert reader."""

from __future__ import annotations

import html


def _money(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _equity_svg(points: list[tuple[str, float]], width: int = 760, height: int = 220) -> str:
    if len(points) < 2:
        return "<p>Not enough days to draw a chart.</p>"
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = width / (len(values) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - (v - lo) / span * (height - 20) - 10:.1f}" for i, v in enumerate(values)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Account value by day">'
        f'<polyline fill="none" stroke="#2563eb" stroke-width="2" points="{coords}"/>'
        f'<text x="4" y="14" font-size="12" fill="#555">{_money(hi)}</text>'
        f'<text x="4" y="{height - 2}" font-size="12" fill="#555">{_money(lo)}</text>'
        f'<text x="{width - 4}" y="{height - 2}" font-size="12" fill="#555" text-anchor="end">'
        f"{html.escape(points[-1][0])}</text></svg>"
    )


def _rows(mapping: dict, money: bool = True) -> str:
    return "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{_money(v) if money else html.escape(str(v))}</td></tr>"
        for k, v in mapping.items()
    )


def _summary_table(title: str, s: dict) -> str:
    pf = s["profit_factor"]
    return (
        f"<h3>{html.escape(title)}</h3><table>"
        f"<tr><td>Trades</td><td>{s['trades']}</td></tr>"
        f"<tr><td>Money made or lost (after fees)</td><td>{_money(s['net_pnl'])}</td></tr>"
        f"<tr><td>Winning trades</td><td>{s['win_rate_pct']}%</td></tr>"
        f"<tr><td>Average win / average loss</td>"
        f"<td>{_money(s['avg_win'])} / {_money(s['avg_loss'])}</td></tr>"
        f"<tr><td>Profit factor (above 1 = made money)</td><td>{pf if pf is not None else 'n/a'}</td></tr>"
        f"<tr><td>Average per trade</td><td>{_money(s['expectancy'])}</td></tr>"
        f"<tr><td>Longest losing streak</td><td>{s['longest_losing_streak']} trades</td></tr>"
        f"<tr><td>Fees paid</td><td>{_money(s['commissions'])}</td></tr></table>"
    )


def plain_words(summary: dict) -> list[str]:
    a, st, sp = summary["all"], summary["stress"], summary["split"]
    out = [
        f"Starting with {_money(summary['starting_equity'])}, the account would have ended at "
        f"{_money(summary['ending_equity'])} ({summary['return_pct']}%) after fees.",
        f"The biggest drop from a high point was {_money(summary['max_drawdown'])} "
        f"({summary['max_drawdown_pct']}%). The longest run of losing trades in a row was "
        f"{a['longest_losing_streak']}.",
        f"With {st['extra_slippage_bps_per_side']} bps worse prices on every buy and sell, "
        f"the result would be {_money(st['net_pnl'])}.",
    ]
    bench = summary.get("benchmark")
    if bench:
        out.append(
            f"For comparison, just buying {bench['symbol']} and holding it would have made "
            f"{_money(bench['pnl'])} ({bench['return_pct']}%)."
        )
    if sp["cut_date"]:
        out.append(
            f"Before {sp['cut_date']} it made {_money(sp['first_two_thirds']['net_pnl'])}; "
            f"from then on it made {_money(sp['last_third']['net_pnl'])}. Big differences mean "
            f"the result depends on luck of the period."
        )
    if a["trades"] < 100:
        out.append(
            f"Only {a['trades']} trades: that's too few to trust either way (the paper-trading "
            f"gate needs at least 100)."
        )
    return out


def render(summary: dict, equity_points: list[tuple[str, float]]) -> str:
    run = summary.get("run", {})
    stress_title = f"Stress test: {summary['stress']['extra_slippage_bps_per_side']} bps worse fills"
    halts = _rows(summary["risk_halts"], money=False) or "<tr><td>None</td><td></td></tr>"
    bullets = "".join(f"<li>{html.escape(line)}</li>" for line in plain_words(summary))
    css = (
        "body{font-family:system-ui,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;"
        "color:#111;background:#fff}table{border-collapse:collapse;width:100%;margin:8px 0}"
        "td{border-bottom:1px solid #e5e7eb;padding:6px 4px}td:last-child{text-align:right}"
        ".note{background:#fef3c7;padding:10px 12px;border-radius:6px}svg{width:100%;height:auto}"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FlowForge backtest</title>
<style>{css}</style></head><body>
<h1>FlowForge practice backtest</h1>
<p>Strategy: opening range breakout, long only. Data: {html.escape(str(run.get("feed", "?")))} 1-minute
bars, {html.escape(str(summary["first_day"]))} to {html.escape(str(summary["last_day"]))}
({summary["trading_days"]} trading days). Symbols: {html.escape(", ".join(run.get("symbols", [])))}.</p>
<p class="note">This is a replay of old prices. No orders were sent anywhere. It tests the backtest
machine with a simple strategy; it is not proof that anything works.</p>
<h2>In plain words</h2><ul>{bullets}</ul>
<h2>Account value by day</h2>{_equity_svg(equity_points)}
{_summary_table("All trades", summary["all"])}
{_summary_table(stress_title, summary["stress"])}
{_summary_table("First two thirds of the period", summary["split"]["first_two_thirds"])}
{_summary_table("Last third of the period", summary["split"]["last_third"])}
<h3>Money made or lost by month</h3><table>{_rows(summary["monthly_pnl"])}</table>
<h3>By symbol (money made or lost)</h3>
<table>{_rows({k: v["net_pnl"] for k, v in summary["by_symbol"].items()})}</table>
<h3>How trades ended</h3><table>{_rows(summary["exit_reasons"], money=False)}</table>
<h3>Signals the risk rules blocked ({summary["signals"]} signals in total)</h3>
<table>{_rows(summary["signals_blocked"], money=False)}</table>
<h3>Risk halts</h3><table>{halts}</table>
<p>Worst day {_money(summary["worst_day"])}, best day {_money(summary["best_day"])}.
Errors, denied or rejected orders: {summary["errors"]}.</p>
<h3>Run details</h3><table>{_rows(run, money=False)}</table>
</body></html>"""
