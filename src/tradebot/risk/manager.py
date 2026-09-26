"""Pre-trade risk controls and the kill switch.

Follows industry guidance (FIA "Best Practices for Automated Trading Risk
Controls", 2024): every order passes an ordered set of fail-closed gates before it
reaches an exchange, and the kill switch has explicit, persistent states with a
*manual* re-arm - an automatic restart would make it a pause button.

Order of gates:
    kill switch -> operator pause / daily-loss pause -> rate limit -> symbol lock
    (protections) -> min/max notional -> fat-finger price band -> max open positions
    -> per-symbol exposure -> gross exposure -> leverage

Reduce-only orders (exits) are always allowed, even when halted, so the bot can
always get flat.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from ..core.types import ClosedTrade, Order, OrderStatus, OrderType, Position
from .protections import Protection

log = logging.getLogger("tradebot.risk")


class KillSwitch(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"  # no new exposure; exits allowed; auto-clears at next UTC day for daily-loss pauses
    HALTED = "halted"  # no new exposure until an operator re-arms; optionally flatten everything


@dataclass
class RiskLimits:
    max_position_pct: float = 1.0  # max |position value| / equity per symbol
    max_gross_exposure: float = 1.0  # sum |position values| / equity
    max_open_positions: int = 10
    min_order_notional: float = 10.0
    max_order_notional: float | None = None
    max_price_deviation_pct: float = 10.0  # limit/stop price vs last price (entries only)
    daily_loss_limit_pct: float = 6.0  # pause entries for the rest of the UTC day
    max_drawdown_pct: float = 30.0  # trip the kill switch (HALTED) from the equity peak
    max_orders_per_minute: int = 30
    flatten_on_halt: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> RiskLimits:
        if not d:
            return cls()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class RiskDecision:
    approved: bool
    reason: str = ""
    qty: float | None = None  # possibly reduced quantity


@dataclass
class RiskEvent:
    ts: int
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None, protections: list[Protection] | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.protections = protections or []
        self.state = KillSwitch.RUNNING
        self.state_reason = ""
        self.peak_equity = 0.0
        self.day = -1
        self.day_start_equity = 0.0
        self.daily_pause = False
        self._order_times: deque[int] = deque()
        self.events: list[RiskEvent] = []
        self.listeners: list[Callable[[RiskEvent], None]] = []

    # ------------------------------------------------------------------ state machine
    def _emit(self, now: int, kind: str, message: str, **data: Any) -> None:
        ev = RiskEvent(now, kind, message, data)
        self.events.append(ev)
        log.warning("[risk] %s: %s", kind, message)
        for fn in self.listeners:
            fn(ev)

    def halt(self, reason: str, now: int = 0) -> None:
        if self.state is not KillSwitch.HALTED:
            self.state, self.state_reason = KillSwitch.HALTED, reason
            self._emit(now, "halt", reason)

    def pause(self, reason: str, now: int = 0) -> None:
        if self.state is KillSwitch.RUNNING:
            self.state, self.state_reason = KillSwitch.PAUSED, reason
            self._emit(now, "pause", reason)

    def resume(self, operator: str = "operator", now: int = 0) -> None:
        """Manual re-arm. Also resets the drawdown peak so the breaker doesn't re-trip instantly."""
        prev = self.state
        self.state, self.state_reason, self.daily_pause = KillSwitch.RUNNING, "", False
        self.peak_equity = 0.0
        self._emit(now, "resume", f"re-armed by {operator} (was {prev})")

    def update_equity(self, equity: float, now: int) -> None:
        """Call at every bar close. May pause (daily loss) or halt (max drawdown)."""
        if not math.isfinite(equity):
            self.halt("equity is not finite - data or accounting error", now)
            return
        day = now // 86_400_000
        if day != self.day:
            self.day, self.day_start_equity = day, equity
            if self.daily_pause and self.state is KillSwitch.PAUSED:
                self.state, self.state_reason, self.daily_pause = KillSwitch.RUNNING, "", False
                self._emit(now, "resume", "new UTC day - daily loss pause cleared")
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            dd = (equity / self.peak_equity - 1.0) * 100
            if dd <= -abs(self.limits.max_drawdown_pct):
                self.halt(f"max drawdown {dd:.1f}% breached (limit {self.limits.max_drawdown_pct}%)", now)
        if self.day_start_equity > 0 and self.state is KillSwitch.RUNNING:
            day_pnl = (equity / self.day_start_equity - 1.0) * 100
            if day_pnl <= -abs(self.limits.daily_loss_limit_pct):
                self.daily_pause = True
                self.pause(
                    f"daily loss {day_pnl:.1f}% breached (limit {self.limits.daily_loss_limit_pct}%)", now
                )

    def on_trade_closed(self, trade: ClosedTrade) -> None:
        for p in self.protections:
            p.on_trade(trade)

    # ------------------------------------------------------------------ pre-trade check
    def check(
        self,
        order: Order,
        *,
        equity: float,
        positions: dict[str, Position],
        last_price: float,
        now: int,
    ) -> RiskDecision:
        pos = positions.get(order.symbol)
        reduces = order.reduce_only or (
            pos is not None
            and pos.is_open
            and (pos.qty > 0) != (order.side.sign > 0)
            and order.qty <= abs(pos.qty) + 1e-12
        )
        if reduces:
            return RiskDecision(True)
        if self.state is KillSwitch.HALTED:
            return RiskDecision(False, f"kill switch HALTED: {self.state_reason}")
        if self.state is KillSwitch.PAUSED:
            return RiskDecision(False, f"trading paused: {self.state_reason}")
        # rate limit (sliding 60s window)
        while self._order_times and now - self._order_times[0] > 60_000:
            self._order_times.popleft()
        if len(self._order_times) >= self.limits.max_orders_per_minute:
            self.halt(
                f"order rate limit exceeded ({self.limits.max_orders_per_minute}/min) - possible runaway loop",
                now,
            )
            return RiskDecision(False, "order rate limit exceeded")
        for p in self.protections:
            locked, why = p.is_locked(order.symbol, now)
            if locked:
                return RiskDecision(False, f"protection {type(p).__name__}: {why}")
        if not (last_price > 0) or equity <= 0:
            return RiskDecision(False, "no valid price/equity")
        px = order.price if order.type is not OrderType.MARKET and order.price else last_price
        if order.type is not OrderType.MARKET and order.price:
            dev = abs(order.price / last_price - 1.0) * 100
            if dev > self.limits.max_price_deviation_pct:
                return RiskDecision(
                    False, f"price {order.price} deviates {dev:.1f}% from last {last_price} (fat-finger band)"
                )
        qty = order.qty
        notional = qty * px
        if self.limits.max_order_notional and notional > self.limits.max_order_notional:
            qty = self.limits.max_order_notional / px
        open_syms = {s for s, p in positions.items() if p.is_open}
        if order.symbol not in open_syms and len(open_syms) >= self.limits.max_open_positions:
            return RiskDecision(False, f"max open positions ({self.limits.max_open_positions}) reached")
        cur_val = abs(pos.qty) * last_price if pos is not None and pos.is_open else 0.0
        max_sym = self.limits.max_position_pct * equity
        if cur_val + qty * px > max_sym * (1 + 1e-9):
            qty = max((max_sym - cur_val) / px, 0.0)
        gross = sum(abs(p.qty) * (p.last_price or last_price) for p in positions.values() if p.is_open)
        max_gross = self.limits.max_gross_exposure * equity
        if gross + qty * px > max_gross * (1 + 1e-9):
            qty = max((max_gross - gross) / px, 0.0)
        if qty * px < self.limits.min_order_notional:
            return RiskDecision(
                False,
                f"order notional {qty * px:.2f} below minimum {self.limits.min_order_notional} after limits",
            )
        self._order_times.append(now)
        if qty < order.qty * (1 - 1e-9):
            return RiskDecision(
                True, f"quantity reduced from {order.qty:.8g} to {qty:.8g} by exposure limits", qty
            )
        return RiskDecision(True)

    def status(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "reason": self.state_reason,
            "peak_equity": round(self.peak_equity, 2),
            "day_start_equity": round(self.day_start_equity, 2),
            "limits": asdict(self.limits),
            "protections": [p.describe() for p in self.protections],
            "events": [asdict(e) for e in self.events[-50:]],
        }

    def to_state(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "reason": self.state_reason,
            "peak_equity": self.peak_equity,
            "day": self.day,
            "day_start_equity": self.day_start_equity,
            "daily_pause": self.daily_pause,
            "protections": [p.to_state() for p in self.protections],
        }

    def load_state(self, s: dict[str, Any]) -> None:
        self.state = KillSwitch(s.get("state", "running"))
        self.state_reason = s.get("reason", "")
        self.peak_equity = float(s.get("peak_equity", 0.0))
        self.day = int(s.get("day", -1))
        self.day_start_equity = float(s.get("day_start_equity", 0.0))
        self.daily_pause = bool(s.get("daily_pause", False))
        for p, ps in zip(self.protections, s.get("protections", []), strict=False):
            p.load_state(ps)


