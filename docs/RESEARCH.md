# Research: trading bots, industry standards, and what actually makes money

This document is the survey behind the design of this repository: what the major
open-source trading bots do, which *types* of bots exist and when they work, what
the academic evidence says about profitable strategies, and which industry
practices we copied. Our own backtest results are in [STRATEGIES.md](STRATEGIES.md).

> **Bottom line.** The only strategy family that survived every one of our
> validation gates on real data, after costs, is **trend following / momentum
> with volatility targeting** (on BTC and on a diversified basket of coins).
> Mean reversion, grid bots, DCA and intraday seasonality either lost money after
> fees or only worked in-sample. LLM "AI traders" have no demonstrated edge on their
> own, so JEV (our AI) sits *on top of* the validated trend signals and can only
> make them more cautious by default.

---

## 1. The open-source bot landscape

| Project | Language | Focus | What we borrowed |
|---|---|---|---|
| **[Freqtrade](https://github.com/freqtrade/freqtrade)** (~50k★) | Python | Crypto spot/futures, the most popular open-source bot | Dry-run (paper) by default; **protections** (Cooldown, StoplossGuard, MaxDrawdown, LowProfit); **look-ahead analysis** tool; hyperopt; Telegram control; web UI |
| **[Jesse](https://github.com/jesse-ai/jesse)** | Python | Clean strategy API, accurate backtests (1-minute simulation) | The **strategy API** (`should_long`, `go_long`, `update_position`, `self.buy = (qty, price)`, events, `hp`); intrabar fills from 1-minute candles; Optuna optimisation |
| **[Hummingbot](https://hummingbot.org/strategies/v2-strategies/)** | Python/Cython | Market making & arbitrage, 50+ connectors | Controller/executor split; Avellaneda-Stoikov inventory model as a reference for why MM needs order-book data and fee rebates |
| **[NautilusTrader](https://nautilustrader.io/docs/latest/concepts/architecture/)** | Rust + Python | Institutional, deterministic event-driven engine | **Research-to-live parity**: the same engine, event ordering and accounting in backtest and live |
| **QuantConnect LEAN** | C# / Python | Multi-asset institutional platform | The **Alpha → Portfolio construction → Risk → Execution** decomposition (our signal models → strategies/JEV → RiskManager → exchange) |
| **Passivbot / OctoBot / Superalgos** | Python/Rust/JS | Grid & DCA bots, no-code UIs | Grid & DCA bot types (implemented, and shown to underperform) |
| **[FinRL](https://github.com/AI4Finance-Foundation/FinRL), TradingAgents, FinGPT, ai-hedge-fund** | Python | Reinforcement learning and LLM multi-agent traders | The "analyst panel → trader → risk manager" pattern used by JEV, with hard guardrails |
| **[CCXT](https://github.com/ccxt/ccxt)** | Many | Unified API for 100+ exchanges | Our exchange layer (market data, orders, precision, limits) |

Rankings and comparisons: [Gainium - best open-source bots](https://gainium.io/best/open-source),
[best-of algorithmic trading](https://github.com/PlaceNL2026/best-of-algorithmic-trading),
[autotradelab framework review](https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared).

## 2. Types of trading bots - and when each works

| Bot type | How it makes money | When it fails | Verdict from our tests |
|---|---|---|---|
| **Trend following / momentum** | Rides persistent moves; cuts losers, lets winners run | Choppy sideways markets (many small losses) | ✅ Works: best risk-adjusted results, positive in every sub-period |
| **Cross-sectional momentum (rotation)** | Holds the strongest coins | Crashes and momentum reversals; alt-season dependence | ⚠️ Worked 2017-2021, weak since 2022, −70%+ drawdowns |
| **Mean reversion (RSI(2), Bollinger, pairs)** | Fades short-term extremes | Trending markets - crypto trends at daily horizons | ❌ Fails after costs on BTC; ETH/BTC ratio *trends* rather than reverts |
| **Grid bots** | Buys dips / sells rips inside a range (short volatility) | Breakouts: holds losing "bags" in down-trends, sells too early in up-trends | ❌ Sharpe ~0.7, −70% drawdown |
| **DCA bots** | Deploys capital gradually | Not an edge - equals buy & hold after deployment | ❌ Not a strategy |
| **Volatility breakout (intraday)** | Range expansion continues intraday | High turnover: fees destroy it at retail spot fees | ⚠️ Only with ≤ 8 bps/side costs (futures maker/VIP), weaker since 2022 |
| **Seasonality (hour-of-day)** | Documented intraday return patterns (e.g. 21-23h UTC) | Tiny per-trade edge << fees | ❌ Gross Sharpe ~1, net strongly negative |
| **Funding-rate carry (cash-and-carry)** | Long spot + short perpetual, collect funding | Funding flips negative, exchange/counterparty risk, capital intensive | Literature: ~8%/yr at low vol (BIS "Crypto Carry"); cross-venue spreads don't persist ([negative result](https://github.com/ZuShen168/funding_rate_data)); not implemented (needs dual-venue data) |
| **Market making** | Earns the spread, manages inventory | Adverse selection, fee tiers, needs L2 data and low latency | Out of scope for candle-based retail bots |
| **Arbitrage (cross-exchange, triangular)** | Price discrepancies | Latency race vs. HFT firms; transfer delays | Out of scope - not competitive at retail latency |
| **LLM / "AI" traders** | Reasoning over news, fundamentals, indicators | No demonstrated edge; memorised history makes backtests unreliable | Used only as an *overlay* (JEV) with veto authority by default |

Grid/DCA background: [Gainium: DCA vs Grid vs Combo](https://gainium.io/blog/dca-vs-grid-vs-combo-bots-choosing-the-right-strategy).

## 3. Evidence on profitable strategies

**Time-series momentum / trend following** - the most robust anomaly across asset classes.
Moskowitz, Ooi & Pedersen (2012) document it in 58 futures markets; Hurst, Ooi &
Pedersen (2017) show a century of evidence. In crypto:
[A Decade of Evidence of Trend Following Investing in Cryptocurrencies](https://arxiv.org/pdf/2009.12155),
[Time-series momentum strategy for cryptocurrencies](https://www.researchgate.net/publication/374536953_Time_Series_Momentum_Trading_Strategy_for_Cryptocurrencies),
[Systematic trend-following with adaptive portfolio construction (2026)](https://arxiv.org/html/2602.11708v1),
Concretum's [Catching Crypto Trends](https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/)
(Donchian ensembles on BTC and altcoins). Liu & Tsyvinski (2021) find strong time-series
momentum at 1-4 week horizons.

**Cross-sectional momentum** - Liu, Tsyvinski & Wu (2022, *Journal of Finance*),
[Common Risk Factors in Cryptocurrency](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119):
market, size and momentum factors price the crypto cross-section. More recent work
questions its stability: [Cryptocurrency momentum has (not) its moments](https://link.springer.com/article/10.1007/s11408-025-00474-9) -
consistent with our finding that rotation has weakened since 2022.

**Volatility targeting** raises Sharpe ratios and cuts drawdowns because exposure is
already reduced when turbulence arrives: [Man Group](https://www.man.com/insights/the-impact-of-volatility-targeting),
[Research Affiliates](https://www.researchaffiliates.com/publications/press-exclusive/1014-harnessing-volatility-targeting),
[Quantpedia](https://quantpedia.com/an-introduction-to-volatility-targeting/).

**Bitcoin seasonality, trend and mean reversion** - Padysak & Vojtko,
[Seasonality, Trend-following, and Mean reversion in Bitcoin](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4081000),
revisited by [Beluská & Vojtko (2024)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4955617):
BTC trends from 10-day maxima; hours 21-23 UTC carry the highest average returns
([Quantpedia](https://quantpedia.com/are-there-seasonal-intraday-or-overnight-anomalies-in-bitcoin/)).
Concretum's [intraday trend study](https://concretumgroup.com/seasonality-in-bitcoin-intraday-trend-trading/)
reports a *gross* Sharpe ~1.6. Our tests reproduce the gross effects - and show that
retail fees erase them.

**Crypto carry** - Schmeling, Schrimpf & Todorov,
[Crypto Carry (Management Science)](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.05069),
[CEPR summary](https://cepr.org/voxeu/columns/crypto-carry-market-segmentation-and-price-distortions-digital-asset-markets):
large, time-varying carry driven by leveraged trend-chasing demand and limited arbitrage capital.

**LLM trading agents** - live benchmarks show mixed-to-poor results:
[When Agents Trade (WWW 2026)](https://arxiv.org/abs/2510.11695),
[LiveTradeBench](https://arxiv.org/html/2511.03628),
[What LLM trading agents actually do in production](https://arxiv.org/pdf/2609.05663).
Most runs underperform buy & hold; architecture matters more than the model; and
because models memorise market history, backtests of LLM decisions are optimistic.
This is why JEV defaults to **veto** authority over a validated quant baseline.

## 4. Why most backtests lie - and the defences we built

| Pitfall | Defence in this repo |
|---|---|
| **Look-ahead bias** (using data not available yet) | Causal indicators verified by truncation tests; engine only exposes candles up to *now*; `tradebot lookahead` replays decisions on truncated data (Freqtrade-style) |
| **Unrealistic fills** | Orders fill on the *next* price path; adverse-extreme-first intrabar sequencing, optional 1-minute sub-bars (Jesse-style); gap fills at the open; slippage; maker/taker fees; funding; liquidation |
| **Overfitting / data snooping** | Parameter *plateaus* (robust selection), walk-forward out-of-sample testing, **Deflated Sharpe Ratio** ([Bailey & López de Prado](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)), **Probability of Backtest Overfitting** via CSCV ([Bailey et al.](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)) |
| **Survivorship bias** | Coin universe includes dead/collapsed coins (FTT, OMG, BSV, BTG, …) with **point-in-time** top-20 market-cap membership |
| **Cost blindness** | Every result at 0×/1×/2×/3× costs |
| **Luck** | Block-bootstrap confidence intervals; sub-period stability; "edge still alive since 2022" gate |
| **Backtest ≠ live** | Same `Strategy` classes and the same ledger code in backtest, paper and live; a **parity test** asserts replayed live trading reproduces the backtest trade-for-trade |

## 5. Industry-standard engineering practices we copied

- **Event-driven architecture with backtest/live parity** (NautilusTrader, LEAN, Jesse).
- **Paper trading first** (Freqtrade dry-run); live requires explicit configuration and keys.
- **Pre-trade risk gates and a kill switch with manual re-arm** - per the FIA's
  [Best Practices for Automated Trading Risk Controls](https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf):
  order-rate limits (runaway-loop protection), fat-finger price bands, position &
  gross-exposure caps, daily-loss pause, max-drawdown halt, reduce-only exits always allowed.
- **Idempotent client order IDs** persisted *before* sending; "unknown outcome"
  orders reconciled by client ID instead of re-sent (no duplicate orders after timeouts).
- **Reconciliation** of the bot's ledger against exchange balances/positions; a
  discrepancy halts the bot.
- **Exchange-native stops** when supported (protect you if the bot dies); emulated otherwise, with a warning.
- **Secrets only in environment variables**; trade-only API keys without withdrawal rights; token-protected API; localhost binding by default.
- **Audit trail**: every order attempt (including risk rejections), fill, trade, decision and risk event is persisted.
- **Observability**: web dashboard, WebSocket live updates, Telegram/Discord/webhook alerts.
- **Reproducible research**: one command (`tradebot research run`) regenerates every number in the docs and dashboard.

## 6. Data used

- **Bitstamp BTC/USD 1-minute candles, 2012 → today** ([ff137/bitstamp-btcusd-minute-data](https://github.com/ff137/bitstamp-btcusd-minute-data), updated daily) - 7.7M candles, no gaps.
- **Coin Metrics community data** ([coinmetrics/data](https://github.com/coinmetrics/data)) - daily reference rates and market caps for ~45 coins including dead ones.
- Any exchange via CCXT for paper/live trading and your own research (`tradebot data download`).
