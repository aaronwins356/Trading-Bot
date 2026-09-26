"""Freqtrade-style protections: temporary trading locks after adverse events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..core.types import ClosedTrade

MIN = 60_000


class Protection:
    """Base class. Durations are in minutes to stay timeframe-agnostic."""

    def on_trade(self, trade: ClosedTrade) -> None:
        raise NotImplementedError

    def is_locked(self, symbol: str, now: int) -> tuple[bool, str]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "name": type(self).__name__,
            **{k: v for k, v in self.__dict__.items() if not k.startswith("_")},
        }

    def to_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, s: dict[str, Any]) -> None:
        pass


@dataclass
class CooldownPeriod(Protection):
    """Lock a symbol for `stop_minutes` after any exit on it."""

    stop_minutes: int = 240
    _until: dict[str, int] = field(default_factory=dict)

    def on_trade(self, trade: ClosedTrade) -> None:
        self._until[trade.symbol] = trade.closed_at + self.stop_minutes * MIN

    def is_locked(self, symbol: str, now: int) -> tuple[bool, str]:
        until = self._until.get(symbol, 0)
        return now < until, f"cooldown until {until}"

    def to_state(self) -> dict[str, Any]:
        return {"until": self._until}

    def load_state(self, s: dict[str, Any]) -> None:
        self._until = {k: int(v) for k, v in s.get("until", {}).items()}


@dataclass
class StoplossGuard(Protection):
    """Lock everything after `trade_limit` stop-loss exits within `lookback_minutes`."""

    lookback_minutes: int = 1440
    trade_limit: int = 4
    stop_minutes: int = 720
    only_per_symbol: bool = False
    _stops: deque = field(default_factory=deque)
    _until: dict[str, int] = field(default_factory=dict)

    def on_trade(self, trade: ClosedTrade) -> None:
        if trade.pnl < 0 and trade.exit_reason.startswith("stop_loss"):
            self._stops.append((trade.closed_at, trade.symbol))
            window = [s for t, s in self._stops if t >= trade.closed_at - self.lookback_minutes * MIN]
            key = trade.symbol if self.only_per_symbol else "*"
            relevant = [s for s in window if self.only_per_symbol is False or s == trade.symbol]
            if len(relevant) >= self.trade_limit:
                self._until[key] = trade.closed_at + self.stop_minutes * MIN

    def is_locked(self, symbol: str, now: int) -> tuple[bool, str]:
        until = max(self._until.get("*", 0), self._until.get(symbol, 0))
        return now < until, f"{self.trade_limit} stop-losses within {self.lookback_minutes}min"


@dataclass
class MaxDrawdownLock(Protection):
    """Lock everything when closed-trade drawdown within the lookback exceeds `max_drawdown_pct`."""

    lookback_minutes: int = 2880
    max_drawdown_pct: float = 15.0
    stop_minutes: int = 1440
    starting_equity: float = 10_000.0
    _trades: deque = field(default_factory=deque)
    _until: int = 0

    def on_trade(self, trade: ClosedTrade) -> None:
        self._trades.append((trade.closed_at, trade.pnl))
        cutoff = trade.closed_at - self.lookback_minutes * MIN
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()
        eq, peak, dd = self.starting_equity, self.starting_equity, 0.0
        for _, pnl in self._trades:
            eq += pnl
            peak = max(peak, eq)
            dd = min(dd, eq / peak - 1.0)
        if dd * 100 <= -self.max_drawdown_pct:
            self._until = trade.closed_at + self.stop_minutes * MIN

    def is_locked(self, symbol: str, now: int) -> tuple[bool, str]:
        return now < self._until, f"closed-trade drawdown > {self.max_drawdown_pct}%"


@dataclass
class LowProfitSymbols(Protection):
    """Lock a symbol whose trades within the lookback returned less than `required_profit_pct`."""

    lookback_minutes: int = 4320
    trade_limit: int = 3
    required_profit_pct: float = 0.0
    stop_minutes: int = 1440
    _trades: dict[str, deque] = field(default_factory=dict)
    _until: dict[str, int] = field(default_factory=dict)

    def on_trade(self, trade: ClosedTrade) -> None:
        q = self._trades.setdefault(trade.symbol, deque())
        q.append((trade.closed_at, trade.return_pct))
        cutoff = trade.closed_at - self.lookback_minutes * MIN
        while q and q[0][0] < cutoff:
            q.popleft()
        if len(q) >= self.trade_limit and sum(r for _, r in q) < self.required_profit_pct:
            self._until[trade.symbol] = trade.closed_at + self.stop_minutes * MIN

    def is_locked(self, symbol: str, now: int) -> tuple[bool, str]:
        until = self._until.get(symbol, 0)
        return now < until, f"low profit on {symbol}"


PROTECTIONS = {c.__name__: c for c in (CooldownPeriod, StoplossGuard, MaxDrawdownLock, LowProfitSymbols)}


def build_protections(specs: list[dict[str, Any]] | None) -> list[Protection]:
    out: list[Protection] = []
    for spec in specs or []:
        spec = dict(spec)
        name = spec.pop("method", None) or spec.pop("name", None)
        if name not in PROTECTIONS:
            raise ValueError(f"unknown protection {name!r}; available: {', '.join(PROTECTIONS)}")
        out.append(PROTECTIONS[name](**spec))
    return out
