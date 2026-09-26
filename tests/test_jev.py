"""JEV decision engine: authority rules, guardrails, fallbacks, JSON robustness,
caching, and an end-to-end call against a mock OpenAI-compatible server."""

from __future__ import annotations

import json

import httpx
import pytest

from tradebot.backtest.engine import BacktestConfig, run_backtest
from tradebot.jev.context import MarketContext, consensus_exposure
from tradebot.jev.engine import JEVEngine
from tradebot.jev.llm import LLMClient, LLMError, extract_json
from tradebot.jev.schema import LLMDecision
from tradebot.jev.strategy import JEVStrategy

from .conftest import gbm, make_candles


def _ctx(consensus=0.6) -> MarketContext:
    return MarketContext("ASSET", "1d", "t", {"ret_30d_pct": 12.0}, [], consensus, "test", {}, {})


def _mock_llm(reply: dict | str, status: int = 200, calls: list | None = None, tmp_path=None) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(json.loads(request.content))
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(
            status,
            json={
                "model": "gpt-oss:20b",
                "choices": [{"message": {"role": "assistant", "content": content}}],
            },
        )

    return LLMClient(
        "http://mock/v1", "gpt-oss:20b", transport=httpx.MockTransport(handler), cache_dir=tmp_path
    )


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": "x}"}} hope it helps') == {"a": {"b": "x}"}}
    with pytest.raises(LLMError):
        extract_json("no json here")


@pytest.mark.parametrize(
    ("authority", "consensus", "llm", "expected"),
    [
        ("advisory", 0.6, 0.1, 0.6),  # journaled only
        ("veto", 0.6, 0.3, 0.3),  # may reduce
        ("veto", 0.6, 0.9, 0.6),  # may NOT increase
        ("veto", 0.6, -0.5, 0.0),  # opposite direction -> flat
        ("veto", 0.0, 0.8, 0.0),  # cannot open when consensus is flat
        ("full", 0.6, 0.9, 0.9),
        ("full", 0.6, 5.0, 1.0),  # clipped to max exposure (validator caps at 1 anyway)
    ],
)
def test_authority_rules(authority, consensus, llm, expected):
    eng = JEVEngine(authority=authority, max_exposure=1.0)
    d = LLMDecision(target_exposure=max(min(llm, 1), -1), confidence=0.9)
    final, _, _ = eng.apply_authority(consensus, d)
    assert final == pytest.approx(expected)


def test_spot_never_goes_short_even_in_full_mode():
    eng = JEVEngine(authority="full", allow_short=False)
    final, _, _ = eng.apply_authority(0.5, LLMDecision(target_exposure=-1.0, confidence=1.0))
    assert final == 0.0


def test_low_confidence_falls_back_to_consensus():
    eng = JEVEngine(authority="full", min_confidence=0.7)
    final, src, _ = eng.apply_authority(0.4, LLMDecision(target_exposure=1.0, confidence=0.5))
    assert (final, src) == (0.4, "fallback")


def test_end_to_end_decision_with_mock_server(tmp_path):
    calls: list = []
    llm = _mock_llm(
        {
            "action": "reduce",
            "target_exposure": 0.25,
            "confidence": 0.8,
            "rationale": "vol spike",
            "key_risks": ["vol"],
        },
        calls=calls,
    )
    journal = []
    eng = JEVEngine(llm=llm, authority="veto", journal=lambda fd, ctx, raw: journal.append((fd, raw)))
    fd = eng.decide(_ctx(0.6), ts=123)
    assert fd.final_exposure == pytest.approx(0.25)
    assert fd.source == "llm-veto"
    assert fd.llm.rationale == "vol spike"
    body = calls[0]
    assert body["model"] == "gpt-oss:20b"
    assert body["response_format"] == {"type": "json_object"}
    assert "Reasoning: medium" in body["messages"][0]["content"]
    assert journal and journal[0][0].ts == 123


def test_invalid_output_and_server_errors_fall_back(tmp_path):
    eng = JEVEngine(llm=_mock_llm("I think you should buy!!"), authority="full")
    fd = eng.decide(_ctx(0.5))
    assert fd.source == "fallback" and fd.final_exposure == 0.5 and fd.error
    eng = JEVEngine(llm=_mock_llm({"x": 1}, status=500), authority="full")
    assert eng.decide(_ctx(0.3)).final_exposure == 0.3
    # schema violation (exposure out of range) is rejected
    eng = JEVEngine(llm=_mock_llm({"target_exposure": 7, "confidence": 1}), authority="full")
    assert eng.decide(_ctx(0.2)).source == "fallback"


def test_cache_makes_calls_reproducible(tmp_path):
    calls: list = []
    llm = _mock_llm({"target_exposure": 0.5, "confidence": 0.9}, calls=calls, tmp_path=tmp_path)
    r1 = llm.chat_json("sys", "user")
    r2 = llm.chat_json("sys", "user")
    assert len(calls) == 1 and r2.cached and r1.data == r2.data


def test_consensus_is_vol_targeted_and_long_only_on_spot():
    sigs = {"tsmom": 1.0, "donchian": 1.0, "ma_trend": 1.0, "breakout_20d": 0.0}
    x = consensus_exposure(sigs, vol=1.0, target_vol=0.5, max_exposure=1.0, allow_short=False)
    assert x == pytest.approx(0.9 * 0.5)
    assert consensus_exposure({"tsmom": -1.0}, 0.5, 0.5, 1.0, allow_short=False) == 0.0
    assert consensus_exposure({"tsmom": -1.0}, 0.5, 0.5, 1.0, allow_short=True) == pytest.approx(-1.0)


def test_jev_strategy_backtests_in_consensus_mode():
    c = make_candles(gbm(800, mu=0.001, sigma=0.03, seed=5), spread=0.01)
    res = run_backtest(JEVStrategy, c, "1d", config=BacktestConfig())
    assert res.metrics["total_trades"] > 0
    assert res.exposure.max() <= 1.0 + 1e-9
