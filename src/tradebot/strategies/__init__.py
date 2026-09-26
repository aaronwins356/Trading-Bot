"""Built-in strategy library and registry."""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from dataclasses import dataclass
from typing import Any

from ..strategy.base import Strategy
from ..strategy.portfolio import PortfolioStrategy
from .bots import DCABot, GridTrading, RSI2MeanReversion
from .donchian import DonchianBreakout
from .rotation import MomentumRotation, MultiAssetTrend
from .trend_voltarget import TrendVolTarget
from .vol_breakout import VolatilityBreakout


@dataclass(frozen=True)
class StrategyInfo:
    name: str
    cls: type
    kind: str  # "route" | "portfolio"
    timeframe: str
    status: str  # "recommended" | "conditional" | "experimental" | "not-recommended" | "ai"
    summary: str

    def to_dict(self) -> dict[str, Any]:
        inst = self.cls()
        params = []
        for p in inst.hyperparameters():
            q = dict(p)
            t = q.get("type")
            q["type"] = t.__name__ if isinstance(t, type) else str(t)
            params.append(q)
        return {
            "name": self.name,
            "kind": self.kind,
            "timeframe": self.timeframe,
            "status": self.status,
            "summary": self.summary,
            "description": self.cls.description,
            "hyperparameters": params,
            "supports_short": getattr(self.cls, "supports_short", False),
        }


REGISTRY: dict[str, StrategyInfo] = {}


def register(cls: type, status: str, summary: str, timeframe: str | None = None) -> type:
    kind = "portfolio" if issubclass(cls, PortfolioStrategy) else "route"
    REGISTRY[cls.__name__] = StrategyInfo(
        cls.__name__, cls, kind, timeframe or cls.timeframe_hint, status, summary
    )
    return cls


register(
    TrendVolTarget,
    "recommended",
    "BTC trend following with volatility targeting. Best risk-adjusted result in research.",
)
register(
    DonchianBreakout,
    "recommended",
    "Turtle breakout with trailing ATR stop. Robust across parameters and sub-periods.",
)
register(
    MultiAssetTrend,
    "recommended",
    "Diversified trend across the top coins. Lowest drawdown of all tested strategies.",
)
register(
    MomentumRotation,
    "conditional",
    "Cross-sectional momentum with BTC regime filter. High return, high drawdown, weaker since 2022.",
)
register(
    VolatilityBreakout,
    "conditional",
    "Intraday breakout. Edge only survives low fees (<= 8 bps/side, futures maker/VIP).",
)
register(
    GridTrading,
    "not-recommended",
    "Classic grid bot. Earns in ranges, bleeds in trends. Included as an industry-standard bot type.",
)
register(DCABot, "not-recommended", "Dollar-cost averaging. A deployment rule, not an edge.")
register(
    RSI2MeanReversion,
    "not-recommended",
    "Negative control: daily mean reversion does not survive costs on BTC.",
)


def _register_jev() -> None:
    try:
        from ..jev.strategy import JEVStrategy
    except ImportError:  # pragma: no cover - optional
        return
    register(
        JEVStrategy,
        "ai",
        "JEV: open-source LLM decision engine over the quant analyst panel, with hard risk guardrails.",
    )


_register_jev()


def get(name: str) -> type:
    """Resolve a strategy by registry name or 'package.module:ClassName' path."""
    if name in REGISTRY:
        return REGISTRY[name].cls
    if ":" in name:
        mod, _, cls_name = name.partition(":")
        cwd = os.getcwd()
        if cwd not in sys.path:  # allow `tradebot backtest mymodule:MyStrategy` from a project folder
            sys.path.insert(0, cwd)
        cls = getattr(importlib.import_module(mod), cls_name)
        if not (inspect.isclass(cls) and issubclass(cls, Strategy | PortfolioStrategy)):
            raise TypeError(f"{name} is not a Strategy/PortfolioStrategy")
        return cls
    raise KeyError(f"unknown strategy '{name}'. Available: {', '.join(REGISTRY)}")


def is_portfolio(cls: type) -> bool:
    return issubclass(cls, PortfolioStrategy)


__all__ = [
    "REGISTRY",
    "DCABot",
    "DonchianBreakout",
    "GridTrading",
    "MomentumRotation",
    "MultiAssetTrend",
    "RSI2MeanReversion",
    "StrategyInfo",
    "TrendVolTarget",
    "VolatilityBreakout",
    "get",
    "is_portfolio",
    "register",
]
