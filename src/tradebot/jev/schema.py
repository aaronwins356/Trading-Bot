from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LLMDecision(BaseModel):
    """What the model must return (validated; anything else is rejected)."""

    action: Literal["buy", "sell", "hold", "reduce", "close", "short"] = "hold"
    target_exposure: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    key_risks: list[str] = Field(default_factory=list)

    @field_validator("rationale")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "")[:600]

    @field_validator("key_risks", mode="before")
    @classmethod
    def _risks(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v[:120]]
        return [str(x)[:120] for x in (v or [])][:6]

    @field_validator("target_exposure", "confidence", mode="before")
    @classmethod
    def _num(cls, v: Any) -> float:
        return float(v)


class FinalDecision(BaseModel):
    """The decision actually executed, after authority rules and guardrails."""

    ts: int
    symbol: str
    consensus: float
    llm: LLMDecision | None = None
    final_exposure: float
    authority: str
    source: Literal["consensus", "llm", "llm-veto", "fallback"]
    note: str = ""
    latency_ms: int = 0
    model: str = ""
    error: str = ""
    cached: bool = False

    @property
    def action(self) -> str:
        return self.llm.action if self.llm else ("buy" if self.final_exposure > 0 else "hold")
