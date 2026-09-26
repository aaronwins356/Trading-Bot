"""JEV decision engine: quant consensus -> open-weight LLM -> authority rules -> guardrails.

Authority modes (how much the LLM may change the quant consensus):

* ``advisory`` - the consensus is executed; the LLM's view is only journaled.
* ``veto``     - the LLM may confirm or *reduce* the consensus (down to flat), never
                 increase it or flip its sign. Default: an LLM mistake can only make
                 the bot more cautious than a validated strategy.
* ``full``     - the LLM's target is executed, clipped to ``max_exposure`` and
                 long-only on spot.

Low confidence (< ``min_confidence``), invalid output, timeouts or an unreachable
server fall back to the consensus. The risk manager still vets every order.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .context import MarketContext
from .llm import LLMClient, LLMError
from .prompts import system_prompt, user_prompt
from .schema import FinalDecision, LLMDecision

log = logging.getLogger("tradebot.jev")

Journal = Callable[[FinalDecision, MarketContext, str], None]


class JEVEngine:
    def __init__(
        self,
        llm: LLMClient | None = None,
        authority: str = "veto",
        min_confidence: float = 0.5,
        max_exposure: float = 1.0,
        allow_short: bool = False,
        reasoning_effort: str = "medium",
        journal: Journal | None = None,
    ) -> None:
        if authority not in ("advisory", "veto", "full"):
            raise ValueError("authority must be advisory | veto | full")
        self.llm = llm
        self.authority = authority
        self.min_confidence = min_confidence
        self.max_exposure = max_exposure
        self.allow_short = allow_short
        self.reasoning_effort = reasoning_effort
        self.journal = journal
        self.history: list[FinalDecision] = []
        self.failures = 0

    @property
    def uses_llm(self) -> bool:
        return self.llm is not None

    def _clip(self, x: float) -> float:
        lo = -self.max_exposure if self.allow_short else 0.0
        return float(min(max(x, lo), self.max_exposure))

    def apply_authority(self, consensus: float, d: LLMDecision) -> tuple[float, str, str]:
        """Combine consensus and LLM output. Returns (exposure, source, note)."""
        if self.authority == "advisory":
            return self._clip(consensus), "consensus", "advisory mode: LLM view journaled only"
        if d.confidence < self.min_confidence:
            return (
                self._clip(consensus),
                "fallback",
                f"LLM confidence {d.confidence:.2f} < {self.min_confidence}",
            )
        llm_x = self._clip(d.target_exposure)
        if self.authority == "full":
            return llm_x, "llm", ""
        # veto: same direction -> the smaller magnitude; opposite direction or flat -> flat
        c = self._clip(consensus)
        if c == 0:
            return 0.0, "consensus", "consensus is flat; veto mode cannot open positions"
        if llm_x == 0 or math.copysign(1, c) != math.copysign(1, llm_x):
            return 0.0, "llm-veto", "LLM vetoed the consensus direction -> flat"
        final = math.copysign(min(abs(c), abs(llm_x)), c)
        src = "llm-veto" if abs(final) < abs(c) - 1e-9 else "llm"
        return final, src, "reduced by LLM" if src == "llm-veto" else "LLM confirmed consensus"

    def decide(self, ctx: MarketContext, ts: int = 0) -> FinalDecision:
        consensus = self._clip(ctx.consensus)
        if self.llm is None:
            fd = FinalDecision(
                ts=ts,
                symbol=ctx.asset,
                consensus=consensus,
                final_exposure=consensus,
                authority=self.authority,
                source="consensus",
            )
            self._record(fd, ctx, "")
            return fd
        ctx_d = ctx.to_dict()
        ctx_d["limits"] = {
            **ctx_d.get("limits", {}),
            "max_exposure": self.max_exposure,
            "allow_short": self.allow_short,
        }
        sys_p = system_prompt(self.reasoning_effort)
        usr_p = user_prompt(ctx_d, self.authority)
        raw = ""
        try:
            reply = self.llm.chat_json(sys_p, usr_p)
            raw = reply.raw
            d = LLMDecision.model_validate(reply.data)
            final, src, note = self.apply_authority(consensus, d)
            self.failures = 0
            fd = FinalDecision(
                ts=ts,
                symbol=ctx.asset,
                consensus=consensus,
                llm=d,
                final_exposure=final,
                authority=self.authority,
                source=src,  # type: ignore[arg-type]
                note=note,
                latency_ms=reply.latency_ms,
                model=reply.model,
                cached=reply.cached,
            )
        except (LLMError, ValidationError, TypeError, ValueError) as exc:
            self.failures += 1
            log.warning("JEV falling back to consensus: %s", exc)
            fd = FinalDecision(
                ts=ts,
                symbol=ctx.asset,
                consensus=consensus,
                final_exposure=consensus,
                authority=self.authority,
                source="fallback",
                note="LLM unavailable or invalid output -> quant consensus",
                error=str(exc)[:500],
                model=self.llm.model,
            )
        self._record(fd, ctx, raw)
        return fd

    def _record(self, fd: FinalDecision, ctx: MarketContext, raw: str) -> None:
        self.history.append(fd)
        del self.history[:-500]
        if self.journal is not None:
            try:
                self.journal(fd, ctx, raw)
            except Exception as exc:  # journaling must never break trading
                log.warning("JEV journal failed: %s", exc)

    def recent_for_context(self, n: int = 3) -> list[dict[str, Any]]:
        out = []
        for fd in self.history[-n:]:
            out.append(
                {
                    "final_exposure": round(fd.final_exposure, 3),
                    "consensus": round(fd.consensus, 3),
                    "source": fd.source,
                    "rationale": (fd.llm.rationale if fd.llm else "")[:160],
                }
            )
        return out


def engine_from_settings(
    settings: Any, journal: Journal | None = None, cache_dir: Any = None, allow_short: bool = False
) -> JEVEngine:
    llm = LLMClient.from_settings(settings, cache_dir) if settings.enabled else None
    return JEVEngine(
        llm=llm,
        authority=settings.authority,
        min_confidence=settings.min_confidence,
        max_exposure=settings.max_exposure,
        allow_short=allow_short,
        reasoning_effort=settings.reasoning_effort,
        journal=journal,
    )
