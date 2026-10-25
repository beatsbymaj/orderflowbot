import gzip
import http.client
import json
import urllib.error
import urllib.parse
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from flowforge.data import alpaca_ticks as at
from flowforge.data.alpaca_bars import AlpacaError, AlpacaKeys
from flowforge.data.ticks import NS, format_ts_ns, parse_ts_ns

ET = ZoneInfo("America/New_York")
KEYS = AlpacaKeys("id", "secret")
DAY = date(2026, 10, 20)  # a Tuesday


def et(h, m, s=0, day=DAY):
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=ET).timestamp()) * NS


class FakeAlpaca:
    """Serves bars, trades and quotes like the data API, paging `page` records at a time."""

    def __init__(self, page=3):
        self.page = page
        self.data = {"bars": {}, "trades": {}, "quotes": {}}
        self.calls = []
        self.drop_once = set()  # trade ids missing on the first request that covers them

    def add(self, kind, symbol, *records):
        self.data[kind].setdefault(symbol, []).extend(records)

    def __call__(self, url, headers):
        assert headers["APCA-API-KEY-ID"] == "id"
        parsed = urllib.parse.urlparse(url)
        kind = parsed.path.rsplit("/", 1)[-1]
        q = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((kind, q))
        symbol = q["symbols"]
        start = parse_ts_ns(q["start"] if "T" in q["start"] else q["start"] + "T00:00:00Z")
        end = parse_ts_ns(q["end"] if "T" in q["end"] else q["end"] + "T00:00:00Z")
        rows = [r for r in self.data[kind].get(symbol, []) if start <= parse_ts_ns(r["t"]) <= end]
        if kind == "trades" and "page_token" not in q:
            hidden = {r["i"] for r in rows} & self.drop_once
            self.drop_once -= hidden
            rows = [r for r in rows if r["i"] not in hidden]
        offset = int(q.get("page_token", 0))
        chunk = rows[offset : offset + self.page]
        token = str(offset + self.page) if offset + self.page < len(rows) else None
        return {kind: {symbol: chunk} if chunk else {}, "next_page_token": token}


def trade(ts, i, size=100, price=100.0, x="D", c=("@",), u=None):
    raw = {"t": format_ts_ns(ts), "x": x, "p": price, "s": size, "c": list(c), "i": i, "z": "B"}
    if u:
        raw["u"] = u
    return raw


def quote(ts, bp=100.0, ap=100.01, bs=3, asz=4):
    return {
        "t": format_ts_ns(ts),
        "bx": "Q",
        "bp": bp,
        "bs": bs,
        "ax": "P",
        "ap": ap,
        "as": asz,
        "c": ["R"],
        "z": "B",
    }


def bar(ts, n, v):
    return {"t": format_ts_ns(ts).replace(".000000000Z", "Z"), "o": 1, "h": 1, "l": 1, "c": 1, "v": v, "n": n}


def no_sleep_settings(tmp_path, fake, **kw):
    kw.setdefault("window_minutes", 1)
    return at.Settings(KEYS, tmp_path, fetch=fake, **kw)


def full_day_fake(day=DAY, close=(16, 0)):
    fake = FakeAlpaca()
    for symbol in ("SPY",):
        fake.add(
            "bars",
            symbol,
            bar(et(9, 30, day=day), 2, 300),
            bar(et(close[0], close[1], day=day) - 60 * NS, 1, 100),
        )
    return fake


def test_throttle_spaces_calls_and_retries_transient_errors():
    clock = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    attempts = []

    def flaky(url, headers):
        attempts.append(url)
        if len(attempts) == 1:
            raise AlpacaError("rate limited", 429)
        if len(attempts) == 2:
            raise urllib.error.URLError("reset")
        if len(attempts) == 3:
            raise http.client.IncompleteRead(b"partial", 100)  # transfer cut off mid-page
        if len(attempts) == 4:
            raise json.JSONDecodeError("truncated", "{", 1)
        return {"ok": True}

    t = at.Throttle(flaky, 60, sleep=sleep, clock=lambda: clock[0])
    assert t("u", {}) == {"ok": True}
    assert t.calls == 5
    assert sleeps[0] == 2.0  # backoff after the 429
    assert 4.0 in sleeps  # backoff after the network error


def test_throttle_gives_up_on_permanent_and_repeated_errors():
    def forbidden(url, headers):
        raise AlpacaError("subscription does not permit", 403)

    with pytest.raises(AlpacaError, match="permit"):
        at.Throttle(forbidden, 600, sleep=lambda s: None)("u", {})

    def down(url, headers):
        raise TimeoutError("slow")

    with pytest.raises(AlpacaError, match="network error"):
        at.Throttle(down, 600, sleep=lambda s: None)("u", {})

    def busy(url, headers):
        raise AlpacaError("busy", 503)

    with pytest.raises(AlpacaError, match="busy"):
        at.Throttle(busy, 600, sleep=lambda s: None)("u", {})


