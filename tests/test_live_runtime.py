"""Live runtime: replay mode end-to-end, persistence, restart, commands, and
backtest <-> live parity (the same strategy must produce the same trades)."""

from __future__ import annotations

import numpy as np
import pytest

from tradebot.backtest.engine import BacktestConfig, run_backtest
from tradebot.config import AppSettings, BotConfig, CostSettings, ReplaySettings, RiskSettings
from tradebot.data.store import DataStore
from tradebot.live.manager import BotManager
from tradebot.live.runner import BotRunner
from tradebot.storage.db import Database
from tradebot.strategies import DonchianBreakout, TrendVolTarget

from .conftest import gbm, make_candles


@pytest.fixture
def env(tmp_path):
    store = DataStore(tmp_path / "data")
    candles = make_candles(gbm(900, mu=0.001, sigma=0.03, seed=11), spread=0.01)
    store.save("bitstamp", "BTC/USD", "1d", candles)
    db = Database(f"sqlite:///{tmp_path / 'db.sqlite'}")
    return store, db, candles


def _cfg(strategy="TrendVolTarget", **kw) -> BotConfig:
    return BotConfig(
        id=kw.pop("id", "t1"),
        name="test",
        strategy=strategy,
        symbols=["BTC/USD"],
        timeframe="1d",
        mode="replay",
        capital=10_000,
        warmup_bars=300,
        costs=CostSettings(fee_maker=0.001, fee_taker=0.001, slippage_bps=5),
        risk=RiskSettings(max_drawdown_pct=95, daily_loss_limit_pct=50, max_price_deviation_pct=50),
        replay=kw.pop("replay", ReplaySettings(exchange="bitstamp", speed=0.0)),
        **kw,
    )


@pytest.mark.parametrize("strategy_cls", [TrendVolTarget, DonchianBreakout])
def test_replay_matches_backtest(env, strategy_cls):
    store, db, candles = env
    cfg = _cfg(strategy_cls.__name__)
    runner = BotRunner(cfg, db, store=store, fresh=True)
    runner.start()
    runner.join(120)
    assert runner.status == "finished", runner.error
    live_trades = db.trades("t1")
    # backtest over exactly the replayed window (warm-up candles only feed indicators)
    start_ts = int(candles[300, 0])
    bt = run_backtest(
        strategy_cls,
        candles,
        "1d",
        symbol="BTC/USD",
        start=start_ts,
        config=BacktestConfig(fee_maker=0.001, fee_taker=0.001, slippage_bps=5, close_at_end=False),
    )
    assert len(live_trades) == len(bt.trades) > 0
    for lt, bt_t in zip(sorted(live_trades, key=lambda t: t["closed_at"]), bt.trades, strict=True):
        assert lt["entry_price"] == pytest.approx(bt_t.entry_price, rel=1e-9)
        assert lt["exit_price"] == pytest.approx(bt_t.exit_price, rel=1e-9)
        assert lt["pnl"] == pytest.approx(bt_t.pnl, rel=1e-6, abs=1e-6)
    eq = db.equity("t1", max_points=100_000)
    assert eq[-1]["equity"] == pytest.approx(float(bt.equity.iloc[-1]), rel=1e-6)


def test_state_persists_and_restart_continues(env):
    store, db, _ = env
    cfg = _cfg(replay=ReplaySettings(exchange="bitstamp", speed=0.0))
    r1 = BotRunner(cfg, db, store=store, fresh=True)
    r1.start()
    r1.join(60)
    state = db.load_state("t1")
    assert state and state["bars_processed"] > 0
    assert "exchange" in state and "risk" in state
    # a restarted runner resumes from the saved replay index: nothing left to process
    r2 = BotRunner(cfg, db, store=store)
    r2.start()
    r2.join(60)
    assert r2.status == "finished"
    assert r2.bars_processed == 0


def test_kill_switch_command_blocks_new_entries(env):
    store, db, _ = env
    cfg = _cfg("TrendVolTarget", id="t2")
    r = BotRunner(cfg, db, store=store, fresh=True)
    r.command("halt", "test halt")  # applied to persisted state before start
    r.start()
    r.join(60)
    assert r.risk is not None and r.risk.state.value == "halted"
    orders = db.orders("t2", limit=10_000)
    assert orders and all(o["status"] == "rejected" for o in orders if o["role"] in ("entry", "increase"))


def test_manager_crud_and_status(env, tmp_path):
    store, db, _ = env
    settings = AppSettings(data_dir=str(tmp_path / "data"))
    m = BotManager(settings, db=db, store=store)
    cfg = m.save_config(_cfg(id="m1"))
    assert [c.id for c in m.configs()] == ["m1"]
    r = m.start(cfg.id, fresh=True)
    r.join(60)
    st = m.status("m1")
    assert st["status"] in ("finished", "stopped")
    assert "equity" in st
    m.delete("m1")
    assert m.configs() == []


def test_jev_strategy_runs_in_replay_consensus_mode(env):
    store, db, _ = env
    cfg = _cfg("JEVStrategy", id="j1")
    r = BotRunner(cfg, db, store=store, fresh=True)
    r.start()
    r.join(120)
    assert r.status == "finished", r.error
    decisions = db.decisions("j1", limit=5)
    assert decisions and decisions[0]["authority"] in ("veto", "advisory", "full")
    assert all(-1.0 <= d["final_exposure"] <= 1.0 for d in decisions)
    assert np.isfinite(db.equity("j1")[-1]["equity"])
