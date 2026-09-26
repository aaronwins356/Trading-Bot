"""Classic retail "bot types" offered by exchanges (Pionex, Binance, 3Commas, KuCoin):
grid trading and dollar-cost averaging, plus an RSI(2) mean-reversion control.

These are included because they are industry-standard bot products, *not*
because they are recommended: see docs/STRATEGIES.md for their (honest)
backtest results. A grid bot is short volatility - it earns in ranges and bleeds
in trends; DCA is a capital-deployment rule, not an edge.
"""

from __future__ import annotations

import numpy as np

from .. import indicators as ta
from ..core.candles import CLOSE
from ..core.types import ClosedTrade, Fill, Order, OrderRole, OrderType, Side
from ..strategy import utils
from ..strategy.base import Strategy


class GridTrading(Strategy):
    """Spot grid: buy limits below the centre, a matching sell limit one step above each fill.

    The grid re-centres when price leaves it upwards with no inventory. When price
    falls through the bottom, inventory is held (the classic grid "bag") unless
    ``stop_below_pct`` > 0, in which case the grid is liquidated and restarted.
    """

    description = "Spot grid bot: ladder of buy limits with take-profit one grid step above each fill."
    timeframe_hint = "1h"

    def hyperparameters(self):
        return [
            {"name": "levels", "type": int, "min": 2, "max": 50, "default": 10},
            {"name": "spacing_pct", "type": float, "min": 0.2, "max": 10.0, "default": 1.5},
            {"name": "stop_below_pct", "type": float, "min": 0.0, "max": 50.0, "default": 0.0},
        ]

    def before(self):
        self.vars.setdefault("center", None)
        self.vars.setdefault("slots", {})  # level_price -> state ("buy" | "sell")

    def _level_prices(self, center: float) -> list[float]:
        step = self.hp["spacing_pct"] / 100.0
        return [center * (1 - step * (k + 1)) for k in range(int(self.hp["levels"]))]

    def _slot_qty(self, center: float) -> float:
        budget = self.portfolio_value / int(self.hp["levels"])
        return utils.size_to_qty(budget * 0.98, center, fee_rate=self.fee_rate)

    def _start_grid(self) -> None:
        self.broker.cancel_all(self.symbol)
        center = self.price
        self.vars["center"] = center
        self.vars["qty"] = self._slot_qty(center)
        self.vars["slots"] = {}
        for lvl in self._level_prices(center):
            o = self.broker.submit(
                Order(
                    self.symbol,
                    Side.BUY,
                    OrderType.LIMIT,
                    self.vars["qty"],
                    lvl,
                    role=OrderRole.ENTRY,
                    tag="grid buy",
                )
            )
            if o.is_active:
                self.vars["slots"][o.id] = ("buy", lvl)

    # grid manages its own orders: bypass the entry/exit lifecycle
    def _check(self) -> None:
        center = self.vars.get("center")
        if center is None:
            self._start_grid()
            return
        step = self.hp["spacing_pct"] / 100.0
        bottom = center * (1 - step * int(self.hp["levels"]))
        if self.is_close and self.price > center * (1 + step):
            self._start_grid()  # price ran away upwards with no inventory: follow it
        elif self.hp["stop_below_pct"] > 0 and self.price < bottom * (1 - self.hp["stop_below_pct"] / 100):
            self.liquidate("grid stop")
            self._start_grid()

    def _on_fill(
        self, order: Order, fill: Fill, before: float, after: float, closed: ClosedTrade | None
    ) -> None:
        if closed is not None:
            closed.strategy = self.name
        slot = self.vars.get("slots", {}).pop(order.id, None)
        if slot is None:
            return
        kind, lvl = slot
        step = self.hp["spacing_pct"] / 100.0
        if kind == "buy":
            o = self.broker.submit(
                Order(
                    self.symbol,
                    Side.SELL,
                    OrderType.LIMIT,
                    fill.qty,
                    lvl * (1 + step),
                    role=OrderRole.TAKE_PROFIT,
                    reduce_only=True,
                    tag="grid sell",
                )
            )
            if o.is_active:
                self.vars["slots"][o.id] = ("sell", lvl)
        else:  # sold: re-arm the buy at the original level
            o = self.broker.submit(
                Order(
                    self.symbol,
                    Side.BUY,
                    OrderType.LIMIT,
                    self.vars["qty"],
                    lvl,
                    role=OrderRole.ENTRY,
                    tag="grid buy",
                )
            )
            if o.is_active:
                self.vars["slots"][o.id] = ("buy", lvl)


