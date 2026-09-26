# JEV - the open-source decision-making AI

JEV is this project's AI decision layer: an **open-weight language model that you run
yourself** decides the target exposure for a market, on top of a panel of validated
quantitative signals - and it can never bypass the risk engine.

```
 quant analysts ──► quant consensus ──► JEV (open LLM) ──► authority rules ──► risk manager ──► exchange
 (validated          (weighted signal ×     (gpt-oss, Qwen,     (advisory / veto /    (kill switch,
  signal models)      vol target)            Llama via Ollama)   full)                  limits, audit)
```

## Why this design

Research on LLM trading agents ([When Agents Trade](https://arxiv.org/abs/2510.11695),
[LiveTradeBench](https://arxiv.org/html/2511.03628)) finds no reliable standalone edge:
most runs underperform buy & hold, and behaviour depends more on the agent
architecture than on the model. So JEV does not *replace* the strategies that passed
our validation - it **reasons over them**:

1. **Analysts** - the same causal signal models as the recommended strategies:
   time-series momentum (1-32 week horizons), Turtle/Donchian breakout state, 100-day
   moving-average trend, and the 20-day high/low breakout. Each outputs a signal in [-1, 1].
2. **Quant consensus** - a weighted average of the analysts, volatility-targeted
   (default 50% annualised) and long-only on spot. This consensus alone is a
   backtestable strategy: it passed every research gate (see `jev_consensus_btc_1d`).
3. **The LLM** receives a compact JSON snapshot - relative market features (returns over
   7/30/90/365 days, distance from moving averages, drawdown from the 1-year high,
   realised volatility and its percentile, RSI, ADX, volume z-score), every analyst's
   signal with a description of its evidence, the consensus, the current position and
   the hard limits - and must answer with strict JSON:
   `{"action", "target_exposure", "confidence", "rationale", "key_risks"}`.
4. **Authority rules** decide how much the model may change the consensus.
5. The **risk manager** vets every resulting order (kill switch, exposure caps, daily
   loss, drawdown halt, fat-finger band, rate limit).

## Authority modes

| Mode | What the LLM can do | Use when |
|---|---|---|
| `advisory` | Nothing - its view is journaled next to the executed consensus | Measuring the model with zero risk |
| `veto` (default) | Confirm or **reduce** the consensus, down to flat. It can never increase exposure or flip direction | Paper/live trading: a model mistake can only make you more cautious than a validated strategy |
| `full` | Choose any exposure within `max_exposure` (long-only on spot) | Only after a long forward track record in the journal |

Low confidence (below `min_confidence`), invalid JSON, schema violations, timeouts or an
unreachable server all fall back to the quant consensus. Every decision - the exact
context sent, the raw reply, latency, consensus and final exposure - is stored and shown
in the dashboard's decision journal.

## Running a model

Any server exposing the OpenAI-compatible `POST /v1/chat/completions` works.

**Ollama (recommended, easiest):**
```bash
ollama pull gpt-oss:20b          # OpenAI's open-weight model, Apache-2.0, ~16 GB RAM
ollama serve                     # http://localhost:11434/v1
tradebot jev check               # endpoint + model availability
tradebot jev ask                 # one decision on the latest stored BTC candles
```
Smaller machines: `qwen3:8b`, `llama3.1:8b`, `deepseek-r1:8b`. Bigger: `gpt-oss:120b`.
`gpt-oss` supports configurable reasoning effort (`low`/`medium`/`high`), which JEV sets
through the system prompt (`reasoning_effort` in the config).
See [OpenAI: introducing gpt-oss](https://openai.com/index/introducing-gpt-oss/) and
[ollama.com/library/gpt-oss](https://ollama.com/library/gpt-oss).

**vLLM / LM Studio / llama.cpp** - point `base_url` at their `/v1` endpoint.
**Hosted open-model APIs** (Together, Groq, OpenRouter, ...) - set `base_url` and
`api_key_env` (the *name* of an environment variable holding the key).

**Docker:** `docker compose --profile ai up -d` starts Ollama next to the bot; use
`base_url: http://ollama:11434/v1`.

## Configuration

```yaml
- id: jev-btc-paper
  strategy: JEVStrategy
  symbols: [BTC/USDT]
  timeframe: 1d
  mode: paper
  hp: {target_vol: 0.5, max_exposure: 1.0, band: 0.1}
  jev:
    enabled: true
    base_url: http://localhost:11434/v1
    model: gpt-oss:20b
    authority: veto
    reasoning_effort: medium
    min_confidence: 0.5
    anonymize: true        # hide ticker, dates and price levels from the model
    temperature: 0.2
```

## Evaluating JEV honestly

- **The consensus is backtestable** - `tradebot backtest JEVStrategy` runs it with the
  LLM off. Research results: see [STRATEGIES.md](STRATEGIES.md).
- **The LLM layer is not fairly backtestable.** Models have memorised market history; a
  model asked about 2021 "knows" what happened next. Anonymisation (no ticker, no dates,
  no price levels) reduces but cannot eliminate this. LLM calls are cached on disk
  (`data/jev_cache`) so replays are reproducible and cheap, but treat any LLM backtest as
  optimistic.
- **The clean test is forward paper trading.** Run a JEV bot in `paper` mode with
  `authority: advisory` or `veto` for several weeks, then compare in the decision journal
  what the model changed versus the consensus - and whether those changes helped.

## Safety properties (tested in `tests/test_jev.py`)

- Output must validate against a strict schema (`target_exposure ∈ [-1, 1]`, confidence ∈ [0, 1]).
- Veto mode cannot increase exposure, flip direction, or open a position when the consensus is flat.
- Spot bots are long-only regardless of what the model says.
- Server errors, timeouts and malformed replies fall back to the consensus.
- The journal failing can never break trading.
