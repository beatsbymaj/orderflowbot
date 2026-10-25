import io
import json
import urllib.error
from datetime import date

import pytest

from flowforge.data import alpaca_bars as ab

KEYS = ab.AlpacaKeys("id", "secret")


def _bar(t, c=100.0):
    return {"t": t, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 500, "n": 3, "vw": c}


def _pages(*pages):
    calls = []

    def fetch(url, headers):
        calls.append((url, headers))
        return pages[len(calls) - 1]

    return fetch, calls


def test_keys_from_env_and_repr_hides_secret():
    keys = ab.AlpacaKeys.from_env({"ALPACA_API_KEY_ID": " k ", "ALPACA_API_SECRET_KEY": "s"})
    assert keys == ab.AlpacaKeys("k", "s")
    assert repr(keys) == "AlpacaKeys(key_id=***, secret=***)"
    with pytest.raises(ab.AlpacaError, match="ALPACA_API_KEY_ID"):
        ab.AlpacaKeys.from_env({"ALPACA_API_KEY_ID": "k"})


def test_iter_bars_follows_page_tokens_and_sends_keys():
    fetch, calls = _pages(
        {"bars": {"SPY": [_bar("2025-01-02T14:30:00Z")]}, "next_page_token": "abc"},
        {"bars": {"SPY": [_bar("2025-01-02T14:31:00Z")]}, "next_page_token": None},
    )
    sleeps = []
    out = list(ab.iter_bars("SPY", date(2025, 1, 1), date(2025, 1, 3), KEYS, "sip", fetch, sleeps.append))
    assert [b["t"] for b in out] == ["2025-01-02T14:30:00Z", "2025-01-02T14:31:00Z"]
    assert "feed=sip" in calls[0][0] and "page_token" not in calls[0][0]
    assert "page_token=abc" in calls[1][0]
    assert calls[0][1] == {"APCA-API-KEY-ID": "id", "APCA-API-SECRET-KEY": "secret"}
    assert sleeps == [ab.MIN_SECONDS_BETWEEN_CALLS]


def test_iter_bars_rejects_unexpected_payload():
    fetch, _ = _pages({"message": "forbidden"})
    with pytest.raises(ab.AlpacaError, match="unexpected response"):
        list(ab.iter_bars("SPY", date(2025, 1, 1), date(2025, 1, 2), KEYS, fetch=fetch))


def test_download_writes_csv_and_manifest(tmp_path):
    fetch, _ = _pages({"bars": {"SPY": [_bar("2025-01-02T14:30:00Z", 101.5)]}})
    manifest = ab.download(["SPY"], date(2025, 1, 1), date(2025, 1, 3), tmp_path, KEYS, fetch=fetch)
    lines = (tmp_path / "SPY_1min_sip.csv").read_text().splitlines()
    assert lines[0] == "ts_utc,open,high,low,close,volume"
    assert lines[1] == "2025-01-02T14:30:00Z,101.5,102.5,100.5,101.5,500"
    assert manifest["files"]["SPY"]["rows"] == 1
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved["feed"] == "sip" and "secret" not in json.dumps(saved)


def test_download_fails_when_no_bars(tmp_path):
    fetch, _ = _pages({"bars": {}})
    with pytest.raises(ab.AlpacaError, match="no bars"):
        ab.download(["SPY"], date(2025, 1, 1), date(2025, 1, 2), tmp_path, KEYS, fetch=fetch)


def test_http_get_json_success_and_http_error(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ab.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"bars": {}}'))
    assert ab._http_get_json("https://example.test", {}) == {"bars": {}}

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"subscription"))

    monkeypatch.setattr(ab.urllib.request, "urlopen", boom)
    with pytest.raises(ab.AlpacaError, match="HTTP 403: subscription"):
        ab._http_get_json("https://example.test", {})


def test_main_reports_missing_keys(monkeypatch, capsys):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert ab.main([]) == 1
    assert "ALPACA_API_KEY_ID" in capsys.readouterr().err


def test_main_passes_arguments(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setattr(ab, "download", lambda *a: seen.setdefault("args", a))
    assert ab.main(["--symbols", "spy, qqq", "--start", "2025-01-01", "--out", str(tmp_path)]) == 0
    symbols, start, _end, out, _keys, feed = seen["args"]
    assert symbols == ["SPY", "QQQ"] and start == date(2025, 1, 1) and out == tmp_path and feed == "sip"
