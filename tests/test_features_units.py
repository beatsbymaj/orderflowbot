from flowforge.data.ticks import Trade
from flowforge.features import conditions as cond
from flowforge.features.classify import NO_SIDE, QUOTE_RULE, TICK_TEST, Classifier, Side
from flowforge.features.config import FeatureConfig
from flowforge.features.context import Level, PriorDay, Session
from flowforge.features.footprint import Bar, History, QuoteClock, _runs
from flowforge.features.sequencer import QUOTE, TRADE, Sequencer
from flowforge.features.vpin import Vpin

U = 1_000_000
REG = (" ",)


def test_conditions_follow_the_sip_matrix():
    assert cond.counts_volume(REG) and cond.market_priced(REG)
    assert not cond.counts_volume(("M",)) and not cond.market_priced(("Q",))
    assert cond.market_priced(("I",))  # odd lots trade at the market
    assert cond.market_priced(("F", "I"))
    for code in ("W", "4", "7", "T", "Z", "P"):
        assert cond.counts_volume((code,)) and not cond.market_priced((code,)), code
    assert cond.market_priced(("I", "W")) is False  # any "no" wins
    assert cond.is_auction(("O",)) and cond.is_auction(("6",)) and not cond.is_auction(REG)
    assert cond.unknown((" ", "F", "J", "?")) == ["J", "?"]


def test_quote_rule_then_tick_test():
    c = Classifier()
    assert c.classify(100 * U, REG) == (Side.NONE, NO_SIDE)  # no quote, no prior price
    c.on_quote(99_990_000, 100_010_000)
    assert c.classify(100_010_000, REG) == (Side.BUY, QUOTE_RULE)
    assert c.classify(99_995_000, REG) == (Side.SELL, QUOTE_RULE)
    # At the midpoint: tick test against the last different price (99.995 -> 100.000 is up).
    assert c.classify(100 * U, REG) == (Side.BUY, TICK_TEST)
    assert c.classify(100 * U, REG) == (Side.BUY, TICK_TEST)  # zero tick keeps the last move
    c.on_quote(100 * U, 100 * U)
    assert c.nbbo_state == "locked"
    assert c.classify(99_990_000, REG) == (Side.SELL, TICK_TEST)
    c.on_quote(100_020_000, 100_010_000)
    assert c.nbbo_state == "crossed"
    assert c.classify(100_050_000, REG) == (Side.BUY, TICK_TEST)
    c.on_quote(0, 100_010_000)
    assert c.nbbo_state == "missing"


def test_off_market_and_auction_prints_get_no_side():
    c = Classifier()
    c.on_quote(99_990_000, 100_010_000)
    assert c.classify(90 * U, ("W",)) == (Side.NONE, NO_SIDE)
    assert c.classify(100_010_000, ("O",)) == (Side.NONE, NO_SIDE)
    # The auction print still sets the tick-test reference; the W print does not.
    c.on_quote(100 * U, 100 * U)
    assert c.classify(100_020_000, REG) == (Side.BUY, TICK_TEST)


def test_sequencer_orders_within_the_window_and_drops_late_events():
    s = Sequencer(window_ns=10)
    assert s.push_quote(100, "q1") == []
    assert s.push_trade(100, "Q", 2, "t2") == []
    assert s.push_trade(100, "N", 9, "t1") == []
    out = s.push_trade(111, "Q", 3, "t3")
    # trades before quotes at the same ns; trades by (exchange, id)
    assert [e.payload for e in out] == ["t1", "t2", "q1"]
    assert [e.rank for e in out] == [TRADE, TRADE, QUOTE]
    assert s.push_quote(95, "late") == [] and s.late == 1
    assert s.push_trade(105, "Q", 4, "ok") == []  # after what was released: still in order
    assert [e.payload for e in s.flush()] == ["ok", "t3"]
    assert s.flush() == []


def test_runs_and_imbalances():
    assert _runs([1, 2, 3, 5, 6, 8, 9, 10, 11], 3) == [(1, 3), (8, 11)]
    assert _runs([], 3) == []
    cfg = FeatureConfig(imbalance_min_shares=100)
    bar = Bar(0, 60, QuoteClock(0), row=10_000)
    # rows 100..103: Ask at 101 vs Bid at 100 = 900/100 -> buy imbalance, etc.
    bar.rows = {100: [100, 50, 0], 101: [100, 900, 0], 102: [100, 400, 0], 103: [5, 300, 0]}
    buys, sells = bar._imbalances(cfg)
    assert buys == [101, 102, 103]
    assert sells == []
    a, _ = bar.analyse(cfg, History(5), 0)
    assert a["stacked_buy"] == [[1.01, 1.03]] and a["poc"] == 1.01
    assert a["unfinished_high"] is True and a["unfinished_low"] is True
    assert a["avg_cell"] is None and a["absorption"] == []  # no history yet


def _bar(rows, start=0, high=None, low=None):
    bar = Bar(start, start + 60, QuoteClock(start), row=10_000)
    bar.rows = rows
    bar.trades = 1
    bar.high = high if high is not None else max(rows) * 10_000
    bar.low = low if low is not None else min(rows) * 10_000
    return bar


