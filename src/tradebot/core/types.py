"""Core trading domain types shared by backtesting, paper and live trading.

The same objects flow through every mode (backtest / paper / live) so that a
strategy that works in research behaves identically in production - the
"research-to-live parity" principle used by NautilusTrader, LEAN and Jesse.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"  # stop-market: triggers a market order when price trades through `price`


class OrderStatus(StrEnum):
    NEW = "new"  # created locally, not yet acknowledged
    OPEN = "open"  # resting on the book / waiting for trigger
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"

    @property
    def is_active(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


class OrderRole(StrEnum):
    """Why an order exists. Used for analytics, protections and exit reasons."""

    ENTRY = "entry"
    INCREASE = "increase"
    REDUCE = "reduce"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    CLOSE = "close"  # liquidate()/manual/kill-switch flatten
    REBALANCE = "rebalance"  # portfolio target-weight adjustments


class Liquidity(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


class ExchangeType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


_order_seq = itertools.count(1)
# run prefix keeps ids unique across restarts (restored orders never collide with new ones)
_RUN = format(int(time.time() * 1000) % (36**7), "x")


def next_order_id(prefix: str = "o") -> str:
    """Unique, monotonically increasing local order id."""
    return f"{prefix}{_RUN}-{next(_order_seq)}"


@dataclass
class Order:
    symbol: str
    side: Side
    type: OrderType
    qty: float
    price: float | None = None  # limit price or stop trigger price
    role: OrderRole = OrderRole.ENTRY
    reduce_only: bool = False
    id: str = field(default_factory=next_order_id)
    client_id: str | None = None  # idempotency key sent to the exchange
    exchange_id: str | None = None
    status: OrderStatus = OrderStatus.NEW
    created_at: int = 0  # ms epoch
    updated_at: int = 0
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    fee: float = 0.0
    tag: str = ""  # free text reason (e.g. "jev: trend flipped")
    reject_reason: str = ""

    @property
    def remaining_qty(self) -> float:
        return max(self.qty - self.filled_qty, 0.0)

    @property
    def is_active(self) -> bool:
        return self.status.is_active

    @property
    def is_filled(self) -> bool:
        return self.status is OrderStatus.FILLED

    @property
    def signed_qty(self) -> float:
        return self.qty * self.side.sign

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("side", "type", "role", "status"):
            d[k] = str(d[k])
        return d


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    timestamp: int
    liquidity: Liquidity = Liquidity.TAKER
    role: OrderRole = OrderRole.ENTRY

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("side", "liquidity", "role"):
            d[k] = str(d[k])
        return d


@dataclass
class Position:
    """Net position in one instrument. qty > 0 long, qty < 0 short."""

    symbol: str
    qty: float = 0.0
    entry_price: float = 0.0
    opened_at: int = 0
    realized_pnl: float = 0.0  # realized since the position was opened (gross of fees)
    fees: float = 0.0  # fees paid since the position was opened
    funding: float = 0.0  # funding paid (+) / received (-) since opened
    max_qty: float = 0.0  # peak absolute size during the trade
    last_price: float = 0.0
    # round-trip bookkeeping (used to build ClosedTrade statistics)
    entry_qty_total: float = 0.0
    entry_notional: float = 0.0
    exit_qty_total: float = 0.0
    exit_notional: float = 0.0

    EPS = 1e-12

    def reset_trade_stats(self) -> None:
        self.realized_pnl = self.fees = self.funding = 0.0
        self.max_qty = 0.0
        self.entry_qty_total = self.entry_notional = 0.0
        self.exit_qty_total = self.exit_notional = 0.0

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > self.EPS

    @property
    def is_close(self) -> bool:
        return not self.is_open

    @property
    def is_long(self) -> bool:
        return self.qty > self.EPS

    @property
    def is_short(self) -> bool:
        return self.qty < -self.EPS

    @property
    def type(self) -> str:
        return "long" if self.is_long else "short" if self.is_short else "close"

    def value(self, price: float | None = None) -> float:
        """Absolute notional value."""
        p = self.last_price if price is None else price
        return abs(self.qty) * p

    def unrealized_pnl(self, price: float | None = None) -> float:
        p = self.last_price if price is None else price
        if not self.is_open:
            return 0.0
        return self.qty * (p - self.entry_price)

    @property
    def pnl(self) -> float:
        return self.unrealized_pnl()

    @property
    def pnl_percentage(self) -> float:
        """Unrealized PnL as % of entry notional (un-leveraged)."""
        if not self.is_open or self.entry_price <= 0:
            return 0.0
        return self.unrealized_pnl() / (abs(self.qty) * self.entry_price) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "qty": self.qty,
            "type": self.type,
            "entry_price": self.entry_price,
            "last_price": self.last_price,
            "value": self.value(),
            "unrealized_pnl": self.unrealized_pnl(),
            "pnl_percentage": self.pnl_percentage,
            "opened_at": self.opened_at,
        }


@dataclass
class ClosedTrade:
    """A round trip: from flat -> position -> flat (reversals close one trade and open another)."""

    id: str
    symbol: str
    side: str  # "long" | "short"
    qty: float  # peak absolute size
    entry_price: float  # average entry
    exit_price: float  # average exit
    opened_at: int
    closed_at: int
    pnl: float  # net of fees and funding
    fees: float
    funding: float
    return_pct: float  # pnl / (entry notional) * 100
    exit_reason: str = ""
    strategy: str = ""
    bars_held: int = 0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def holding_ms(self) -> int:
        return self.closed_at - self.opened_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
