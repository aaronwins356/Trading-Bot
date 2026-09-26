"""JEVStrategy - trade the decisions of the JEV engine through the normal strategy API.

Without an LLM (backtests, or ``jev.enabled: false``) it trades the quant consensus,
which is a deterministic, backtestable trend ensemble. With an LLM, every decision
is journaled (context, raw reply, final exposure) for audit in the dashboard.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..core import timeframes as tfs
from ..core.candles import CLOSE, TS
from ..strategy.base import Strategy
from . import context as C
from .engine import JEVEngine


class JEVStrategy(Strategy):
    description = (
        "JEV: open-source LLM decision engine over the validated quant-analyst panel, with hard guardrails."
    )
    timeframe_hint = "1d"
    supports_short = True

    def hyperparameters(self) -> list[dict[str, Any]]:
        return [
            {"name": "target_vol", "type": float, "min": 0.2, "max": 1.0, "default": 0.5},
            {"name": "max_exposure", "type": float, "min": 0.2, "max": 3.0, "default": 1.0},
            {"name": "band", "type": float, "min": 0.0, "max": 0.3, "default": 0.1},
            {"name": "decide_every_bars", "type": int, "min": 1, "max": 30, "default": 1},
            {"name": "allow_short", "type": "categorical", "options": [False, True], "default": False},
        ]

    def precompute(self, candles):
        sigs = C.analyst_signals(candles, self.timeframe)
        feats = C.feature_arrays(candles, self.timeframe)
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        from ..strategies import signals

        vol = signals.realized_vol(
            candles[:, CLOSE], max(5, round(30 * bpd)), tfs.bars_per_year(self.timeframe)
        )
        out = {f"sig_{k}": v for k, v in sigs.items()}
        out.update({f"feat_{k}": v for k, v in feats.items()})
        out["vol"] = vol
        return out

    @property
    def engine(self) -> JEVEngine:
        eng = self.vars.get("jev_engine")
        if eng is None:
            allow_short = bool(self.hp.get("allow_short")) and not self.is_spot_trading
            eng = JEVEngine(llm=None, max_exposure=float(self.hp["max_exposure"]), allow_short=allow_short)
            self.vars["jev_engine"] = eng
        return eng

    def _current(self, prefix: str) -> dict[str, float]:
        return {k[len(prefix) :]: self.ind(k) for k in self._ind if k.startswith(prefix)}

    def after(self) -> None:
        if self.index % int(self.hp["decide_every_bars"]) != 0:
            return
        sigs = self._current("sig_")
        feats = self._current("feat_")
        if all(not np.isfinite(v) for v in sigs.values()):
            return  # still warming up
        eng = self.engine
        allow_short = eng.allow_short and not self.is_spot_trading
        consensus = C.consensus_exposure(
            sigs, self.ind("vol"), float(self.hp["target_vol"]), eng.max_exposure, allow_short
        )
        pos = self.position
        eq = self.portfolio_value
        portfolio = {
            "current_exposure": round(self.exposure, 3),
            "unrealized_pnl_pct": round(pos.pnl_percentage, 2) if pos.is_open else 0.0,
            "bars_in_position": int((self.time - pos.opened_at) // tfs.to_ms(self.timeframe))
            if pos.is_open and pos.opened_at
            else 0,
            "equity_vs_start_pct": round((eq / self.vars.setdefault("start_equity", eq) - 1) * 100, 2),
        }
        ctx = C.build_context(
            symbol=self.symbol,
            timeframe=self.timeframe,
            ts_ms=int(self.current_candle[TS]),
            feats=feats,
            sigs=sigs,
            consensus=consensus,
            target_vol=float(self.hp["target_vol"]),
            portfolio=portfolio,
            limits={"max_exposure": eng.max_exposure, "allow_short": allow_short},
            anonymize=bool(self.vars.get("anonymize", True)),
            recent=eng.recent_for_context(),
        )
        fd = eng.decide(ctx, ts=self.time)
        self.vars["last_decision"] = fd
        self.order_target_exposure(fd.final_exposure, band=float(self.hp["band"]), tag=f"jev:{fd.source}")

    def signal(self) -> float | None:
        fd = self.vars.get("last_decision")
        return None if fd is None else float(fd.final_exposure)