def test_absorption_big_cells_and_exhaustion():
    cfg = FeatureConfig(extreme_lookback=3)
    hist = History(10)
    for i in range(3):
        b = _bar({100: [100, 100, 0], 101: [100, 100, 0], 102: [100, 100, 0]}, start=i)
        hist.push(b, (600, 6), cvd=10 * i)
    # Same 3-row range, both sides heavy at 101: absorption; 1,000 >= 6x100: big cells.
    bar = _bar({100: [50, 50, 0], 101: [1_000, 1_000, 0], 102: [50, 50, 0]})
    a, cells = bar.analyse(cfg, hist, cvd=0)
    assert a["absorption"] == [1.01] and a["big_bid"] == [1.01] and a["big_ask"] == [1.01]
    assert cells == (2_200, 6)
    # New high, almost nothing lifted at the top row: buyer exhaustion; CVD lower than before.
    top = _bar({100: [300, 300, 0], 101: [300, 300, 0], 103: [200, 5, 0]})
    a, _ = top.analyse(cfg, hist, cvd=-50)
    assert a["exhaustion"] == -1 and a["cvd_divergence"] == -1 and a["absorption"] == []
    low = _bar({99: [5, 200, 0], 100: [300, 300, 0], 101: [300, 300, 0]})
    a, _ = low.analyse(cfg, hist, cvd=500)
    assert a["exhaustion"] == 1 and a["cvd_divergence"] == 1


def test_quote_clock_time_weights_the_nbbo():
    q = QuoteClock(0)
    q.advance(10)  # nothing yet: missing
    q.update(10, 100 * U, 100_020_000, 300, 100)
    q.update(30, 100 * U, 100 * U, 100, 100)  # locked
    q.update(40, 100_030_000, 100_020_000, 100, 100)  # crossed
    q.update(50, 100_010_000, 100_030_000, 100, 100)
    q.advance(60)
    s = q.summary()
    assert s["updates"] == 4 and s["missing_share"] == 0.1667 and s["locked_share"] == 0.1667
    assert s["crossed_share"] == 0.1667
    assert s["spread_avg"] == 0.02 and s["size_imbalance_avg"] == round((0.5 * 20) / 30, 4)
    assert s["mid_ups"] == 1 and s["mid_downs"] == 2 and s["mid_change"] == 0.01
    nxt = q.carry(60)
    assert nxt.bid == 100_010_000 and nxt.updates == 0 and nxt.summary()["mid_change"] == 0.0
    assert QuoteClock(0).summary()["spread_avg"] is None


def test_vpin_buckets_split_trades():
    v = Vpin(bucket=100, window=2)
    v.add(150, 1)  # bucket 1: 100 buy
    assert v.buckets == 1 and v.value is None
    v.add(50, -1)  # bucket 2: 50 buy + 50 sell
    assert v.buckets == 2 and v.value == round((100 + 0) / 200, 6)
    v.add(100, -1)
    assert v.value == round((0 + 100) / 200, 6)


def test_value_area_and_levels():
    s = Session("2026-10-23", 0, 100, PriorDay(2.0, 1.0, 1.5, 1000), 5, 0.7, row=10_000)
    for price, size in ((1_000_000, 10), (1_010_000, 50), (1_020_000, 30), (1_030_000, 5), (1_040_000, 5)):
        s.add_trade(50, price, size)
    assert s.value_area_rows() == (101, 101, 102)  # POC 1.01, grows to the heavier side until >= 70%
    snap = s.snapshot(200)
    assert snap["poc"] == 1.01 and snap["val"] == 1.01 and snap["vah"] == 1.03
    assert snap["opening_range"] == {"high": 1.04, "low": 1.0, "complete": True}
    assert snap["prior_day"]["close"] == 1.5
    lvl = Level("stacked_imbalance", 300, 1_000_000, 1_009_999, 1, created=0)
    s.add_level(lvl)
    s.on_bar(300, 1_050_000, 1_060_000, None, 10)  # away
    s.on_bar(300, 1_050_000, 1_060_000, None, 20)  # away
    s.on_bar(300, 1_005_000, 1_060_000, None, 30)  # back: first touch
    assert (lvl.away_bars, lvl.touches) == (0, 1)
    ub = Level("unfinished", 300, 1_020_000, 1_020_000, -1, created=0)
    s.add_level(ub)
    s.on_bar(300, 1_000_000, 1_030_000, None, 40)
    assert ub.active is False
    s.on_bar(1800, 0, 0, 7, 50)
    s.on_bar(1800, 1_000_000, 1_010_000, 7, 60)  # same POC row twice: multiple node
    s.on_bar(1800, 1_000_000, 1_010_000, 7, 70)  # not added twice
    assert [lv.kind for lv in s.levels].count("multiple_node") == 1
    assert "unfinished" not in s.snapshot(80)["levels"]
    empty = Session("d", 0, 1, None, 5, 0.7, row=1).snapshot(0)
    assert empty["vwap"] is None and empty["opening_range"] is None and empty["levels"] == {}


def test_config_rows_and_thresholds():
    cfg = FeatureConfig(row_usd={"SPY": 0.25}, large_print_usd_by_symbol={"NVDA": 5e6})
    assert cfg.row_for("SPY", 670 * U) == 250_000
    assert cfg.row_for("QQQ", 600 * U) == 100_000  # 1 bp of $600 = $0.06 -> $0.10
    assert cfg.row_for("AAPL", 250 * U) == 50_000
    assert cfg.row_for("PENNY", 5 * U) == 10_000
    assert cfg.row_for("BRK", 700_000 * U) == 1_000_000
    assert cfg.large_print_for("NVDA") == 5e6 and cfg.large_print_for("SPY") == 2e6


def test_trade_dropped_flag_is_respected_by_engine():
    from flowforge.features.engine import FeatureEngine

    e = FeatureEngine()
    e.on_trade("X", Trade(1, 1.0, 1, "Q", REG, 1, "C", update="canceled"))
    assert e.symbols == {}