class RiskGuardedBroker:
    """Wraps an exchange (simulated or live): every order must pass the RiskManager first.

    Strategies receive this object as ``self.broker`` - they cannot bypass it.
    """

    def __init__(
        self, inner: Any, risk: RiskManager, on_reject: Callable[[Order, str], None] | None = None
    ) -> None:
        self._inner = inner
        self.risk = risk
        self._on_reject = on_reject

    def submit(self, order: Order) -> Order:
        price = self._inner.last_price.get(order.symbol, 0.0)
        d = self.risk.check(
            order,
            equity=self._inner.equity(),
            positions=self._inner.positions,
            last_price=price,
            now=self._inner.now,
        )
        if not d.approved:
            order.status = OrderStatus.REJECTED
            order.reject_reason = f"risk: {d.reason}"
            order.created_at = order.created_at or self._inner.now
            order.updated_at = self._inner.now
            self._inner.orders[order.id] = order
            notify = getattr(self._inner, "_notify_order", None)
            if notify is not None:  # audit trail: rejected attempts are persisted too
                notify(order)
            if self._on_reject:
                self._on_reject(order, d.reason)
            return order
        if d.qty is not None:
            order.qty = d.qty
            order.tag = (order.tag + " | " if order.tag else "") + d.reason
        return self._inner.submit(order)

    def __getattr__(self, name: str) -> Any:  # delegate everything else (positions, equity, ...)
        return getattr(self._inner, name)
