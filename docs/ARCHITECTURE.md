# Architecture

```
                 ┌──────────────────────── research & backtesting ────────────────────────┐
 data/ (Parquet) │  BacktestEngine ─ SimulatedExchange ─ metrics / walk-forward / PBO / DSR │──► research/results/*.json
   ▲             └─────────────────────────────────────────────────────────────────────────┘
   │ CCXT / free                      same Strategy classes, same ledger code
   │ datasets     ┌────────────────────────── paper / live / replay ───────────────────────┐
   └──────────────│ BotRunner (thread per bot)                                              │
                  │   feed ─► exchange.process_bar ─► RiskManager.update_equity             │
                  │        ─► Strategy/PortfolioStrategy/JEV decide ─► RiskGuardedBroker ─► │──► SimulatedExchange (paper)
                  │        ─► persist (SQLite/Postgres) ─► notify ─► WebSocket events       │    CCXTExchange (live)
                  └──────────────────────────────────────────────────────────────────────────┘
                         ▲ commands (pause/resume/halt/flatten)          │ REST + WS
                  ┌──────┴──────────────── FastAPI (tradebot serve) ────▼──────────────────┐
                  │ bots · backtests · research · data jobs · JEV ask · kill switch         │◄── React dashboard
                  └──────────────────────────────────────────────────────────────────────────┘
```

## Packages (`src/tradebot`)

| Package | Responsibility |
|---|---|
| `core` | Domain types (`Order`, `Fill`, `Position`, `ClosedTrade`), timeframes, candle arrays (CCXT column order), strict-JSON helpers |
| `indicators` | Vectorised, causal indicators with Jesse's `sequential` convention (SMA/EMA/RMA, RSI, ATR, Bollinger, Donchian, Keltner, MACD, ADX, SuperTrend, ...) |
| `strategy` | `Strategy` (Jesse-style route strategy), `PortfolioStrategy` (target weights), sizing utils |
| `strategies` | Built-in strategies, causal **signal models** (`signals.py`) and the registry |
| `execution` | `SimulatedExchange` (matching engine + ledger, used by backtest *and* paper), `CCXTExchange` (live; subclasses the simulator's ledger) |
| `backtest` | Engine (route + portfolio), metrics (PSR/DSR), grid & Optuna search, walk-forward, validation (bootstrap, cost stress, PBO), look-ahead detector |
| `risk` | Pre-trade gates, kill switch state machine, protections, `RiskGuardedBroker` |
| `live` | Candle feeds (exchange / replay), `BotRunner`, `BotManager` |
| `jev` | LLM client, analyst context, prompts, decision schema, engine, `JEVStrategy`, on-demand "ask" |
| `storage` | SQLAlchemy persistence: bots, state, orders, trades, equity, decisions, events, backtests |
| `notify` | Telegram / Discord / webhook alerts (non-blocking) |
| `api` | FastAPI app, background jobs, bundled dashboard |
| `research` | The validation pipeline that produces every published number |
| `data` | Parquet store and downloaders (CCXT, Bitstamp 1m, Coin Metrics) |

## Key design decisions

**One ledger everywhere.** `SimulatedExchange._book_fill` applies every fill to positions,
realised PnL, fees and closed trades. The live `CCXTExchange` calls the same method with
the exchange's reported fills, so PnL accounting cannot drift between modes. The
parity test (`tests/test_live_runtime.py`) replays history through the live runner and
asserts identical trades to the backtester.

**No look-ahead by construction.** Decisions happen at a candle's close; orders placed
then can only fill on later price paths. Intrabar events are timestamped strictly inside
the bar. Indicators are causal (tested by truncation). Strategies can only read candles
up to the current index.

**Intrabar realism.** A bar is walked as open → adverse extreme → other extreme → close;
with 1-minute sub-candles the true path is used (a stop and a target inside one 4h bar
resolve in the order they happened). Gaps fill at the open.

**Equity timestamps.** Equity is recorded at bar close times and bucketed into the
period each close *ends* (1 ms shift), so daily/monthly statistics align across 1h, 4h
and 1d strategies (regression-tested).

**Bot-scoped capital.** Each bot trades a fixed capital allocation with its own ledger
(like Freqtrade's stake), so several bots can share one exchange account; the ledger is
reconciled against exchange balances/positions and a shortfall halts the bot.

**Operator commands run on the bot thread.** Pause / resume / halt / flatten are queued
and executed between candles - never racing the trading loop. Commands issued while a
bot is stopped are written to its persisted state; a halted kill switch survives
restarts (even "fresh" restarts) until explicitly re-armed.

## Strategy API

See [STRATEGY_API.md](STRATEGY_API.md).

## Persistence

SQLite (WAL mode) at `data/tradebot.db` by default; any SQLAlchemy URL via
`TRADEBOT_DB_URL` (e.g. Postgres). A bot's full state (simulated account, open orders,
risk state, JSON-safe strategy vars, replay position) is saved after every candle.
