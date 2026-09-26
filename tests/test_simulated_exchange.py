"""Order-matching semantics of the simulated exchange (shared by backtest and paper trading)."""

from __future__ import annotations

import numpy as np
import pytest

from tradebot.core.types import ExchangeType, Order, OrderRole, OrderStatus, OrderType, Side
from tradebot.execution.simulated import ExecutionConfig, SimulatedExchange


def bar(o, h, low, c, ts=0):
    return np.array([ts, o, h, low, c, 1.0])


def make_ex(**kw) -> SimulatedExchange:
    cfg = ExecutionConfig(**{"fee_maker": 0.001, "fee_taker": 0.002, "slippage_bps": 10, **kw})
    ex = SimulatedExchange(cfg)
    ex.mark("X", 100.0)
    return ex


def test_market_order_fills_at_last_price_plus_slippage_with_taker_fee():
    ex = make_ex()
    o = ex.submit(Order("X", Side.BUY, OrderType.MARKET, 10))
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx(100.0 * 1.001)
    assert o.fee == pytest.approx(10 * 100.1 * 0.002)
    assert ex.position("X").qty == pytest.approx(10)
    # equity = balance - fee + unrealized (marked at 100 < fill price)
    assert ex.equity() == pytest.approx(10_000 - o.fee + 10 * (100 - 100.1))


def test_limit_buy_fills_only_when_touched_at_limit_price_as_maker():
    ex = make_ex()
    o = ex.submit(Order("X", Side.BUY, OrderType.LIMIT, 1, 95.0))
    assert o.status is OrderStatus.OPEN
    ex.process_bar("X", bar(100, 101, 96, 99))
    assert o.status is OrderStatus.OPEN  # low 96 never reached 95
    ex.process_bar("X", bar(99, 99.5, 94, 98))
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx(95.0)
    assert o.fee == pytest.approx(95.0 * 0.001)


def test_buy_stop_triggers_on_breakout_with_slippage():
    ex = make_ex()
    o = ex.submit(Order("X", Side.BUY, OrderType.STOP, 1, 105.0))
    ex.process_bar("X", bar(100, 104, 99, 103))
    assert o.is_active
    ex.process_bar("X", bar(103, 108, 102, 107))
    assert o.is_filled
    assert o.avg_fill_price == pytest.approx(105.0 * 1.001)


def test_gap_through_stop_fills_at_open_not_stop_price():
    ex = make_ex()
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 1))
    sl = ex.submit(Order("X", Side.SELL, OrderType.STOP, 1, 95.0, role=OrderRole.STOP_LOSS, reduce_only=True))
    ex.process_bar("X", bar(90, 92, 88, 91))  # gaps below the stop
    assert sl.is_filled
    assert sl.avg_fill_price == pytest.approx(90 * (1 - 0.001))


def test_gap_through_limit_gives_price_improvement():
    ex = make_ex()
    lim = ex.submit(Order("X", Side.BUY, OrderType.LIMIT, 1, 95.0))
    ex.process_bar("X", bar(90, 93, 89, 92))
    assert lim.avg_fill_price == pytest.approx(90.0)


def test_adverse_extreme_first_when_stop_and_target_in_same_bar():
    ex = make_ex()
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 1))
    sl = ex.submit(Order("X", Side.SELL, OrderType.STOP, 1, 95.0, role=OrderRole.STOP_LOSS, reduce_only=True))
    tp = ex.submit(
        Order("X", Side.SELL, OrderType.LIMIT, 1, 110.0, role=OrderRole.TAKE_PROFIT, reduce_only=True)
    )
    ex.process_bar("X", bar(100, 112, 94, 111))  # up bar -> path O, L, H, C : stop first
    assert sl.is_filled
    assert tp.status is OrderStatus.CANCELED  # reduce-only order cancelled once flat
    assert not ex.position("X").is_open


def test_sub_bars_resolve_true_intrabar_order():
    ex = make_ex()
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 1))
    sl = ex.submit(Order("X", Side.SELL, OrderType.STOP, 1, 95.0, role=OrderRole.STOP_LOSS, reduce_only=True))
    tp = ex.submit(
        Order("X", Side.SELL, OrderType.LIMIT, 1, 110.0, role=OrderRole.TAKE_PROFIT, reduce_only=True)
    )
    sub = np.array(
        [
            [0, 100, 105, 99, 104, 1],
            [60_000, 104, 111, 103, 110, 1],  # target hit first
            [120_000, 110, 110, 94, 96, 1],
        ]
    )
    ex.process_bar("X", bar(100, 111, 94, 96), sub_bars=sub)
    assert tp.is_filled
    assert sl.status is OrderStatus.CANCELED


def test_spot_rejects_short_selling_and_leverage():
    ex = make_ex()
    o = ex.submit(Order("X", Side.SELL, OrderType.MARKET, 1))
    assert o.status is OrderStatus.REJECTED
    with pytest.raises(ValueError):
        ExecutionConfig(exchange_type=ExchangeType.SPOT, leverage=2)


def test_insufficient_margin_is_rejected():
    ex = make_ex()
    o = ex.submit(Order("X", Side.BUY, OrderType.MARKET, 1000))  # $100k notional on $10k
    assert o.status is OrderStatus.REJECTED
    assert "insufficient margin" in o.reject_reason


def test_futures_short_pnl_and_funding():
    ex = make_ex(exchange_type=ExchangeType.FUTURES, leverage=3, fee_maker=0, fee_taker=0, slippage_bps=0)
    ex.submit(Order("X", Side.SELL, OrderType.MARKET, 10))
    ex.mark("X", 90.0)
    assert ex.equity() == pytest.approx(10_000 + 100)
    paid = ex.apply_funding("X", 0.001)  # shorts receive when funding > 0
    assert paid == pytest.approx(-0.9)
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 10, reduce_only=True))
    t = ex.closed_trades[-1]
    assert t.side == "short"
    assert t.pnl == pytest.approx(100 + 0.9)


def test_liquidation_when_equity_below_maintenance():
    ex = make_ex(exchange_type=ExchangeType.FUTURES, leverage=10, fee_maker=0, fee_taker=0, slippage_bps=0)
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 900))  # $90k notional on $10k
    ex.process_bar("X", bar(100, 100, 88, 89))
    assert ex.liquidated
    assert not ex.position("X").is_open


def test_partial_exits_average_prices_and_trade_record():
    ex = make_ex(fee_maker=0, fee_taker=0, slippage_bps=0)
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 10))
    ex.mark("X", 110)
    ex.submit(Order("X", Side.SELL, OrderType.MARKET, 5, reduce_only=True))
    ex.mark("X", 120)
    ex.submit(Order("X", Side.SELL, OrderType.MARKET, 5, reduce_only=True))
    t = ex.closed_trades[-1]
    assert t.entry_price == pytest.approx(100)
    assert t.exit_price == pytest.approx(115)
    assert t.pnl == pytest.approx(150)
    assert t.return_pct == pytest.approx(15)
    assert ex.balance == pytest.approx(10_150)


def test_reversal_closes_trade_and_opens_opposite_side():
    ex = make_ex(exchange_type=ExchangeType.FUTURES, leverage=2, fee_maker=0, fee_taker=0, slippage_bps=0)
    ex.submit(Order("X", Side.BUY, OrderType.MARKET, 10))
    ex.mark("X", 105)
    ex.submit(Order("X", Side.SELL, OrderType.MARKET, 25))
    assert ex.closed_trades[-1].pnl == pytest.approx(50)
    pos = ex.position("X")
    assert pos.qty == pytest.approx(-15)
    assert pos.entry_price == pytest.approx(105)
