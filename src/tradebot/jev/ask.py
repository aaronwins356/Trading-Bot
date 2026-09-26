"""'Ask JEV': an on-demand decision for any stored market (dashboard / CLI)."""

from __future__ import annotations

from typing import Any

from ..core import timeframes as tfs
from ..core.candles import CLOSE, TS
from ..data.store import DataStore
from ..strategies import signals
from . import context as C
from .engine import engine_from_settings


def ask_jev(
    store: DataStore, exchange: str, symbol: str, timeframe: str, settings: Any, target_vol: float = 0.5
) -> dict[str, Any]:
    candles = store.load(exchange, symbol, timeframe)[-1500:]
    if len(candles) < 250:
        raise FileNotFoundError(f"need at least 250 candles of {symbol} {timeframe}; have {len(candles)}")
    sigs_arr = C.analyst_signals(candles, timeframe)
    feats_arr = C.feature_arrays(candles, timeframe)
    bpd = 86_400_000 / tfs.to_ms(timeframe)
    vol = signals.realized_vol(candles[:, CLOSE], max(5, round(30 * bpd)), tfs.bars_per_year(timeframe))
    i = len(candles) - 1
    sigs, feats = C.latest(sigs_arr, i), C.latest(feats_arr, i)
    engine = engine_from_settings(settings)
    consensus = C.consensus_exposure(sigs, float(vol[i]), target_vol, engine.max_exposure, engine.allow_short)
    ctx = C.build_context(
        symbol=symbol,
        timeframe=timeframe,
        ts_ms=int(candles[i, TS]),
        feats=feats,
        sigs=sigs,
        consensus=consensus,
        target_vol=target_vol,
        portfolio={"current_exposure": 0.0, "note": "on-demand analysis, no open position"},
        limits={"max_exposure": engine.max_exposure, "allow_short": engine.allow_short},
        anonymize=settings.anonymize,
    )
    fd = engine.decide(ctx, ts=int(candles[i, TS]) + tfs.to_ms(timeframe))
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": int(candles[i, TS]) + tfs.to_ms(timeframe),
        "price": float(candles[i, CLOSE]),
        "context": ctx.to_dict(),
        "decision": fd.model_dump(),
        "llm_used": engine.uses_llm,
    }
