from __future__ import annotations

import pytest

from tradebot.backtest.engine import BacktestConfig, run_backtest
from tradebot.core.types import ClosedTrade, Order, OrderType, Position, Side
from tradebot.execution.simulated import ExecutionConfig, SimulatedExchange
from tradebot.risk.manager import KillSwitch, RiskGuardedBroker, RiskLimits, RiskManager
from tradebot.risk.protections import CooldownPeriod, StoplossGuard, build_protections

from .conftest import make_candles

DAY = 86_400_000


def _check(rm, order, equity=10_000, positions=None, price=100.0, now=0):
    return rm.check(order, equity=equity, positions=positions or {}, last_price=price, now=now)


def test_halted_blocks_entries_but_allows_exits():
    rm = RiskManager()
    rm.halt("test")
    assert not _check(rm, Order("X", Side.BUY, OrderType.MARKET, 1)).approved
    pos = {"X": Position("X", qty=5, entry_price=100, last_price=100)}
    assert _check(rm, Order("X", Side.SELL, OrderType.MARKET, 5), positions=pos).approved
    assert _check(rm, Order("X", Side.SELL, OrderType.MARKET, 5, reduce_only=True)).approved


def test_max_drawdown_trips_kill_switch_and_needs_manual_rearm():
    rm = RiskManager(RiskLimits(max_drawdown_pct=20))
    rm.update_equity(10_000, 0)
    rm.update_equity(7_900, DAY // 2)
    assert rm.state is KillSwitch.HALTED
    rm.update_equity(9_000, 2 * DAY)  # recovery does not auto-resume
    assert rm.state is KillSwitch.HALTED
    rm.resume("tester", 2 * DAY)
    assert rm.state is KillSwitch.RUNNING


def test_daily_loss_pause_clears_next_day():
    rm = RiskManager(RiskLimits(daily_loss_limit_pct=5, max_drawdown_pct=90))
    rm.update_equity(10_000, 0)
    rm.update_equity(9_400, 3_600_000)
    assert rm.state is KillSwitch.PAUSED
    rm.update_equity(9_400, DAY + 1)
    assert rm.state is KillSwitch.RUNNING


def test_position_and_gross_limits_clip_quantity():
    rm = RiskManager(RiskLimits(max_position_pct=0.5, max_gross_exposure=0.8, min_order_notional=1))
    d = _check(rm, Order("X", Side.BUY, OrderType.MARKET, 100))  # $10k notional on $10k equity
    assert d.approved and d.qty == pytest.approx(50)
    pos = {"Y": Position("Y", qty=60, entry_price=100, last_price=100)}  # $6k gross already
    d = _check(rm, Order("X", Side.BUY, OrderType.MARKET, 100), positions=pos)
    assert d.approved and d.qty == pytest.approx(20)


def test_fat_finger_band_and_min_notional():
    rm = RiskManager(RiskLimits(max_price_deviation_pct=5))
    assert not _check(rm, Order("X", Side.BUY, OrderType.LIMIT, 1, 80.0)).approved
    assert not _check(rm, Order("X", Side.BUY, OrderType.MARKET, 0.01)).approved  # $1 < $10


def test_rate_limit_halts_runaway_loops():
    rm = RiskManager(RiskLimits(max_orders_per_minute=3))
    for _ in range(3):
        assert _check(rm, Order("X", Side.BUY, OrderType.MARKET, 1)).approved
    assert not _check(rm, Order("X", Side.BUY, OrderType.MARKET, 1)).approved
    assert rm.state is KillSwitch.HALTED


def _trade(sym, pnl, reason, closed_at):
    return ClosedTrade("t", sym, "long", 1, 100, 99, closed_at - 1000, closed_at, pnl, 0, 0, pnl, reason)


def test_protections():
    cd = CooldownPeriod(stop_minutes=60)
    cd.on_trade(_trade("X", 5, "close", 0))
    assert cd.is_locked("X", 30 * 60_000)[0]
    assert not cd.is_locked("X", 61 * 60_000)[0]
    assert not cd.is_locked("Y", 1)[0]
    sg = StoplossGuard(lookback_minutes=600, trade_limit=2, stop_minutes=60)
    sg.on_trade(_trade("X", -5, "stop_loss", 0))
    sg.on_trade(_trade("Y", -5, "stop_loss", 60_000))
    assert sg.is_locked("Z", 120_000)[0]
    assert len(build_protections([{"method": "CooldownPeriod", "stop_minutes": 10}])) == 1
    with pytest.raises(ValueError):
        build_protections([{"method": "Nope"}])


def test_guarded_broker_rejects_via_risk():
    ex = SimulatedExchange(ExecutionConfig())
    ex.mark("X", 100)
    rm = RiskManager()
    rm.halt("x")
    b = RiskGuardedBroker(ex, rm)
    o = b.submit(Order("X", Side.BUY, OrderType.MARKET, 1))
    assert o.status.value == "rejected" and o.reject_reason.startswith("risk:")
    assert b.equity() == ex.equity()  # delegation


def test_backtest_with_risk_wrapped_exchange_still_runs():
    # strategies are unaware of the wrapper: same API
    res = run_backtest(
        __import__("tradebot.strategies", fromlist=["TrendVolTarget"]).TrendVolTarget,
        make_candles([100 + i for i in range(400)]),
        "1d",
        config=BacktestConfig(),
    )
    assert res.metrics["total_trades"] >= 0
