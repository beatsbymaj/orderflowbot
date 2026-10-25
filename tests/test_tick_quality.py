from datetime import date

import pytest

from flowforge.data import quality as q
from flowforge.data.ticks import NS, Quote, Trade, format_ts_ns, parse_ts_ns, quote_size_multiplier

M = q.MINUTE_NS


def tr(ts, i, size=100, c=("@",), u="", x="D", price=10.0):
    return Trade(ts, price, size, x, c, i, "B", u)


def qt(ts, bid=10.0, ask=10.01):
    return Quote(ts, bid, 100, "Q", ask, 100, "P", (), "B")


def test_parse_ts_ns_keeps_nanoseconds_and_offsets():
    base = parse_ts_ns("2026-10-20T13:30:00Z")
    assert base == 1_792_503_000 * NS
    assert parse_ts_ns("2026-10-20T13:30:00.123456789Z") == base + 123_456_789
    assert parse_ts_ns("2026-10-20T13:30:00.5Z") == base + 500_000_000
    assert parse_ts_ns("2026-10-20T09:30:00-04:00") == base
    assert parse_ts_ns("2026-10-20T15:30:00+02:00") == base
    assert format_ts_ns(base + 7) == "2026-10-20T13:30:00.000000007Z"
    with pytest.raises(ValueError, match="time zone"):
        parse_ts_ns("2026-10-20T13:30:00")
    with pytest.raises(ValueError, match="fractional"):
        parse_ts_ns("2026-10-20T13:30:00.1234567890Z")


def test_quote_size_multiplier_switch_date():
    assert quote_size_multiplier(date(2025, 10, 31)) == 100
    assert quote_size_multiplier(date(2025, 11, 3)) == 1


def test_compare_with_bars_ignores_no_volume_conditions_and_dropped_trades():
    trades = [tr(5, 1), tr(6, 2, c=("Q",)), tr(7, 3, u="canceled"), tr(M + 1, 4), tr(M + 2, 5), tr(3 * M, 6)]
    bars = [q.MinuteBar(0, 1, 100), q.MinuteBar(M, 1, 100), q.MinuteBar(2 * M, 4, 400)]
    result = q.compare_with_bars(trades, bars, 0, 3 * M)
    assert result.minutes_checked == 3 and result.minutes_matched == 1
    assert result.extra_minutes == [M] and result.gap_minutes == [2 * M]
    assert result.missing_trades == 4 and result.bar_volume == 600 and result.tick_volume == 300


def test_merge_minutes_and_trades():
    assert q.merge_minutes([2 * M, 0, M, 5 * M, M]) == [(0, 3 * M), (5 * M, 6 * M)]
    a, b = tr(2, 1), tr(1, 2)
    assert q.merge_trades([a], [b, tr(2, 1, size=999)]) == [b, a]


def test_trade_checks_flags_bad_records():
    trades = [tr(1, 1), tr(1, 1), tr(0, 2, size=0, price=0.0), tr(3, 3, price=10.0001, u="corrected")]
    checks = q.trade_checks(trades)
    assert checks["duplicate_ids"] == 1 and checks["out_of_order"] == 1
    assert checks["bad_price"] == 1 and checks["zero_size"] == 1 and checks["sub_penny"] == 1
    assert checks["updates"] == {"corrected": 1} and checks["in_catalog"] == 4


def test_quote_checks_counts_crossed_locked_empty_and_silences():
    quotes = [
        qt(10 * NS),
        qt(11 * NS, 10.02, 10.01),
        qt(12 * NS, 10.0, 10.0),
        qt(13 * NS, 0.0),
        qt(80 * NS, ask=0.0),
    ]
    checks = q.quote_checks(quotes, 0, 200 * NS, 30 * NS)
    assert (checks["crossed"], checks["locked"], checks["no_bid"], checks["no_ask"]) == (1, 1, 1, 1)
    assert checks["silences"] == [(13 * NS, 80 * NS), (80 * NS, 200 * NS)]
    assert q.quote_checks([], 0, 10, 5)["silences"] == [(0, 10)]


def test_add_counts():
    total = q.add_counts({}, {"count": 2, "updates": {"canceled": 1}, "silences": [1]})
    total = q.add_counts(total, {"count": 3, "updates": {"canceled": 2}, "silences": [2]})
    assert total == {"count": 5, "updates": {"canceled": 3}, "silences": [1, 2]}
