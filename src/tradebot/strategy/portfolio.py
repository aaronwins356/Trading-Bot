"""Target-weight portfolio strategies (multi-asset rotation, diversified trend).

Industry-standard "portfolio construction" interface (QuantConnect's
Portfolio Construction model, Zipline's ``order_target_percent``): the strategy
returns desired weights and the engine generates the orders.

    class Rotation(PortfolioStrategy):
        def target_weights(self) -> dict[str, float]:
            scores = {s: self.closes(s)[-1] / self.closes(s)[-29] - 1 for s in self.tradable()}
            top = sorted(scores, key=scores.get, reverse=True)[:3]
            return {s: 1 / 3 for s in top}
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np

from ..core import timeframes as tfs
from ..core.candles import CLOSE, TS


class PortfolioStrategy:
    description: ClassVar[str] = ""
    timeframe_hint: ClassVar[str] = "1d"
    supports_short: ClassVar[bool] = False
    #: skip rebalancing trades smaller than this fraction of equity (turnover control)
    rebalance_band: ClassVar[float] = 0.02

    def __init__(self) -> None:
        self.name: str = type(self).__name__
        self.symbols: list[str] = []
        self.timeframe: str = "1d"
        self.hp: dict[str, Any] = {}
        self.vars: dict[str, Any] = {}
        self.index: int = 0
        self._data: dict[str, np.ndarray] = {}
        self._pos: dict[str, int] = {}  # index of the current candle per symbol (-1 = none yet)
        self._now: int = 0
        self._equity: float = 0.0
        self._weights: dict[str, float] = {}
        self._ind: dict[str, dict[str, np.ndarray]] = {}

    # ------------------------------------------------------------------ hooks
    def hyperparameters(self) -> list[dict[str, Any]]:
        return []

    def precompute(self, data: dict[str, np.ndarray]) -> dict[str, dict[str, np.ndarray]] | None:
        """Causal per-symbol indicators: {symbol: {name: array aligned to that symbol's candles}}."""
        return None

    def should_rebalance(self) -> bool:
        every = int(self.hp.get("rebalance_bars", 1) or 1)
        return self.index % every == 0

    def target_weights(self) -> dict[str, float]:
        raise NotImplementedError

    def signal(self) -> float | None:
        return None

    # ------------------------------------------------------------------ engine wiring
    def _bind(self, data: dict[str, np.ndarray], timeframe: str, hp: dict[str, Any] | None = None) -> None:
        self._data = data
        self.symbols = list(data)
        self.timeframe = timeframe
        defaults = {p["name"]: p.get("default") for p in self.hyperparameters()}
        self.hp = {**defaults, **(hp or {})}
        self._pos = {s: -1 for s in data}
        ind = self.precompute(data)
        self._ind = ind or {}

    # ------------------------------------------------------------------ accessors
    @property
    def time(self) -> int:
        return self._now

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def weights(self) -> dict[str, float]:
        """Current weights (position value / equity, signed)."""
        return dict(self._weights)

    def is_available(self, symbol: str) -> bool:
        """True if the symbol printed a candle that closed exactly now (i.e. is currently trading)."""
        i = self._pos.get(symbol, -1)
        if i < 0:
            return False
        return int(self._data[symbol][i, TS]) + tfs.to_ms(self.timeframe) == self._now

    def tradable(self, min_history: int = 1) -> list[str]:
        return [s for s in self.symbols if self.is_available(s) and self._pos[s] + 1 >= min_history]

    def candles(self, symbol: str) -> np.ndarray:
        i = self._pos.get(symbol, -1)
        return self._data[symbol][: i + 1]

    def closes(self, symbol: str) -> np.ndarray:
        return self.candles(symbol)[:, CLOSE]

    def history_len(self, symbol: str) -> int:
        return self._pos.get(symbol, -1) + 1

    def ind(self, symbol: str, name: str, offset: int = 0) -> float:
        i = self._pos.get(symbol, -1) - offset
        if i < 0 or symbol not in self._ind or name not in self._ind[symbol]:
            return float("nan")
        return float(self._ind[symbol][name][i])
