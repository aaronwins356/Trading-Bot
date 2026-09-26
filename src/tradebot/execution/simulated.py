"""Simulated exchange used for backtesting AND paper trading.

Design goals (industry practice, see docs/RESEARCH.md):

* **No look-ahead**: orders placed at the close of bar *t* can only fill on the
  price path of bar *t+1* onwards.
* **Realistic intrabar sequencing**: each bar is walked as a price path
  (``open -> low -> high -> close`` for up bars, ``open -> high -> low -> close``
  for down bars - the adverse extreme first). When 1-minute sub-candles are
  supplied, the path is walked minute by minute so a stop-loss and a
  take-profit inside the same 4h candle resolve in the order they really
  happened (the approach Jesse uses).
* **Gaps**: a stop/limit that is jumped over by the open fills at the open.
* **Costs**: maker/taker fees, adverse slippage on market & stop fills,
  perpetual-futures funding, maintenance-margin liquidation.
* **Margin accounting**: equity = balance + unrealized PnL. Spot is modelled as
  1x, long-only margin accounting, which is economically identical to holding coins.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..core.candles import CLOSE, HIGH, LOW, OPEN, TS
from ..core.types import (
    ClosedTrade,
    ExchangeType,
    Fill,
    Liquidity,
    Order,
    OrderRole,
    OrderStatus,
    OrderType,
    Position,
    Side,
    next_order_id,
)

EPS = 1e-12


@dataclass
class ExecutionConfig:
    starting_balance: float = 10_000.0
    fee_maker: float = 0.001  # 0.10% (Binance spot default)
    fee_taker: float = 0.001
    slippage_bps: float = 5.0  # adverse slippage for market/stop fills, in basis points
    exchange_type: ExchangeType = ExchangeType.SPOT
    leverage: float = 1.0
    funding_rate_8h: float = 0.0  # constant funding per 8h (futures); >0 => longs pay shorts
    maintenance_margin: float = 0.005
    min_notional: float = 1.0  # orders below this notional are rejected
    quote_currency: str = "USDT"

    def __post_init__(self) -> None:
        self.exchange_type = ExchangeType(self.exchange_type)
        if self.exchange_type is ExchangeType.SPOT and self.leverage != 1.0:
            raise ValueError("spot trading only supports leverage=1")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")


FillListener = Callable[[Order, Fill, float, float, "ClosedTrade | None"], None]
OrderListener = Callable[[Order], None]


@dataclass
class _PathEvent:
    order: Order
    level: float


class SimulatedExchange:
    """A deterministic matching engine over OHLC price paths."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()
        self.balance: float = self.config.starting_balance
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self._active: dict[str, list[Order]] = {}
        self.fills: list[Fill] = []
        self.closed_trades: list[ClosedTrade] = []
        self.last_price: dict[str, float] = {}
        self.now: int = 0
        self.total_fees: float = 0.0
        self.total_funding: float = 0.0
        self.liquidated: bool = False
        self._fill_listeners: list[FillListener] = []
        self._order_listeners: list[OrderListener] = []
        self._trade_seq = 0
        self._exit_reason: dict[str, str] = {}
        self.strategy_name: str = ""

    # ------------------------------------------------------------------ listeners
    def add_fill_listener(self, fn: FillListener) -> None:
        self._fill_listeners.append(fn)

    def add_order_listener(self, fn: OrderListener) -> None:
        self._order_listeners.append(fn)

    def _notify_order(self, order: Order) -> None:
        for fn in self._order_listeners:
            fn(order)

    # ------------------------------------------------------------------ account views
    @property
    def fee_rate(self) -> float:
        return self.config.fee_taker

    @property
    def leverage(self) -> float:
        return self.config.leverage

    @property
    def exchange_type(self) -> ExchangeType:
        return self.config.exchange_type

    def position(self, symbol: str) -> Position:
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol, last_price=self.last_price.get(symbol, 0.0))
            self.positions[symbol] = pos
        return pos

    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl() for p in self.positions.values() if p.is_open)

    def equity(self) -> float:
        return self.balance + self.unrealized_pnl()

    def used_margin(self) -> float:
        return sum(p.value() for p in self.positions.values() if p.is_open) / self.config.leverage

    def gross_exposure(self) -> float:
        eq = self.equity()
        if eq <= 0:
            return 0.0
        return sum(p.value() for p in self.positions.values() if p.is_open) / eq

    def available_margin(self) -> float:
        return self.equity() - self.used_margin() - self._reserved_margin()

    def _reserved_margin(self) -> float:
        """Margin reserved by resting orders that would increase exposure."""
        total = 0.0
        for orders in self._active.values():
            for o in orders:
                if o.reduce_only or o.type is OrderType.MARKET:
                    continue
                pos = self.position(o.symbol)
                if pos.is_open and np.sign(pos.qty) != o.side.sign:
                    continue  # reducing order
                px = o.price or self.last_price.get(o.symbol, 0.0)
                total += o.remaining_qty * px / self.config.leverage
        return total

    def active_orders(self, symbol: str | None = None) -> list[Order]:
        if symbol is None:
            return [o for lst in self._active.values() for o in lst]
        return list(self._active.get(symbol, []))

    def has_active_orders(self, symbol: str) -> bool:
        return bool(self._active.get(symbol))

    # ------------------------------------------------------------------ order entry
    def submit(self, order: Order) -> Order:
        """Validate and accept an order. Market orders fill immediately at the last price."""
        order.created_at = order.created_at or self.now
        order.updated_at = self.now
        if not order.id:
            order.id = next_order_id()
        self.orders[order.id] = order
        price = self.last_price.get(order.symbol)
        reason = self._validate(order, price)
        if reason:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reason
            self._notify_order(order)
            return order
        assert price is not None
        order.status = OrderStatus.OPEN
        # marketable / already-triggered orders execute immediately as taker
        if order.type is OrderType.MARKET:
            self._fill(order, self._slip(order.side, price), Liquidity.TAKER)
            return order
        if order.type is OrderType.LIMIT and (
            (order.side is Side.BUY and order.price >= price)
            or (order.side is Side.SELL and order.price <= price)
        ):
            self._fill(order, price, Liquidity.TAKER)
            return order
        if order.type is OrderType.STOP and (
            (order.side is Side.BUY and order.price <= price)
            or (order.side is Side.SELL and order.price >= price)
        ):
            self._fill(order, self._slip(order.side, price), Liquidity.TAKER)
            return order
        self._active.setdefault(order.symbol, []).append(order)
        self._notify_order(order)
        return order

    def _validate(self, order: Order, price: float | None) -> str:
        if price is None or price <= 0:
            return "no market price available"
        if not (order.qty > EPS) or not math.isfinite(order.qty):
            return f"invalid quantity {order.qty}"
        if order.type is not OrderType.MARKET and (order.price is None or not order.price > 0):
            return f"{order.type} order requires a positive price"
        pos = self.position(order.symbol)
        reduces = pos.is_open and np.sign(pos.qty) != order.side.sign
        if order.reduce_only and not reduces:
            return "reduce-only order would not reduce the position"
        ref_px = order.price if order.type is not OrderType.MARKET and order.price else price
        if not order.reduce_only and order.qty * ref_px < self.config.min_notional:
            return f"notional {order.qty * ref_px:.2f} below minimum {self.config.min_notional}"
        spot_sell = self.config.exchange_type is ExchangeType.SPOT and order.side is Side.SELL
        if spot_sell and pos.qty - order.qty < -1e-9 * max(1.0, abs(pos.qty)):
            return "spot: cannot sell more than held (no shorting on spot)"
        if not reduces and not order.reduce_only:
            required = order.qty * ref_px / self.config.leverage
            avail = self.available_margin()
            if required > avail * (1 + 1e-6) + 1e-9:
                return f"insufficient margin: required {required:.2f}, available {avail:.2f}"
        elif reduces and not order.reduce_only:
            # reversal: the part beyond the current size needs margin
            excess = order.qty - abs(pos.qty)
            if excess > EPS:
                if self.config.exchange_type is ExchangeType.SPOT:
                    return "spot: cannot sell more than held (no shorting on spot)"
                required = excess * ref_px / self.config.leverage
                avail = self.available_margin() + abs(pos.qty) * ref_px / self.config.leverage
                if required > avail * (1 + 1e-6) + 1e-9:
                    return f"insufficient margin for reversal: required {required:.2f}, available {avail:.2f}"
        return ""

    def cancel(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or not order.is_active:
            return False
        order.status = OrderStatus.CANCELED
        order.updated_at = self.now
        lst = self._active.get(order.symbol, [])
        if order in lst:
            lst.remove(order)
        self._notify_order(order)
        return True

    def cancel_all(self, symbol: str | None = None, roles: set[OrderRole] | None = None) -> int:
        n = 0
        for o in list(self.active_orders(symbol)):
            if roles is None or o.role in roles:
                n += int(self.cancel(o.id))
        return n

    # ------------------------------------------------------------------ market data
    def set_time(self, ts_ms: int) -> None:
        self.now = int(ts_ms)

    def mark(self, symbol: str, price: float) -> None:
        self.last_price[symbol] = float(price)
        pos = self.positions.get(symbol)
        if pos is not None:
            pos.last_price = float(price)

    def process_bar(self, symbol: str, bar: np.ndarray, sub_bars: np.ndarray | None = None) -> None:
        """Walk the price path of one candle, filling resting orders as prices are touched."""
        if not self._active.get(symbol):
            self.mark(symbol, bar[CLOSE])
            self._check_liquidation()
            return
        if sub_bars is not None and len(sub_bars):
            self._process_sub_bars(symbol, sub_bars)
        else:
            self._run_path(symbol, _bar_path(bar))
        self.mark(symbol, bar[CLOSE])
        self._check_liquidation()

    def _process_sub_bars(self, symbol: str, sub: np.ndarray) -> None:
        j, n = 0, len(sub)
        highs, lows = sub[:, HIGH], sub[:, LOW]
        while j < n and self._active.get(symbol):
            up_lvl, dn_lvl = self._trigger_levels(symbol)
            window_hits = (highs[j:] >= up_lvl) | (lows[j:] <= dn_lvl)
            # gaps between minutes are negligible but still handled by _run_path
            hits = np.flatnonzero(window_hits)
            if hits.size == 0:
                break
            k = j + int(hits[0])
            if k > 0:
                self.mark(symbol, sub[k - 1, CLOSE])
            self.now = int(sub[k, TS]) + 1
            self._run_path(symbol, _bar_path(sub[k]))
            j = k + 1
        self.mark(symbol, sub[-1, CLOSE])

    def _trigger_levels(self, symbol: str) -> tuple[float, float]:
        """Lowest level that an up-move would trigger and highest level a down-move would trigger."""
        up, dn = math.inf, -math.inf
        for o in self._active.get(symbol, []):
            p = float(o.price)  # type: ignore[arg-type]
            if (o.type is OrderType.STOP and o.side is Side.BUY) or (
                o.type is OrderType.LIMIT and o.side is Side.SELL
            ):
                up = min(up, p)
            else:
                dn = max(dn, p)
        return up, dn

    def _run_path(self, symbol: str, path: tuple[float, ...]) -> None:
        prev = self.last_price.get(symbol, path[0])
        segments = [(prev, path[0], True)] + [(path[i], path[i + 1], False) for i in range(len(path) - 1)]
        for a, b, is_gap in segments:
            guard = 0
            while self._active.get(symbol):
                guard += 1
                if guard > 10_000:  # pathological callback loops
                    raise RuntimeError("order processing did not converge")
                ev = self._next_trigger(symbol, a, b)
                if ev is None:
                    break
                o = ev.order
                if is_gap:
                    fill_px = b  # jumped over: fill at the new price
                else:
                    fill_px = ev.level
                if o.type is OrderType.STOP:
                    self.mark(symbol, fill_px)
                    self._fill(o, self._slip(o.side, fill_px), Liquidity.TAKER)
                else:
                    self.mark(symbol, fill_px)
                    self._fill(o, fill_px, Liquidity.MAKER)
                a = b if is_gap else ev.level
            self.mark(symbol, b)

    def _next_trigger(self, symbol: str, a: float, b: float) -> _PathEvent | None:
        best: _PathEvent | None = None
        if b >= a:  # moving up: buy stops & sell limits in (a, b]
            for o in self._active.get(symbol, []):
                p = float(o.price)  # type: ignore[arg-type]
                up_order = (o.type is OrderType.STOP and o.side is Side.BUY) or (
                    o.type is OrderType.LIMIT and o.side is Side.SELL
                )
                if up_order and a <= p <= b and (best is None or p < best.level):
                    best = _PathEvent(o, p)
        if b <= a:  # moving down: sell stops & buy limits in [b, a)
            for o in self._active.get(symbol, []):
                p = float(o.price)  # type: ignore[arg-type]
                dn_order = (o.type is OrderType.STOP and o.side is Side.SELL) or (
                    o.type is OrderType.LIMIT and o.side is Side.BUY
                )
                if dn_order and b <= p <= a and (best is None or p > best.level):
                    best = _PathEvent(o, p)
        return best

    def _slip(self, side: Side, price: float) -> float:
        return price * (1 + side.sign * self.config.slippage_bps / 10_000.0)

    # ------------------------------------------------------------------ fills & accounting
    def _fill(self, order: Order, price: float, liquidity: Liquidity) -> None:
        pos = self.position(order.symbol)
        qty = order.remaining_qty
        if order.reduce_only:
            if not pos.is_open or np.sign(pos.qty) == order.side.sign:
                self._remove_active(order)
                order.status = OrderStatus.CANCELED
                order.reject_reason = "reduce-only: position already closed"
                self._notify_order(order)
                return
            qty = min(qty, abs(pos.qty))
        fee_rate = self.config.fee_maker if liquidity is Liquidity.MAKER else self.config.fee_taker
        self._book_fill(order, qty, price, qty * price * fee_rate, liquidity, final=True)

    def _book_fill(
        self, order: Order, qty: float, price: float, fee: float, liquidity: Liquidity, final: bool = True
    ) -> Fill:
        """Apply an executed quantity to the ledger and notify listeners.

        Shared by the simulator and the live CCXT adapter so that PnL, fee and
        trade accounting are identical in backtest, paper and live trading.
        """
        pos = self.position(order.symbol)
        before = pos.qty
        closed = self._apply_to_position(pos, order, qty, price, fee)
        order.avg_fill_price = (
            (order.avg_fill_price * order.filled_qty + price * qty) / (order.filled_qty + qty)
            if order.filled_qty + qty > 0
            else price
        )
        order.filled_qty += qty
        order.fee += fee
        if final or order.filled_qty >= order.qty * (1 - 1e-9):
            order.status = OrderStatus.FILLED
            self._remove_active(order)
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
        order.updated_at = self.now
        self.balance -= fee
        self.total_fees += fee
        fill = Fill(order.id, order.symbol, order.side, qty, price, fee, self.now, liquidity, order.role)
        self.fills.append(fill)
        self._notify_order(order)
        for fn in self._fill_listeners:
            fn(order, fill, before, pos.qty, closed)
        return fill

    def _remove_active(self, order: Order) -> None:
        lst = self._active.get(order.symbol)
        if lst and order in lst:
            lst.remove(order)

    def _apply_to_position(
        self, pos: Position, order: Order, qty: float, price: float, fee: float
    ) -> ClosedTrade | None:
        signed = qty * order.side.sign
        closed: ClosedTrade | None = None
        if not pos.is_open or np.sign(pos.qty) == np.sign(signed):
            new_qty = pos.qty + signed
            if not pos.is_open:
                pos.reset_trade_stats()
                pos.opened_at = self.now
                pos.entry_price = price
            else:
                pos.entry_price = (pos.entry_price * abs(pos.qty) + price * qty) / abs(new_qty)
            pos.qty = new_qty
            pos.fees += fee
            pos.entry_qty_total += qty
            pos.entry_notional += qty * price
            pos.max_qty = max(pos.max_qty, abs(pos.qty))
        else:
            close_qty = min(qty, abs(pos.qty))
            direction = float(np.sign(pos.qty))
            pnl = close_qty * (price - pos.entry_price) * direction
            self.balance += pnl
            pos.realized_pnl += pnl
            fee_close = fee * close_qty / qty
            pos.fees += fee_close
            pos.exit_qty_total += close_qty
            pos.exit_notional += close_qty * price
            pos.qty -= direction * close_qty
            if abs(pos.qty) <= EPS * max(1.0, pos.max_qty):
                pos.qty = 0.0
                closed = self._close_trade(pos, order)
            remainder = qty - close_qty
            if remainder > EPS:
                # reversal: open the opposite side with the remaining quantity
                pos.reset_trade_stats()
                pos.opened_at = self.now
                pos.qty = order.side.sign * remainder
                pos.entry_price = price
                pos.fees = fee - fee_close
                pos.max_qty = remainder
                pos.entry_qty_total = remainder
                pos.entry_notional = remainder * price
        return closed

    def _close_trade(self, pos: Position, order: Order) -> ClosedTrade:
        self._trade_seq += 1
        avg_entry = pos.entry_notional / pos.entry_qty_total if pos.entry_qty_total else pos.entry_price
        exit_price = pos.exit_notional / pos.exit_qty_total if pos.exit_qty_total else order.avg_fill_price
        net = pos.realized_pnl - pos.fees - pos.funding
        side = "long" if order.side is Side.SELL else "short"
        reason = order.role.value
        if order.tag:
            reason = f"{reason}: {order.tag}"
        trade = ClosedTrade(
            id=f"t{self._trade_seq}",
            symbol=pos.symbol,
            side=side,
            qty=pos.max_qty,
            entry_price=avg_entry,
            exit_price=exit_price,
            opened_at=pos.opened_at,
            closed_at=self.now,
            pnl=net,
            fees=pos.fees,
            funding=pos.funding,
            return_pct=net / pos.entry_notional * 100.0 if pos.entry_notional else 0.0,
            exit_reason=reason,
            strategy=self.strategy_name,
        )
        self.closed_trades.append(trade)
        pos.reset_trade_stats()
        return trade

    def apply_funding(self, symbol: str, rate: float) -> float:
        """Charge one funding interval. Positive rate: longs pay, shorts receive."""
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open or self.config.exchange_type is not ExchangeType.FUTURES:
            return 0.0
        payment = pos.qty * pos.last_price * rate
        self.balance -= payment
        pos.funding += payment
        self.total_funding += payment
        return payment

    def _check_liquidation(self) -> None:
        if self.config.exchange_type is not ExchangeType.FUTURES:
            return
        notional = sum(p.value() for p in self.positions.values() if p.is_open)
        if notional <= 0:
            return
        if self.equity() <= notional * self.config.maintenance_margin:
            self.liquidated = True
            for sym, pos in list(self.positions.items()):
                if pos.is_open:
                    self.cancel_all(sym)
                    side = Side.SELL if pos.is_long else Side.BUY
                    o = Order(
                        sym,
                        side,
                        OrderType.MARKET,
                        abs(pos.qty),
                        role=OrderRole.CLOSE,
                        reduce_only=True,
                        tag="liquidation",
                    )
                    o.created_at = self.now
                    self.orders[o.id] = o
                    o.status = OrderStatus.OPEN
                    self._fill(o, self._slip(side, pos.last_price), Liquidity.TAKER)


def exchange_state(ex: SimulatedExchange) -> dict:
    """Serializable snapshot of the account (for restarts of paper/live bots)."""
    from dataclasses import asdict

    return {
        "balance": ex.balance,
        "total_fees": ex.total_fees,
        "total_funding": ex.total_funding,
        "trade_seq": ex._trade_seq,
        "now": ex.now,
        "last_price": dict(ex.last_price),
        "positions": {s: asdict(p) for s, p in ex.positions.items() if p.is_open},
        "active_orders": [o.to_dict() for o in ex.active_orders()],
    }


def restore_exchange_state(ex: SimulatedExchange, state: dict) -> None:
    ex.balance = float(state.get("balance", ex.balance))
    ex.total_fees = float(state.get("total_fees", 0.0))
    ex.total_funding = float(state.get("total_funding", 0.0))
    ex._trade_seq = int(state.get("trade_seq", 0))
    ex.now = int(state.get("now", 0))
    ex.last_price.update({k: float(v) for k, v in state.get("last_price", {}).items()})
    for sym, pd_ in state.get("positions", {}).items():
        ex.positions[sym] = Position(**pd_)
    for od in state.get("active_orders", []):
        o = Order(
            symbol=od["symbol"],
            side=Side(od["side"]),
            type=OrderType(od["type"]),
            qty=float(od["qty"]),
            price=od.get("price"),
            role=OrderRole(od.get("role", "entry")),
            reduce_only=bool(od.get("reduce_only", False)),
            id=od["id"],
            client_id=od.get("client_id"),
            exchange_id=od.get("exchange_id"),
            status=OrderStatus(od.get("status", "open")),
            created_at=int(od.get("created_at", 0)),
            updated_at=int(od.get("updated_at", 0)),
            filled_qty=float(od.get("filled_qty", 0.0)),
            avg_fill_price=float(od.get("avg_fill_price", 0.0)),
            fee=float(od.get("fee", 0.0)),
            tag=od.get("tag", ""),
        )
        ex.orders[o.id] = o
        ex._active.setdefault(o.symbol, []).append(o)


def _bar_path(bar: np.ndarray) -> tuple[float, float, float, float]:
    """Assumed intrabar path: visit the adverse extreme first (conservative)."""
    o, h, low, c = float(bar[OPEN]), float(bar[HIGH]), float(bar[LOW]), float(bar[CLOSE])
    if c >= o:
        return (o, low, h, c)
    return (o, h, low, c)


@dataclass
class EquityPoint:
    ts: int
    equity: float
    balance: float
    exposure: float
    extra: dict = field(default_factory=dict)