def test_iter_ticks_pages_and_drops_ticks_at_end():
    fake = FakeAlpaca(page=2)
    fake.add(
        "trades", "SPY", *(trade(et(9, 30, s), i) for i, s in enumerate([0, 1, 2, 59])), trade(et(9, 31), 9)
    )
    out = list(at.iter_ticks("trades", "SPY", et(9, 30), et(9, 31), KEYS, "sip", fake))
    assert [r["i"] for r in out] == [0, 1, 2, 3]
    assert fake.calls[0][1]["start"].endswith("13:30:00Z") and fake.calls[0][1]["feed"] == "sip"
    assert fake.calls[1][1]["page_token"] == "2"


def test_iter_ticks_rejects_unexpected_payload():
    with pytest.raises(AlpacaError, match="unexpected trades"):
        list(at.iter_ticks("trades", "SPY", 0, 1, KEYS, "sip", lambda u, h: {"message": "no"}))


def test_session_from_bars_regular_early_and_closed():
    from flowforge.data.quality import MinuteBar

    regular = at.session_from_bars(DAY, [MinuteBar(et(9, 30), 1, 1), MinuteBar(et(15, 59), 1, 1)])
    assert regular.open == et(9, 30) and regular.close == et(16, 0) and not regular.early_close
    half = at.session_from_bars(DAY, [MinuteBar(et(9, 30), 1, 1), MinuteBar(et(12, 59), 1, 1)])
    assert half.close == et(13, 0) and half.early_close
    assert at.session_from_bars(DAY, [MinuteBar(et(8, 0), 1, 1)]) is None


def test_find_sessions_last_n_skips_weekends_and_holidays():
    fake = FakeAlpaca(page=100)
    for d in (date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 21)):  # 10-20 "holiday"
        fake.add("bars", "SPY", bar(et(10, 0, day=d), 1, 1))
    sessions = at.find_sessions(KEYS, "sip", fake, days=3, before=date(2026, 10, 22))
    assert [s.day for s in sessions] == [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 21)]
    ranged = at.find_sessions(KEYS, "sip", fake, start=date(2026, 10, 17), end=date(2026, 10, 20))
    assert [s.day for s in ranged] == [date(2026, 10, 19)]


def test_ingest_day_writes_catalog_raw_and_clean_report(tmp_path):
    fake = FakeAlpaca()
    fake.add("bars", "SPY", bar(et(9, 30), 2, 300), bar(et(9, 31), 1, 50))
    fake.add(
        "trades",
        "SPY",
        trade(et(9, 30, 1), 1, 100),
        trade(et(9, 30, 2), 2, 200, price=100.005),  # sub-penny midpoint print
        trade(et(9, 30, 3), 3, 7, c=("M",)),  # official close print: not in the bar count
        trade(et(9, 31, 5), 4, 50),
        trade(et(9, 31, 6), 5, 10, u="canceled"),
    )
    fake.add(
        "quotes", "SPY", quote(et(9, 26)), quote(et(9, 30, 10)), quote(et(9, 31, 1)), quote(et(9, 31, 40))
    )
    s = no_sleep_settings(tmp_path, fake, max_quote_silence_seconds=300)
    session = at.Session(DAY, et(9, 30), et(9, 32))
    report = at.ingest_day(s, "SPY", session)

    assert report["status"] == "complete" and report["gaps"] == []
    assert report["bar_check"]["minutes_matched"] == 2
    assert report["trades"]["count"] == 5 and report["trades"]["in_catalog"] == 4
    assert report["trades"]["sub_penny"] == 1 and report["trades"]["updates"] == {"canceled": 1}
    assert report["quotes"]["count"] == 4
    assert report["quote_size_unit"] == "shares"

    ticks = s.catalog.query_trade_ticks(["SPY.XNAS"])
    assert [t.trade_id.value for t in ticks] == ["D-1", "D-2", "D-3", "D-4"]
    assert str(ticks[1].price) == "100.005000"
    quotes = s.catalog.query_quote_ticks(["SPY.XNAS"])
    assert len(quotes) == 4 and quotes[0].bid_size.as_double() == 3

    raw = gzip.open(s.raw_path("SPY", DAY, et(9, 31)), "rt").read().splitlines()
    assert raw[0].startswith("ts_ns,price") and raw[-1].endswith(",canceled")
    assert json.loads(s.report_path("SPY", DAY).read_text())["status"] == "complete"

    # Rerunning the day replaces it rather than doubling it.
    at.ingest_day(s, "SPY", session)
    assert len(s.catalog.query_trade_ticks(["SPY.XNAS"])) == 4


def test_ingest_day_backfills_missing_trades(tmp_path):
    fake = FakeAlpaca()
    fake.add("bars", "SPY", bar(et(9, 30), 3, 300))
    fake.add("trades", "SPY", *(trade(et(9, 30, s), s) for s in (1, 2, 3)))
    fake.add("quotes", "SPY", quote(et(9, 29)), quote(et(9, 30, 20)), quote(et(9, 30, 45)))
    fake.drop_once = {2}
    s = no_sleep_settings(tmp_path, fake)
    report = at.ingest_day(s, "SPY", at.Session(DAY, et(9, 30), et(9, 31)))
    assert report["status"] == "complete"
    assert report["gaps"] == [
        {
            "kind": "trades",
            "start": "2026-10-20T09:30:00-04:00",
            "end": "2026-10-20T09:31:00-04:00",
            "backfilled": True,
            "filled": True,
        }
    ]
    assert len(s.catalog.query_trade_ticks(["SPY.XNAS"])) == 3
    assert "1 gap(s) found and re-downloaded, 1 fixed, 0 still marked" in at.describe(report)


