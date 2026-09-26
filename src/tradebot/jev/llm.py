"""OpenAI-compatible chat client for open-weight models.

Works with any server exposing ``POST /v1/chat/completions``:

* Ollama        ``http://localhost:11434/v1``  (``ollama pull gpt-oss:20b``)
* vLLM          ``http://localhost:8000/v1``
* LM Studio     ``http://localhost:1234/v1``
* llama.cpp     ``http://localhost:8080/v1`` (``llama-server``)
* TGI / hosted open-model APIs (Together, Groq, OpenRouter, ...) with an API key

Responses are requested in JSON mode, parsed defensively, validated by the caller,
and cached on disk (keyed by model + prompt) so backtests are reproducible and
never pay for the same call twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("tradebot.jev")


class LLMError(Exception):
    pass


@dataclass
class LLMReply:
    data: dict[str, Any]
    raw: str
    latency_ms: int
    cached: bool = False
    model: str = ""


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (handles code fences / chatter)."""
    if not text:
        raise LLMError("empty reply")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # scan for the first balanced {...}
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    raise LLMError(f"no JSON object in reply: {text[:200]!r}")


class LLMClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "gpt-oss:20b",
        api_key: str | None = None,
        timeout_s: float = 90.0,
        temperature: float = 0.2,
        max_tokens: int = 800,
        cache_dir: str | Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._transport = transport

    @classmethod
    def from_settings(cls, s: Any, cache_dir: str | Path | None = None) -> LLMClient:
        key = os.environ.get(s.api_key_env) if getattr(s, "api_key_env", None) else None
        return cls(s.base_url, s.model, key, s.timeout_s, s.temperature, s.max_tokens, cache_dir)

    def _client(self) -> httpx.Client:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(timeout=self.timeout_s, headers=headers, transport=self._transport)

    def _cache_path(self, system: str, user: str) -> Path | None:
        if self.cache_dir is None:
            return None
        h = hashlib.sha256(f"{self.model}\n{self.temperature}\n{system}\n{user}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{h}.json"

    def chat_json(self, system: str, user: str, retries: int = 2) -> LLMReply:
        cp = self._cache_path(system, user)
        if cp is not None and cp.exists():
            d = json.loads(cp.read_text())
            return LLMReply(d["data"], d["raw"], 0, cached=True, model=self.model)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        t0 = time.perf_counter()
        with self._client() as client:
            for attempt in range(retries + 1):
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                }
                try:
                    r = client.post(f"{self.base_url}/chat/completions", json=payload)
                    if r.status_code >= 400:
                        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                    body = r.json()
                    msg = body["choices"][0]["message"]
                    raw = msg.get("content") or ""
                    data = extract_json(raw)
                    reply = LLMReply(
                        data, raw, int((time.perf_counter() - t0) * 1000), model=body.get("model", self.model)
                    )
                    if cp is not None:
                        cp.parent.mkdir(parents=True, exist_ok=True)
                        cp.write_text(json.dumps({"data": data, "raw": raw}))
                    return reply
                except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
                    last_err = exc
                    log.warning("JEV LLM attempt %d failed: %s", attempt + 1, exc)
                    messages = [
                        *messages[:2],
                        {
                            "role": "user",
                            "content": "Your previous reply was not valid. Reply with ONLY the JSON object described, nothing else.",
                        },
                    ]
        raise LLMError(f"LLM call failed after {retries + 1} attempts: {last_err}")

    def health(self) -> dict[str, Any]:
        """Check the endpoint and whether the configured model is served."""
        try:
            with self._client() as client:
                r = client.get(f"{self.base_url}/models")
                r.raise_for_status()
                models = [m.get("id") for m in r.json().get("data", [])]
            return {
                "ok": True,
                "models": models,
                "model_available": self.model in models,
                "base_url": self.base_url,
                "model": self.model,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "base_url": self.base_url, "model": self.model}
