"""Jesse-style event-driven strategy API.

Port of the Jesse strategy lifecycle (MIT) so Jesse users feel at home::

    class MyStrategy(Strategy):
        def should_long(self) -> bool:
            return self.close > ta.ema(self.candles, 50)

        def go_long(self):
            qty = utils.size_to_qty(self.available_margin * 0.95, self.price, fee_rate=self.fee_rate)
            self.buy = qty, self.price               # price == current -> MARKET
            self.stop_loss = qty, self.price * 0.95  # placed when the entry fills
            self.take_profit = qty, self.price * 1.1

        def update_position(self):
            if self.close < ta.ema(self.candles, 50):
                self.liquidate()

On every closed candle the engine calls ``before() -> _check() -> after()``:

1. If entry orders are pending while flat and ``should_cancel_entry()`` -> cancel them.
2. If a position is open -> ``update_position()``; modified ``stop_loss`` /
   ``take_profit`` / ``buy`` / ``sell`` values are detected and re-submitted.
3. If flat with no pending entries -> ``should_short()`` / ``should_long()``;
   when true, ``go_long()`` / ``go_short()`` define ``self.buy`` / ``self.sell``.
   Each ``(qty, price)`` leg becomes a MARKET (price == current), STOP (price beyond
   current) or LIMIT (price better than current) order.

Extensions over Jesse:

* ``precompute(candles)`` - compute indicators once, vectorized (Freqtrade style),
  and read them with ``self.ind("name")``. Must be causal - ``tradebot lookahead``
  verifies it.
* ``signal()`` - an optional exposure in [-1, 1] consumed by the JEV decision
  engine's quant consensus.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from ..core import timeframes as tfs
from ..core.candles import CLOSE, HIGH, LOW, OPEN, TS, VOLUME
from ..core.types import (
    ClosedTrade,
    ExchangeType,
    Fill,
    Order,
    OrderRole,
    OrderType,
    Position,
    Side,
    TradingMode,
)

if TYPE_CHECKING:
    from ..execution.simulated import SimulatedExchange

log = logging.getLogger("tradebot.strategy")


class InvalidStrategy(Exception):
    pass


class ConflictingRules(InvalidStrategy):
    pass


ENTRY_ROLES = {OrderRole.ENTRY}
EXIT_ROLES = {OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT, OrderRole.REDUCE, OrderRole.CLOSE}


def _normalize_legs(value: Any) -> np.ndarray:
    """(qty, price) or [(qty, price), ...] -> float array of shape (k, 2)."""
    arr = np.asarray(value, dtype="float64")
    if arr.ndim == 1:
        if arr.shape[0] != 2:
            raise ValueError
        arr = arr.reshape(1, 2)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError
    if np.isnan(arr).any():
        raise ValueError
    return arr


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.shape == b.shape and bool(np.allclose(a, b, rtol=1e-9, atol=1e-12))


class Strategy:
    """Base class for single-instrument ("route") strategies."""

    #: shown in the dashboard / CLI
    description: ClassVar[str] = ""
    #: recommended timeframe and market type (informational)
    timeframe_hint: ClassVar[str] = "4h"
    supports_short: ClassVar[bool] = False
    #: number of candles returned by ``self.candles`` (trailing window)
    candles_window: ClassVar[int] = 1000

    def __init__(self) -> None:
        self.name: str = type(self).__name__
        self.symbol: str = ""
        self.exchange: str = "sim"
        self.timeframe: str = "4h"
        self.hp: dict[str, Any] = {}
        self.vars: dict[str, Any] = {}
        self.shared_vars: dict[str, Any] = {}
        self.index: int = 0
        self.mode: TradingMode = TradingMode.BACKTEST
        # Jesse-style order intents
        self.buy: Any = None
        self.sell: Any = None
        self.stop_loss: Any = None
        self.take_profit: Any = None
        self._buy: np.ndarray | None = None
        self._sell: np.ndarray | None = None
        self._stop_loss: np.ndarray | None = None
        self._take_profit: np.ndarray | None = None
        self.increased_count: int = 0
        self.reduced_count: int = 0
        self.last_trade_index: int = -1
        # data
        self._all_candles: np.ndarray = np.empty((0, 6))
        self._i: int = -1
        self._ind: dict[str, np.ndarray] = {}
        self._broker: SimulatedExchange | None = None
        self._candle_provider: Callable[[str, str], np.ndarray] | None = None
        self._logger: Callable[[str, str], None] | None = None
        self.chart_lines: dict[str, list[tuple[int, float]]] = {}
        self._executing = False

    # ================================================================== user hooks
    def hyperparameters(self) -> list[dict[str, Any]]:
        """Optimizable parameters: [{'name','type': int|float|'categorical','min','max','default',...}]"""
        return []

    def precompute(self, candles: np.ndarray) -> dict[str, np.ndarray] | None:
        """Vectorized, causal indicator computation. Return {name: array aligned with candles}."""
        return None

    def should_long(self) -> bool:
        return False

    def should_short(self) -> bool:
        return False

    def go_long(self) -> None:
        pass

    def go_short(self) -> None:
        pass

    def should_cancel_entry(self) -> bool:
        return True

    def update_position(self) -> None:
        pass

    def before(self) -> None:
        pass

    def after(self) -> None:
        pass

    def filters(self) -> list[Callable[[], bool]]:
        return []

    def on_open_position(self, order: Order) -> None:
        pass

    def on_close_position(self, order: Order, closed_trade: ClosedTrade) -> None:
        pass

    def on_increased_position(self, order: Order) -> None:
        pass

    def on_reduced_position(self, order: Order) -> None:
        pass

    def on_cancel(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    def watch_list(self) -> list[tuple[str, Any]]:
        return []

    def signal(self) -> float | None:
        """Desired exposure in [-1, 1] for the JEV quant consensus (None = not supported)."""
        return None

    def dna(self) -> str:
        return ""

    # ================================================================== engine wiring
    def _bind(
        self,
        *,
        symbol: str,
        timeframe: str,
        candles: np.ndarray,
        broker: SimulatedExchange,
        hp: dict[str, Any] | None = None,
        exchange: str = "sim",
        mode: TradingMode = TradingMode.BACKTEST,
        shared_vars: dict[str, Any] | None = None,
        candle_provider: Callable[[str, str], np.ndarray] | None = None,
        logger: Callable[[str, str], None] | None = None,
    ) -> None:
        self.symbol, self.timeframe, self.exchange, self.mode = symbol, timeframe, exchange, mode
        defaults = {p["name"]: p.get("default") for p in self.hyperparameters()}
        self.hp = {**defaults, **(hp or {})}
        self._all_candles = candles
        self._broker = broker
        self._candle_provider = candle_provider
        self._logger = logger
        if shared_vars is not None:
            self.shared_vars = shared_vars
        self._run_precompute()

    def _run_precompute(self) -> None:
        ind = self.precompute(self._all_candles)
        self._ind = {} if ind is None else {k: np.asarray(v, dtype="float64") for k, v in ind.items()}
        for k, v in self._ind.items():
            if len(v) != len(self._all_candles):
                raise InvalidStrategy(
                    f"precompute()['{k}'] length {len(v)} != candles {len(self._all_candles)}"
                )

    def _set_candles(self, candles: np.ndarray, recompute: bool = True) -> None:
        """Live mode: replace the candle buffer (trailing window) and refresh indicators."""
        self._all_candles = candles
        self._i = len(candles) - 1
        if recompute:
            self._run_precompute()

    def _execute(self, i: int) -> None:
        """Run one decision step at the close of candle ``i``."""
        if self._executing:
            return
        self._executing = True
        self._i = i
        try:
            self.before()
            self._check()
            self.after()
        finally:
            self._executing = False
            self.index += 1

    def _check(self) -> None:
        if self._pending_entry_orders() and self.is_close and self.should_cancel_entry():
            self._execute_cancel()

        if self.is_open:
            self.update_position()
            self._detect_modifications()

        if self.is_close and not self._pending_entry_orders():
            self._reset()
            should_short = self.should_short()
            if should_short and self.exchange_type is ExchangeType.SPOT:
                raise InvalidStrategy("should_short() cannot be True when trading spot (no shorting)")
            should_long = self.should_long()
            if should_short and should_long:
                raise ConflictingRules("should_short and should_long should not be true at the same time")
            if should_long:
                self._execute_side(Side.BUY)
            elif should_short:
                self._execute_side(Side.SELL)

    def _execute_side(self, side: Side) -> None:
        if side is Side.BUY:
            self.go_long()
            intent = self.buy
            label = "buy"
        else:
            self.go_short()
            intent = self.sell
            label = "sell"
        if intent is None:
            raise InvalidStrategy(f"You forgot to set self.{label}. example: self.{label} = qty, price")
        try:
            legs = _normalize_legs(intent)
        except ValueError as exc:
            raise InvalidStrategy(
                f"self.{label} must be (qty, price) or [(qty, price), ...]; got {intent!r}"
            ) from exc
        sl = tp = None
        try:
            if self.stop_loss is not None:
                sl = _normalize_legs(self.stop_loss)
            if self.take_profit is not None:
                tp = _normalize_legs(self.take_profit)
        except ValueError as exc:
            raise InvalidStrategy("stop_loss/take_profit must be (qty, price) or a list of them") from exc

        for f in self.filters():
            if not callable(f):
                raise InvalidStrategy(
                    "filters() must return methods without calling them, e.g. [self.my_filter]"
                )
            if not f():
                self._reset()
                return

        if side is Side.BUY:
            self._buy = legs.copy()
        else:
            self._sell = legs.copy()
        self._stop_loss, self._take_profit = sl, tp
        for qty, price in legs:
            self._submit_leg(side, qty, price, OrderRole.ENTRY)

    def _submit_leg(self, side: Side, qty: float, price: float, role: OrderRole) -> Order | None:
        if qty <= 0:
            return None
        cur = self.price
        if abs(price - cur) <= abs(cur) * 1e-9:
            otype, px = OrderType.MARKET, None
        elif (side is Side.BUY and price > cur) or (side is Side.SELL and price < cur):
            otype, px = OrderType.STOP, float(price)
        else:
            otype, px = OrderType.LIMIT, float(price)
        res = self.broker.submit(Order(self.symbol, side, otype, float(qty), px, role=role))
        if res.status.value == "rejected":
            self.log(f"{side} {qty:.8g} @ {price:.8g} rejected: {res.reject_reason}", "warning")
        return res

    def _submit_exits(self) -> None:
        """Place stop-loss (STOP) / take-profit (LIMIT) reduce-only orders for the open position.

        Levels already beyond the current price execute immediately at market
        (Jesse semantics) - the exchange treats them as triggered/marketable.
        """
        if not self.is_open:
            return
        exit_side = Side.SELL if self.is_long else Side.BUY
        for legs, otype, role in (
            (self._stop_loss, OrderType.STOP, OrderRole.STOP_LOSS),
            (self._take_profit, OrderType.LIMIT, OrderRole.TAKE_PROFIT),
        ):
            if legs is None:
                continue
            for qty, price in legs:
                if not self.is_open:
                    return
                qty = min(float(qty), abs(self.position.qty))
                if qty <= 0:
                    continue
                self.broker.submit(
                    Order(self.symbol, exit_side, otype, qty, float(price), role=role, reduce_only=True)
                )

    def _market_exit(self, qty: float, role: OrderRole, tag: str = "") -> None:
        pos = self.position
        if not pos.is_open or qty <= 0:
            return
        side = Side.SELL if pos.is_long else Side.BUY
        self.broker.submit(
            Order(
                self.symbol,
                side,
                OrderType.MARKET,
                min(qty, abs(pos.qty)),
                role=role,
                reduce_only=True,
                tag=tag,
            )
        )

    def _detect_modifications(self) -> None:
        if not self.is_open:
            return
        try:
            sl = None if self.stop_loss is None else _normalize_legs(self.stop_loss)
            tp = None if self.take_profit is None else _normalize_legs(self.take_profit)
        except ValueError as exc:
            raise InvalidStrategy("stop_loss/take_profit must be (qty, price) or a list of them") from exc
        if sl is not None and tp is not None and len(sl) == len(tp) == 1 and np.isclose(sl[0, 1], tp[0, 1]):
            raise InvalidStrategy("stop-loss and take-profit should not be exactly the same")
        if not _same(sl, self._stop_loss):
            self.broker.cancel_all(self.symbol, {OrderRole.STOP_LOSS})
            self._stop_loss = sl
            if sl is not None:
                saved_tp = self._take_profit
                self._take_profit = None
                self._submit_exits()
                self._take_profit = saved_tp
        if self.is_open and not _same(tp, self._take_profit):
            self.broker.cancel_all(self.symbol, {OrderRole.TAKE_PROFIT})
            self._take_profit = tp
            if tp is not None:
                saved_sl = self._stop_loss
                self._stop_loss = None
                self._submit_exits()
                self._stop_loss = saved_sl
        # scaling in: a modified self.buy (long) / self.sell (short)
        if self.is_long and self.buy is not None:
            legs = _normalize_legs(self.buy)
            if not _same(legs, self._buy):
                self.broker.cancel_all(self.symbol, {OrderRole.INCREASE})
                self._buy = legs
                for qty, price in legs:
                    self._submit_leg(Side.BUY, qty, price, OrderRole.INCREASE)
        elif self.is_short and self.sell is not None:
            legs = _normalize_legs(self.sell)
            if not _same(legs, self._sell):
                self.broker.cancel_all(self.symbol, {OrderRole.INCREASE})
                self._sell = legs
                for qty, price in legs:
                    self._submit_leg(Side.SELL, qty, price, OrderRole.INCREASE)

    def _execute_cancel(self) -> None:
        self.broker.cancel_all(self.symbol)
        self._reset()
        self.on_cancel()

    def _reset(self) -> None:
        self.buy = self.sell = self.stop_loss = self.take_profit = None
        self._buy = self._sell = self._stop_loss = self._take_profit = None
        self.increased_count = 0
        self.reduced_count = 0

    def _pending_entry_orders(self) -> list[Order]:
        return [o for o in self.broker.active_orders(self.symbol) if o.role in ENTRY_ROLES]

    # called by the engine for every fill on this strategy's symbol
    def _on_fill(
        self, order: Order, fill: Fill, before: float, after: float, closed: ClosedTrade | None
    ) -> None:
        if closed is not None:
            closed.strategy = self.name
            closed.bars_held = max(int((closed.closed_at - closed.opened_at) // tfs.to_ms(self.timeframe)), 0)
        opened = abs(before) <= Position.EPS and abs(after) > Position.EPS
        flipped = before * after < 0
        if closed is not None:
            self.last_trade_index = self.index
            self.broker.cancel_all(self.symbol)
            saved = (self.buy, self.sell, self.stop_loss, self.take_profit)
            self.on_close_position(order, closed)
            if not flipped:
                self._reset()
            else:
                self.buy, self.sell, self.stop_loss, self.take_profit = saved
        if opened or flipped:
            self.increased_count = 1
            self._submit_exits()
            self.on_open_position(order)
            self._detect_modifications()
        elif abs(after) > abs(before) + Position.EPS:
            self.increased_count += 1
            self.on_increased_position(order)
            self._resize_exits()
            self._detect_modifications()
        elif abs(after) < abs(before) - Position.EPS and abs(after) > Position.EPS:
            self.reduced_count += 1
            self.on_reduced_position(order)
            self._detect_modifications()

    def _resize_exits(self) -> None:
        """After scaling in, resize a single full-size stop-loss to the new position size."""
        pos = self.position
        if self._stop_loss is not None and len(self._stop_loss) == 1:
            self._stop_loss = np.array([[abs(pos.qty), self._stop_loss[0, 1]]])
            self.stop_loss = (abs(pos.qty), float(self._stop_loss[0, 1]))
            self.broker.cancel_all(self.symbol, {OrderRole.STOP_LOSS})
            saved_tp = self._take_profit
            self._take_profit = None
            self._submit_exits()
            self._take_profit = saved_tp

    # ================================================================== actions
    def liquidate(self, tag: str = "liquidate") -> None:
        """Close the whole position at market and cancel resting exit orders."""
        if self.is_close:
            return
        self.broker.cancel_all(self.symbol)
        self._market_exit(abs(self.position.qty), OrderRole.CLOSE, tag)

    @property
    def exposure(self) -> float:
        """Current signed position value as a fraction of portfolio value."""
        eq = self.portfolio_value
        if eq <= 0 or self.is_close:
            return 0.0
        return self.position.qty * self.price / eq

    def order_target_exposure(self, exposure: float, band: float = 0.0, tag: str = "") -> Order | None:
        """Move the position to ``exposure`` x portfolio value with a market order.

        Extension over Jesse (like Zipline's ``order_target_percent``) for
        signal-driven / volatility-targeted strategies. Trades smaller than
        ``band`` x equity are skipped to control turnover; exits always execute.
        """
        eq, price = self.portfolio_value, self.price
        if eq <= 0 or price <= 0 or not math.isfinite(exposure):
            return None
        if self.is_spot_trading or not type(self).supports_short:
            exposure = max(exposure, 0.0)
        # keep a small buffer for fees/slippage so the order is never rejected for margin
        cap = self.leverage * (1.0 - 3.0 * self.fee_rate - 0.001)
        exposure = max(min(exposure, cap), -cap)
        target_qty = exposure * eq / price
        cur = self.position.qty
        if abs(target_qty) * price < 1e-9 * eq:
            if self.is_open:
                self.liquidate(tag or "target exposure 0")
            return None
        delta = target_qty - cur
        if abs(delta) * price < band * eq:
            return None
        side = Side.BUY if delta > 0 else Side.SELL
        if self.is_close:
            role, reduce_only = OrderRole.ENTRY, False
        elif np.sign(delta) == np.sign(cur):
            role, reduce_only = OrderRole.INCREASE, False
        elif abs(delta) <= abs(cur) + 1e-12:
            role, reduce_only = OrderRole.REDUCE, True
        else:  # reversal: flatten first, then open the other side
            self.liquidate(tag or "reverse")
            if self.is_open:
                return None
            role, reduce_only, delta = OrderRole.ENTRY, False, target_qty
        order = Order(
            self.symbol, side, OrderType.MARKET, abs(delta), role=role, reduce_only=reduce_only, tag=tag
        )
        res = self.broker.submit(order)
        if res.status.value == "rejected":
            self.log(f"target exposure order rejected: {res.reject_reason}", "warning")
        return res

    def log(self, msg: str, level: str = "info") -> None:
        if self._logger is not None:
            self._logger(msg, level)
        else:
            getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(msg)

    def add_line_to_chart(self, title: str, value: float) -> None:
        self.chart_lines.setdefault(title, []).append((int(self.current_candle[TS]), float(value)))

    # ================================================================== data accessors
    @property
    def broker(self) -> SimulatedExchange:
        if self._broker is None:
            raise InvalidStrategy("strategy is not bound to an engine")
        return self._broker

    @property
    def current_candle(self) -> np.ndarray:
        return self._all_candles[self._i]

    @property
    def candles(self) -> np.ndarray:
        """Trailing window of closed candles up to and including the current one."""
        lo = max(0, self._i + 1 - self.candles_window)
        return self._all_candles[lo : self._i + 1]

    @property
    def all_candles(self) -> np.ndarray:
        return self._all_candles[: self._i + 1]

    def get_candles(self, exchange: str, symbol: str, timeframe: str) -> np.ndarray:
        if self._candle_provider is None:
            raise InvalidStrategy("no candle provider for extra routes")
        return self._candle_provider(symbol, timeframe)

    def ind(self, name: str, offset: int = 0) -> float:
        """Current (offset=0) or previous (offset=1, ...) value of a precomputed indicator."""
        j = self._i - offset
        if j < 0:
            return float("nan")
        return float(self._ind[name][j])

    def ind_series(self, name: str) -> np.ndarray:
        return self._ind[name][: self._i + 1]

    @property
    def open(self) -> float:
        return float(self.current_candle[OPEN])

    @property
    def close(self) -> float:
        return float(self.current_candle[CLOSE])

    @property
    def price(self) -> float:
        """Current market price: the candle close at decision time, or the live path price
        when called from an event handler during intrabar order execution."""
        if self._broker is not None:
            p = self._broker.last_price.get(self.symbol)
            if p:
                return float(p)
        return float(self.current_candle[CLOSE])

    @property
    def high(self) -> float:
        return float(self.current_candle[HIGH])

    @property
    def low(self) -> float:
        return float(self.current_candle[LOW])

    @property
    def volume(self) -> float:
        return float(self.current_candle[VOLUME])

    @property
    def time(self) -> int:
        """Close time (ms) of the current candle - the moment the decision is made."""
        return int(self.current_candle[TS]) + tfs.to_ms(self.timeframe)

    # ================================================================== account accessors
    @property
    def position(self) -> Position:
        return self.broker.position(self.symbol)

    @property
    def is_long(self) -> bool:
        return self.position.is_long

    @property
    def is_short(self) -> bool:
        return self.position.is_short

    @property
    def is_open(self) -> bool:
        return self.position.is_open

    @property
    def is_close(self) -> bool:
        return self.position.is_close

    @property
    def balance(self) -> float:
        return self.broker.balance

    @property
    def capital(self) -> float:
        return self.broker.balance

    @property
    def portfolio_value(self) -> float:
        return self.broker.equity()

    @property
    def available_margin(self) -> float:
        return self.broker.available_margin()

    @property
    def leveraged_available_margin(self) -> float:
        return self.broker.available_margin() * self.broker.leverage

    @property
    def fee_rate(self) -> float:
        return self.broker.fee_rate

    @property
    def leverage(self) -> float:
        return self.broker.leverage

    @property
    def exchange_type(self) -> ExchangeType:
        return self.broker.exchange_type

    @property
    def is_spot_trading(self) -> bool:
        return self.exchange_type is ExchangeType.SPOT

    @property
    def is_futures_trading(self) -> bool:
        return self.exchange_type is ExchangeType.FUTURES

    @property
    def average_entry_price(self) -> float:
        if self.is_open:
            return self.position.entry_price
        legs = self._buy if self._buy is not None else self._sell
        if legs is None:
            return float("nan")
        return float(np.average(legs[:, 1], weights=legs[:, 0]))

    @property
    def average_stop_loss(self) -> float:
        if self._stop_loss is None:
            return float("nan")
        return float(np.average(self._stop_loss[:, 1], weights=self._stop_loss[:, 0]))

    @property
    def average_take_profit(self) -> float:
        if self._take_profit is None:
            return float("nan")
        return float(np.average(self._take_profit[:, 1], weights=self._take_profit[:, 0]))

    @property
    def trades(self) -> list[ClosedTrade]:
        return [t for t in self.broker.closed_trades if t.symbol == self.symbol]

    @property
    def orders(self) -> list[Order]:
        return [o for o in self.broker.orders.values() if o.symbol == self.symbol]

    @property
    def entry_orders(self) -> list[Order]:
        return [
            o
            for o in self.broker.active_orders(self.symbol)
            if o.role in (OrderRole.ENTRY, OrderRole.INCREASE)
        ]

    @property
    def exit_orders(self) -> list[Order]:
        return [o for o in self.broker.active_orders(self.symbol) if o.role in EXIT_ROLES]

    @property
    def is_backtesting(self) -> bool:
        return self.mode is TradingMode.BACKTEST

    @property
    def is_papertrading(self) -> bool:
        return self.mode is TradingMode.PAPER

    @property
    def is_livetrading(self) -> bool:
        return self.mode is TradingMode.LIVE

    @property
    def is_live(self) -> bool:
        return self.mode in (TradingMode.PAPER, TradingMode.LIVE)