def test_ingest_day_marks_gaps_it_cannot_fill(tmp_path):
    old = date(2025, 6, 2)  # before Alpaca switched quote sizes to shares
    fake = FakeAlpaca()
    fake.add("bars", "SPY", bar(et(9, 30, day=old), 2, 200), bar(et(9, 31, day=old), 1, 100))
    fake.add("trades", "SPY", trade(et(9, 30, 1, day=old), 1), trade(et(9, 31, 1, day=old), 2))
    fake.add("quotes", "SPY", quote(et(9, 30, 1, day=old)))  # then silent for over a minute
    s = no_sleep_settings(tmp_path, fake)
    report = at.ingest_day(s, "SPY", at.Session(old, et(9, 30, day=old), et(9, 32, day=old)))
    assert report["status"] == "gaps"
    kinds = [(g["kind"], g["filled"]) for g in report["gaps"]]
    assert ("trades", False) in kinds and ("quotes", False) in kinds
    assert report["bar_check"]["missing_trades_after_backfill"] == 1
    assert report["quote_size_unit"] == "round lots x100"


def test_ingest_window_with_no_ticks_writes_nothing(tmp_path):
    fake = FakeAlpaca()
    s = no_sleep_settings(tmp_path, fake)
    report = at.ingest_day(s, "SPY", at.Session(DAY, et(9, 30), et(9, 31)))
    assert report["trades"]["count"] == 0 and report["quotes"]["count"] == 0
    assert s.catalog.query_trade_ticks(["SPY.XNAS"]) == []


def test_download_skips_done_days_and_writes_summary(tmp_path, capsys):
    fake = full_day_fake()
    s = no_sleep_settings(tmp_path, fake, window_minutes=390)
    session = at.Session(DAY, et(9, 30), et(16, 0))
    first = at.download(s, ["SPY"], [session])
    calls = len(fake.calls)
    again = at.download(s, ["SPY"], [session])
    assert len(fake.calls) == calls and again == first
    assert "already downloaded" in capsys.readouterr().out
    summary = at.write_summary(tmp_path, first).read_text()
    assert "SPY 2026-10-20" in summary


def test_main_end_to_end(monkeypatch, tmp_path, capsys):
    fake = full_day_fake()
    monkeypatch.setenv("ALPACA_API_KEY_ID", "id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setattr(at, "_http_get_json", fake)
    monkeypatch.setattr(
        at.Throttle, "__init__", lambda self, fetch, cpm: self.__dict__.update(_f=fetch, calls=0)
    )
    monkeypatch.setattr(at.Throttle, "__call__", lambda self, u, h: self._f(u, h))
    code = at.main(
        ["--symbols", "spy", "--start", "2026-10-20", "--end", "2026-10-20", "--out", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert "Trading days: 2026-10-20" in out
    # The fake has bars but no trades, so the bar check reports trades missing.
    assert code == 2 and "still have trade gaps" in out


def test_main_errors(monkeypatch, capsys):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    assert at.main([]) == 1
    assert "ALPACA_API_KEY_ID" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        at.main(["--start", "2026-10-20"])
    monkeypatch.setenv("ALPACA_API_KEY_ID", "id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setattr(at, "find_sessions", lambda *a, **k: [])
    assert at.main([]) == 1
    assert "no trading days" in capsys.readouterr().err


def test_backfill_window_replaces_a_window_in_the_catalog(tmp_path):
    fake = FakeAlpaca()
    fake.add("bars", "SPY", bar(et(10, 0), 2, 200), bar(et(10, 1), 1, 100))
    fake.add("trades", "SPY", trade(et(10, 0, 5), 1), trade(et(10, 0, 9), 2), trade(et(10, 1, 3), 3))
    fake.add(
        "quotes", "SPY", quote(et(10, 0, 1)), quote(et(10, 0, 30)), quote(et(10, 1, 0)), quote(et(10, 1, 30))
    )
    s = no_sleep_settings(tmp_path, fake)
    result = at.backfill_window(s, "SPY", et(10, 0, 20), et(10, 1, 10))  # widened to 10:00-10:02
    assert result["start"].endswith("10:00:00-04:00") and result["end"].endswith("10:02:00-04:00")
    assert result["trades"] == 3 and result["quotes"] == 4 and result["short_minutes"] == 0
    at.backfill_window(s, "SPY", et(10, 0, 20), et(10, 1, 10))
    assert len(s.catalog.query_trade_ticks(["SPY.XNAS"])) == 3
    assert list((tmp_path / "raw" / "SPY" / DAY.isoformat()).glob("backfill_*.csv.gz"))
