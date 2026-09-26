"""Jesse-style lifecycle: entries, stop-loss / take-profit placement, trailing modifications,
entry cancellation, filters, liquidate(), and the no-look-ahead guarantee."""

from __future__ import annotations

import numpy as np
import pytest

from tradebot.backtest.engine import BacktestConfig, run_backtest
from tradebot.core.types import OrderRole
from tradebot.strategy.base import ConflictingRules, InvalidStrategy, Strategy

from .conftest import make_candles

CFG = BacktestConfig(fee_maker=0.0, fee_taker=0.0, slippage_bps=0.0)


class BuyOnceWithBrackets(Strategy):
    def should_long(self):
        return self.index == 0

    def go_long(self):
        self.buy = 10, self.price
        self.stop_loss = 10, self.price * 0.9
        self.take_profit = 10, self.price * 1.2


def test_market_entry_places_brackets_and_take_profit_exits():
    closes = [100, 101, 105, 110, 125, 130]
    res = run_backtest(BuyOnceWithBrackets, make_candles(closes), "1d", config=CFG)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_price == pytest.approx(100)
    assert t.exit_price == pytest.approx(120)  # limit fills at its price
    assert t.exit_reason.startswith("take_profit")
    roles = {o.role for o in res.orders}
    assert {OrderRole.ENTRY, OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT} <= roles


def test_stop_loss_exit():
    closes = [100, 99, 95, 85, 80]
    res = run_backtest(BuyOnceWithBrackets, make_candles(closes), "1d", config=CFG)
    t = res.trades[0]
    assert t.exit_price == pytest.approx(90)
    assert t.exit_reason.startswith("stop_loss")


class TrailingStop(Strategy):
    def should_long(self):
        return self.index == 0

    def go_long(self):
        self.buy = 1, self.price
        self.stop_loss = 1, self.price * 0.95

    def update_position(self):
        new = self.close * 0.95
        if new > self.average_stop_loss:
            self.stop_loss = 1, new


def test_trailing_stop_modifications_are_resubmitted():
    closes = [100, 110, 120, 130, 120, 110]
    res = run_backtest(TrailingStop, make_candles(closes), "1d", config=CFG)
    t = res.trades[0]
    assert t.exit_price == pytest.approx(130 * 0.95)
    stops = [o for o in res.orders if o.role is OrderRole.STOP_LOSS]
    assert len(stops) >= 3  # original + resubmissions


class LimitEntryCancelled(Strategy):
    def should_long(self):
        return True

    def go_long(self):
        self.buy = 1, self.price * 0.5  # far below: never fills


def test_unfilled_entry_is_cancelled_each_new_candle():
    res = run_backtest(LimitEntryCancelled, make_candles([100] * 6), "1d", config=CFG)
    entries = [o for o in res.orders if o.role is OrderRole.ENTRY]
    assert len(entries) == 6
    assert sum(o.status.value == "canceled" for o in entries) == 5


class FilteredOut(Strategy):
    def should_long(self):
        return True

    def go_long(self):
        self.buy = 1, self.price

    def filters(self):
        return [self.never]

    def never(self):
        return False


def test_filters_block_entries():
    res = run_backtest(FilteredOut, make_candles([100] * 5), "1d", config=CFG)
    assert not res.orders


class Conflict(Strategy):
    def should_long(self):
        return True

    def should_short(self):
        return True


def test_conflicting_rules_raise():
    cfg = BacktestConfig(exchange_type="futures", leverage=1)
    with pytest.raises(ConflictingRules):
        run_backtest(Conflict, make_candles([100] * 3), "1d", config=cfg)


class ShortOnSpot(Strategy):
    def should_short(self):
        return True


def test_short_on_spot_is_invalid():
    with pytest.raises(InvalidStrategy):
        run_backtest(ShortOnSpot, make_candles([100] * 3), "1d", config=CFG)


class Peeker(Strategy):
    """Records what the strategy can see at each decision."""

    seen: list = []

    def before(self):
        Peeker.seen.append((self.time, len(self.all_candles), self.close))


def test_strategy_never_sees_future_candles():
    Peeker.seen = []
    c = make_candles(np.arange(100, 110, dtype=float))
    run_backtest(Peeker, c, "1d", config=CFG)
    for k, (t, n_visible, close) in enumerate(Peeker.seen):
        assert n_visible == k + 1
        assert close == c[k, 4]
        assert t == c[k, 0] + 86_400_000  # decision at candle close


class ExposureStrategy(Strategy):
    def after(self):
        self.order_target_exposure(0.5 if self.index < 3 else 0.0, band=0.01)


def test_order_target_exposure_rebalances_and_exits():
    res = run_backtest(ExposureStrategy, make_candles([100, 100, 100, 100, 100]), "1d", config=CFG)
    assert len(res.trades) == 1
    assert res.trades[0].qty == pytest.approx(0.5 * 10_000 / 100)  # 50% of equity, below the leverage cap
    assert res.exposure.iloc[1] == pytest.approx(0.5, rel=1e-6)


class FullExposure(Strategy):
    def after(self):
        self.order_target_exposure(1.0)


def test_order_target_exposure_keeps_fee_buffer_at_the_cap():
    cfg = BacktestConfig(fee_maker=0.001, fee_taker=0.001, slippage_bps=5)
    res = run_backtest(FullExposure, make_candles([100.0] * 4), "1d", config=cfg)
    assert not [o for o in res.orders if o.status.value == "rejected"]
    assert 0.99 < res.exposure.iloc[1] <= 1.0  # iloc[0] is the starting-balance anchor
