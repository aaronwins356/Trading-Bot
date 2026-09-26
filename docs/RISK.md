# Risk management & going live

## Controls (every order passes these, in order)

| Gate | Default | Behaviour |
|---|---|---|
| Kill switch `HALTED` | - | Blocks all new exposure until an operator re-arms. Exits always allowed. Survives restarts. |
| `PAUSED` (operator or daily loss) | daily loss 6% | Blocks new exposure; a daily-loss pause clears at the next UTC day |
| Max drawdown | 30% from equity peak | Trips the kill switch (`HALTED`); optional `flatten_on_halt` |
| Order rate limit | 30 / minute | Trips the kill switch - protects against runaway loops |
| Protections | configurable | `CooldownPeriod`, `StoplossGuard`, `MaxDrawdownLock`, `LowProfitSymbols` (Freqtrade-style) |
| Fat-finger band | 10% | Limit/stop entries priced too far from the last price are rejected |
| Max open positions | 10 | |
| Per-symbol exposure | 100% of equity | Orders are clipped to the cap |
| Gross exposure | 100% of equity | Orders are clipped to the cap |
| Min / max order notional | $10 / none | |
| Exchange limits | from the market | Amount/price precision, minimum amount and cost |
| Reconciliation (live) | every 6 candles | Ledger vs exchange balances/positions; mismatch → `HALTED` |

Reduce-only orders (stop-losses, take-profits, liquidations, flatten) are **always**
allowed so the bot can always get flat. Every order attempt, including rejected ones,
is persisted as an audit trail.

Dashboard controls: per-bot **Pause entries**, **Re-arm**, **Halt**, **Flatten**;
global **Kill switch** (halts every bot). API: `POST /api/bots/{id}/command`,
`POST /api/kill-switch`.

## Going-live checklist

1. **Research.** Only use strategies marked *recommended* (or understand exactly why a
   *conditional* one fits you). Re-run `tradebot research run` on fresh data.
2. **Paper trade for weeks** on the same exchange, symbol and timeframe (`mode: paper`).
   Compare the paper equity with a backtest over the same window - they should match
   closely (fees and slippage aside).
3. **Testnet.** `mode: live` with `exchange.sandbox: true` (Binance/Bybit testnets).
4. **API keys:** trade-only permission, **no withdrawals**, IP whitelist. Keys live only
   in environment variables (`.env`), referenced by name in the config.
5. **Small capital first.** `capital` is the amount the bot may use - allocate a fraction
   of the account and scale up only after the live record matches paper.
6. **Tighter limits for live:** e.g. `max_drawdown_pct: 20`, `daily_loss_limit_pct: 5`.
7. **Alerts:** configure Telegram or Discord before going live.
8. **Secure the server:** set `TRADEBOT_API_TOKEN`, keep the API on localhost or behind a
   VPN/reverse proxy with TLS.
9. **Monitor reconciliation events** and the decision journal (for JEV bots).
10. **Know the kill switch.** Practise halting and flattening in paper mode.

## What can still go wrong

- Exchange outages or API changes; withdrawal freezes; exchange insolvency (FTX).
- Slippage in fast markets beyond the modelled 5 bps; gaps through stops.
- Regime change: the research shows edges weakening since 2022 for several strategies.
- Futures: liquidation risk with leverage; funding can be persistently positive for longs.

Backtests are simulations. None of this is financial advice.
