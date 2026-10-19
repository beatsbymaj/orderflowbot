import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowforge.config.risk_limits import load_risk_limits
from flowforge.nautilus_config import risk_engine_config
from flowforge.ops import ib_paper_check as chk

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"
PAPER = chk.IbSettings(
    host="127.0.0.1", port=4002, client_id=91, account_id="DU1234567", trading_mode="paper"
)


def test_mask_account_hides_the_middle():
    assert chk.mask_account("DU1234567") == "DU*****67"
    assert chk.mask_account("U12") == "***"


def test_paper_settings_pass():
    chk.assert_paper(PAPER)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"trading_mode": "live"}, "IB_TRADING_MODE"),
        ({"port": 4001}, "not a paper port"),
        ({"port": 7496}, "not a paper port"),
        ({"account_id": "U1234567"}, "does not start with 'DU'"),
    ],
)
def test_anything_live_looking_is_refused(change, message):
    settings = chk.IbSettings(**{**PAPER.__dict__, **change})
    with pytest.raises(chk.NotPaperError, match=message):
        chk.assert_paper(settings)


def test_refusal_message_never_contains_full_account_id():
    settings = chk.IbSettings(**{**PAPER.__dict__, "account_id": "U7654321"})
    with pytest.raises(chk.NotPaperError) as err:
        chk.assert_paper(settings)
    assert "U7654321" not in str(err.value)


def test_settings_from_env_defaults_to_paper():
    s = chk.settings_from_env({"IB_ACCOUNT_ID": "DU1"})
    assert (s.host, s.port, s.trading_mode) == ("127.0.0.1", 4002, "paper")


def test_settings_from_env_requires_account():
    with pytest.raises(chk.NotPaperError, match="IB_ACCOUNT_ID"):
        chk.settings_from_env({})


def test_main_refuses_live_before_connecting(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IB_ACCOUNT_ID", "U1234567")
    monkeypatch.setenv("IB_TRADING_MODE", "live")
    monkeypatch.setattr(chk, "build_node", lambda *a: pytest.fail("must not build a node"))
    assert chk.main(["--out", str(tmp_path)]) == 2
    assert "refusing to connect" in capsys.readouterr().err


class _FakeAccount:
    def balances_total(self):
        return {"USD": Decimal("100000.00")}

    def balances_free(self):
        return {"USD": Decimal("99000.00")}


class _FakeCache:
    def __init__(self, account):
        self._account = account

    def account_for_venue(self, venue):
        assert str(venue) == "IB"
        return self._account

    def positions_open(self):
        return [SimpleNamespace(instrument_id="AAPL.NASDAQ", side="LONG", quantity=Decimal("10"))]

    def orders_open(self):
        return []


def test_summarize_cache_reads_account_positions_orders():
    report = chk.CheckReport(started_at="t", account="DU***67")
    chk.summarize_cache(_FakeCache(_FakeAccount()), report)
    assert report.ok
    assert report.balances == [{"currency": "USD", "total": "100000.00", "free": "99000.00"}]
    assert report.open_positions == [{"instrument": "AAPL.NASDAQ", "side": "LONG", "quantity": "10"}]
    assert report.open_orders == []


def test_summarize_cache_flags_missing_account():
    report = chk.CheckReport(started_at="t", account="x")
    chk.summarize_cache(_FakeCache(None), report)
    assert not report.ok
    assert report.problems == ["no IB account state received"]


def test_build_node_has_no_strategies_and_risk_engine_on():
    node = chk.build_node(PAPER, REPO_CONFIG)
    try:
        assert not node.is_running
        assert node.cache.strategy_ids() == []
    finally:
        node.dispose()


def test_risk_engine_is_never_bypassed():
    cfg = risk_engine_config(load_risk_limits(REPO_CONFIG).limits)
    assert cfg.bypass is False
    assert cfg.max_order_submit_rate == "5/00:00:01"


def test_main_writes_report(monkeypatch, tmp_path):
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    fake = chk.CheckReport(started_at="2026-10-19T20:00:00+00:00", account="DU*****67", connected=True)
    monkeypatch.setattr(chk, "run_check", lambda *a: fake)
    assert chk.main(["--out", str(tmp_path)]) == 0
    written = json.loads(next(tmp_path.iterdir()).read_text())
    assert written["ok"] is True and written["account"] == "DU*****67"


def test_main_returns_1_when_check_has_problems(monkeypatch, tmp_path):
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    bad = chk.CheckReport(started_at="2026-10-19T20:00:00+00:00", account="x", problems=["boom"])
    monkeypatch.setattr(chk, "run_check", lambda *a: bad)
    assert chk.main(["--out", str(tmp_path)]) == 1


class _FakeNode:
    def __init__(self, cache, fail=False):
        self.cache = cache
        self._fail = fail
        self.stopped = self.disposed = False

    async def run_async(self):
        if self._fail:
            raise ConnectionError("gateway not reachable")

    def handle(self):
        return SimpleNamespace(stop=lambda: setattr(self, "stopped", True))

    def dispose(self):
        self.disposed = True


def test_run_check_connects_reports_and_disposes(monkeypatch):
    node = _FakeNode(_FakeCache(_FakeAccount()))
    monkeypatch.setattr(chk, "build_node", lambda *a: node)
    report = chk.run_check(PAPER, 0, REPO_CONFIG)
    assert report.ok and node.stopped and node.disposed
    assert report.account == "DU*****67"


def test_run_check_reports_connection_failure(monkeypatch):
    node = _FakeNode(_FakeCache(_FakeAccount()), fail=True)
    monkeypatch.setattr(chk, "build_node", lambda *a: node)
    report = chk.run_check(PAPER, 0, REPO_CONFIG)
    assert not report.ok and node.disposed
    assert report.problems == ["ConnectionError: gateway not reachable"]


def test_run_check_refuses_live(monkeypatch):
    monkeypatch.setattr(chk, "build_node", lambda *a: pytest.fail("must not build a node"))
    with pytest.raises(chk.NotPaperError):
        chk.run_check(chk.IbSettings(**{**PAPER.__dict__, "port": 4001}), 0, REPO_CONFIG)