class DCABot(Strategy):
    """Dollar-cost averaging: invest a fixed slice of the starting capital every N bars.

    ``dip_multiplier`` > 1 buys more when price is below its long moving average
    ("smart DCA"). Optional take-profit sells everything at +X% over the average cost.
    """

    description = "Dollar-cost averaging with optional dip weighting and take-profit."
    timeframe_hint = "1d"

    def hyperparameters(self):
        return [
            {"name": "interval_bars", "type": int, "min": 1, "max": 60, "default": 7},
            {"name": "slices", "type": int, "min": 4, "max": 520, "default": 52},
            {"name": "dip_ma", "type": int, "min": 0, "max": 365, "default": 200},
            {"name": "dip_multiplier", "type": float, "min": 1.0, "max": 5.0, "default": 1.0},
            {"name": "take_profit_pct", "type": float, "min": 0.0, "max": 500.0, "default": 0.0},
        ]

    def precompute(self, candles):
        n = int(self.hp["dip_ma"])
        ma = ta.sma(candles[:, CLOSE], n, sequential=True) if n > 0 else np.full(len(candles), np.nan)
        return {"ma": ma}

    def _check(self) -> None:
        self.vars.setdefault("slice_value", self.portfolio_value / int(self.hp["slices"]))
        if self.index % int(self.hp["interval_bars"]) == 0:
            amount = self.vars["slice_value"]
            ma = self.ind("ma")
            if np.isfinite(ma) and self.close < ma:
                amount *= float(self.hp["dip_multiplier"])
            amount = min(amount, self.available_margin * 0.995)
            qty = utils.size_to_qty(amount, self.price, fee_rate=self.fee_rate)
            if qty * self.price >= 10:
                self.broker.submit(
                    Order(
                        self.symbol,
                        Side.BUY,
                        OrderType.MARKET,
                        qty,
                        role=OrderRole.INCREASE if self.is_open else OrderRole.ENTRY,
                        tag="dca",
                    )
                )
        tp = float(self.hp["take_profit_pct"])
        if tp > 0 and self.is_open and self.close > self.position.entry_price * (1 + tp / 100):
            self.liquidate("dca take-profit")


class RSI2MeanReversion(Strategy):
    """Connors RSI(2) pullback system - a well-known equity-index edge.

    Included as a *negative control*: on BTC daily bars it does not survive costs
    (see docs/STRATEGIES.md). Crypto trends at daily horizons.
    """

    description = "Connors RSI(2) pullback in an uptrend (negative control on crypto)."
    timeframe_hint = "1d"

    def hyperparameters(self):
        return [
            {"name": "rsi_period", "type": int, "min": 2, "max": 5, "default": 2},
            {"name": "entry_rsi", "type": float, "min": 1, "max": 30, "default": 10},
            {"name": "trend_ma", "type": int, "min": 0, "max": 300, "default": 200},
            {"name": "exit_ma", "type": int, "min": 2, "max": 20, "default": 5},
        ]

    def precompute(self, candles):
        c = candles[:, CLOSE]
        return {
            "rsi": ta.rsi(c, int(self.hp["rsi_period"]), sequential=True),
            "trend": ta.sma(c, int(self.hp["trend_ma"]), sequential=True)
            if self.hp["trend_ma"]
            else np.zeros(len(c)),
            "exit": ta.sma(c, int(self.hp["exit_ma"]), sequential=True),
        }

    def should_long(self) -> bool:
        return self.ind("rsi") < self.hp["entry_rsi"] and self.close > self.ind("trend")

    def go_long(self):
        qty = utils.size_to_qty(self.available_margin * 0.99, self.price, fee_rate=self.fee_rate)
        self.buy = qty, self.price

    def update_position(self):
        if self.close > self.ind("exit"):
            self.liquidate("mean reached")

    def signal(self):
        return 1.0 if self.is_long else 0.0
