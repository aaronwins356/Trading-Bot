"""REST API end-to-end: bots lifecycle, backtest jobs, kill switch, research, auth."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from tradebot.api.app import create_app
from tradebot.config import AppSettings
from tradebot.data.store import DataStore
from tradebot.live.manager import BotManager
from tradebot.storage.db import Database

from .conftest import gbm, make_candles


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADEBOT_API_TOKEN", raising=False)
    store = DataStore(tmp_path / "data")
    store.save("bitstamp", "BTC/USD", "1d", make_candles(gbm(700, mu=0.001, sigma=0.03, seed=4), spread=0.01))
    results = tmp_path / "results"
    results.mkdir()
    (results / "summary.json").write_text(
        json.dumps(
            {"generated_at": "2026-01-01", "studies": [], "strategy_portfolio": None, "methodology": ""}
        )
    )
    settings = AppSettings(data_dir=str(tmp_path / "data"), results_dir=str(results))
    mgr = BotManager(settings, db=Database(f"sqlite:///{tmp_path / 'api.db'}"), store=store)
    with TestClient(create_app(settings, manager=mgr, results_dir=results)) as c:
        yield c


def _wait(fn, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_health_and_strategies(client):
    assert client.get("/api/health").json()["ok"] is True
    names = {s["name"] for s in client.get("/api/strategies").json()}
    assert {"TrendVolTarget", "DonchianBreakout", "JEVStrategy", "MultiAssetTrend"} <= names
    assert client.get("/api/research").json()["studies"] == []
    assert client.get("/api/research/nope").status_code == 404


def test_bot_lifecycle_and_views(client):
    cfg = {
        "id": "api-bot",
        "name": "API bot",
        "strategy": "TrendVolTarget",
        "symbols": ["BTC/USD"],
        "timeframe": "1d",
        "mode": "replay",
        "warmup_bars": 300,
        "replay": {"exchange": "bitstamp", "speed": 0},
    }
    assert client.post("/api/bots", json=cfg).status_code == 200
    assert client.post("/api/bots", json=cfg).status_code == 409  # duplicate id
    client.post("/api/bots/api-bot/start", json={"fresh": True})
    _wait(lambda: client.get("/api/bots/api-bot").json()["status"] == "finished")
    bot = client.get("/api/bots/api-bot").json()
    assert bot["bars_processed"] > 300 and bot["config"]["strategy"] == "TrendVolTarget"
    assert client.get("/api/bots/api-bot/equity").json()
    assert isinstance(client.get("/api/bots/api-bot/trades").json(), list)
    assert client.get("/api/bots/api-bot/orders").json()
    candles = client.get("/api/bots/api-bot/candles?limit=50").json()
    assert len(candles["candles"]) == 50
    ov = client.get("/api/overview").json()
    assert ov["bots_total"] == 1
    # kill switch applies to stopped bots through persisted state
    assert client.post("/api/kill-switch", json={"reason": "test"}).json()["bots_halted"] == 1
    assert client.get("/api/bots/api-bot").json()["risk"]["state"] == "halted"
    assert client.post("/api/bots/api-bot/command", json={"command": "resume"}).status_code == 200
    assert client.post("/api/bots/api-bot/command", json={"command": "explode"}).status_code == 400
    assert client.delete("/api/bots/api-bot").json()["ok"] is True
    assert client.get("/api/bots/api-bot").status_code == 404


def test_live_bot_requires_keys(client):
    cfg = {
        "id": "live-bot",
        "name": "x",
        "strategy": "TrendVolTarget",
        "mode": "live",
        "exchange": {"id": "binance", "api_key_env": "NOPE_KEY", "secret_env": "NOPE_SECRET"},
    }
    r = client.post("/api/bots", json=cfg)
    assert r.status_code == 400 and "API keys" in r.json()["detail"]


def test_backtest_job(client):
    bid = client.post(
        "/api/backtests", json={"strategy": "DonchianBreakout", "timeframe": "1d", "start": None}
    ).json()["id"]
    row = _wait(
        lambda: (r := client.get(f"/api/backtests/{bid}").json())["status"] in ("done", "error") and r
    )
    assert row["status"] == "done", row.get("error")
    res = row["result"]
    assert res["equity"] and res["candles"] and "sharpe" in res["metrics"]
    assert client.get("/api/backtests").json()[0]["id"] == bid
    assert client.post("/api/backtests", json={"strategy": "NoSuchStrategy"}).status_code == 400


def test_jev_ask_falls_back_without_llm(client):
    r = client.post(
        "/api/jev/ask",
        json={"exchange": "bitstamp", "symbol": "BTC/USD", "timeframe": "1d", "jev": {"enabled": False}},
    ).json()
    assert r["decision"]["source"] == "consensus"
    assert -1 <= r["decision"]["final_exposure"] <= 1
    assert len(r["context"]["quant_analysts"]) == 4


def test_token_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEBOT_API_TOKEN", "s3cret")
    settings = AppSettings(data_dir=str(tmp_path), db_url=f"sqlite:///{tmp_path / 'a.db'}")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").status_code == 200  # health stays public
        assert c.get("/api/bots").status_code == 401
        assert c.get("/api/bots", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/bots", headers={"Authorization": "Bearer s3cret"}).status_code == 200
