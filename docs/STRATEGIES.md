# Strategy research results

_Generated 2026-09-26 by `tradebot research run` (then `tradebot research report`)._ Every number below is reproducible from the free datasets (`tradebot data free`).

## Leaderboard

Costs are included everywhere: BTC spot 0.10% fee + 5 bps slippage per side; altcoins 0.10% + 15 bps; perpetual futures 0.02%/0.05% maker/taker + 2-3 bps + 0.01% funding per 8h. **OOS** = walk-forward out-of-sample (parameters re-optimised on a rolling window, then traded blind). **DSR** = Deflated Sharpe Ratio (probability the Sharpe is real after accounting for every variant tried). **PBO** = Probability of Backtest Overfitting (lower is better).

| Strategy | Verdict | CAGR | Sharpe | Max DD | OOS Sharpe | OOS CAGR | OOS Max DD | DSR | PBO | Sharpe @2× costs | B&H Sharpe | B&H Max DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| [TSMOM + volatility targeting - BTC daily (spot)](#trend-voltarget-btc-1d) | ✅ Recommended | 50.0% | 1.58 | -25.5% | 1.10 | 19.6% | -18.3% | 1.00 | 0.21 | 1.45 | 1.04 | -83.4% |
| [TSMOM + volatility targeting - BTC daily (perp futures, up to 2x)](#trend-voltarget-btc-1d-futures) | ✅ Recommended | 88.4% | 1.55 | -40.6% | 1.06 | 33.2% | -34.8% | 1.00 | 0.08 | 1.48 | 1.04 | -83.4% |
| [Donchian breakout (Turtle) + trailing ATR stop - BTC 4h](#donchian-btc-4h) | ✅ Recommended | 33.1% | 1.19 | -24.8% | 0.77 | 20.4% | -31.6% | 1.00 | 0.21 | 0.99 | 1.04 | -83.9% |
| [Donchian breakout (Turtle) - BTC daily](#donchian-btc-1d) | ⚠️ Conditional | 34.1% | 1.04 | -47.9% | 0.75 | 23.9% | -56.5% | 0.99 | 0.41 | 0.99 | 1.04 | -83.4% |
| [Diversified trend - top-20 coins by market cap (point-in-time)](#multi-asset-trend-1d) | ✅ Recommended | 65.8% | 1.88 | -29.6% | 0.96 | 25.3% | -47.7% | 1.00 | 0.29 | 1.68 | 1.02 | -83.8% |
| [Cross-sectional momentum rotation + BTC regime filter - top-20 coins](#momentum-rotation-1d) | ⚠️ Conditional | 107.2% | 1.38 | -66.0% | 0.96 | 55.2% | -77.1% | 1.00 | 0.15 | 1.27 | 1.02 | -83.8% |
| [Volatility breakout - BTC 1h on low-fee perp futures](#vol-breakout-btc-1h-futures) | ⚠️ Conditional | 39.5% | 1.31 | -54.7% | 1.18 | 27.9% | -36.8% | 1.00 | 0.15 | 0.69 | 1.04 | -83.9% |
| [Volatility breakout - BTC 1h on spot (0.10% fees)](#vol-breakout-btc-1h-spot) | ❌ Not recommended | 17.0% | 0.70 | -73.3% | 0.72 | 14.9% | -48.5% | 0.75 | 0.39 | -0.65 | 1.04 | -83.9% |
| [JEV quant consensus (LLM off) - BTC daily](#jev-consensus-btc-1d) | ✅ Recommended | 45.2% | 1.50 | -27.1% | 1.17 | 20.7% | -17.1% | 1.00 | 0.17 | 1.42 | 1.04 | -83.4% |
| [RSI(2) mean reversion (Connors) - BTC daily [negative control]](#rsi2-btc-1d) | ❌ Not recommended | 7.2% | 0.40 | -33.5% | 0.10 | -0.2% | -32.3% | 0.70 | 0.81 | 0.30 | 1.04 | -83.4% |
| [Grid bot - BTC 1h (spot)](#grid-btc-1h) | ❌ Not recommended | 25.7% | 0.72 | -69.7% | 0.34 | 5.9% | -53.7% | 0.96 | 0.04 | 0.64 | 1.04 | -83.9% |
| [DCA bot - weekly buys over one year, BTC daily](#dca-btc-1d) | ❌ Not recommended | 63.5% | 1.08 | -83.4% | — | — | — | 1.00 | — | 1.08 | 1.04 | -83.4% |

## Portfolio of the recommended strategies

Blending one variant of each recommended strategy family (trend_voltarget_btc_1d, donchian_btc_4h, multi_asset_trend_1d) with inverse 90-day volatility, rebalanced daily: **CAGR 50.9%, Sharpe 1.92, max drawdown -19.6%**, volatility 22.8%. This is an in-sample blend of full-period backtests - an illustration of diversification, not an out-of-sample result.

## Methodology

Each study runs: (1) a full-period backtest with *a-priori* parameters from the literature; (2) sub-period stability; (3) a parameter grid -> robustness share, heatmap, Deflated Sharpe Ratio; (4) PBO via combinatorially symmetric cross-validation; (5) walk-forward analysis with robust (plateau) parameter selection; (6) cost stress at 0/1/2/3x; (7) a 20-day block bootstrap; (8) a look-ahead bias check. A strategy is **recommended** only if it passes every gate:

- full-period Sharpe > benchmark-adjusted floor 0.8
- beats buy & hold on Sharpe
- walk-forward OOS Sharpe > 0.7
- Deflated Sharpe Ratio > 0.90
- Sharpe > 0.5 at 2x costs
- >= 60% of parameter grid with Sharpe > 0.5
- PBO < 0.5
- positive Sharpe in every sub-period
- edge still alive: Sharpe > 0.3 since 2022
- max drawdown better than -50%
- walk-forward OOS max drawdown better than -50%
- no look-ahead bias detected

## TSMOM + volatility targeting - BTC daily (spot)

<a id="trend-voltarget-btc-1d"></a>**Verdict: ✅ Recommended** · strategy `TrendVolTarget` · 1d · parameters `{"base_lookback_days": 7, "n_horizons": 6, "target_vol": 0.5, "vol_window_days": 30, "max_leverage": 1.0, "band": 0.1, "long_only": true}`

Moskowitz-Ooi-Pedersen time-series momentum across 1w-32w horizons; exposure scaled to 50% vol.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 50.0% | 60.7% |
| Sharpe | 1.58 | 1.04 |
| Max drawdown | -25.5% | -83.4% |
| Sortino / Calmar | 2.64 / 1.96 | |
| Trades / win rate / profit factor | 181 / 34.2% / 2.38 | |
| Time in market | 53% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 19.6% / 1.10 / -18.3% | |
| Deflated Sharpe / PBO | 1.00 / 0.21 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 127.0% | 2.50 | -21.2% | 2.08 | -45.7% |
| 2018-2022 | 49.7% | 1.54 | -20.6% | 0.78 | -81.5% |
| 2022-2027 | 15.5% | 0.77 | -25.5% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.71, 1× → 1.58, 2× → 1.45, 3× → 1.32.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 9.8% / 49.4% / 114.4%; P(losing first year) 10%.  
Look-ahead check: clean (3 indicators, 15 decisions replayed).

## TSMOM + volatility targeting - BTC daily (perp futures, up to 2x)

<a id="trend-voltarget-btc-1d-futures"></a>**Verdict: ✅ Recommended** · strategy `TrendVolTarget` · 1d · parameters `{"base_lookback_days": 7, "n_horizons": 6, "target_vol": 0.8, "vol_window_days": 30, "max_leverage": 2.0, "band": 0.1, "long_only": true}`

Same signal, higher risk budget on perpetual futures; pays 0.01%/8h funding while long.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 88.4% | 60.7% |
| Sharpe | 1.55 | 1.04 |
| Max drawdown | -40.6% | -83.4% |
| Sortino / Calmar | 2.60 / 2.18 | |
| Trades / win rate / profit factor | 182 / 33.5% / 1.80 | |
| Time in market | 53% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 33.2% / 1.06 / -34.8% | |
| Deflated Sharpe / PBO | 1.00 / 0.08 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 281.9% | 2.51 | -34.8% | 2.08 | -45.7% |
| 2018-2022 | 84.9% | 1.52 | -36.3% | 0.78 | -81.5% |
| 2022-2027 | 22.3% | 0.71 | -40.6% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.62, 1× → 1.55, 2× → 1.48, 3× → 1.40.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 12.5% / 87.9% / 242.1%; P(losing first year) 12%.  
Look-ahead check: clean (3 indicators, 15 decisions replayed).

## Donchian breakout (Turtle) + trailing ATR stop - BTC 4h

<a id="donchian-btc-4h"></a>**Verdict: ✅ Recommended** · strategy `DonchianBreakout` · 4h · parameters `{"entry": 55, "exit": 20, "atr_period": 20, "atr_stop": 3.0, "target_vol": 0.6, "max_leverage": 1.0, "allow_short": false}`

Channel breakouts capture persistent crypto trends; ATR trailing stop bounds losses.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 33.1% | 60.7% |
| Sharpe | 1.19 | 1.04 |
| Max drawdown | -24.8% | -83.9% |
| Sortino / Calmar | 2.12 / 1.33 | |
| Trades / win rate / profit factor | 227 / 44.9% / 1.60 | |
| Time in market | 26% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 20.4% / 0.77 / -31.6% | |
| Deflated Sharpe / PBO | 1.00 / 0.21 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 77.3% | 1.97 | -20.9% | 2.08 | -45.7% |
| 2018-2022 | 29.0% | 1.01 | -19.3% | 0.78 | -81.5% |
| 2022-2027 | 14.0% | 0.71 | -24.6% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.39, 1× → 1.19, 2× → 0.99, 3× → 0.79.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 4.7% / 33.9% / 73.9%; P(losing first year) 13%.  
Look-ahead check: clean (7 indicators, 15 decisions replayed).

## Donchian breakout (Turtle) - BTC daily

<a id="donchian-btc-1d"></a>**Verdict: ⚠️ Conditional** · strategy `DonchianBreakout` · 1d · parameters `{"entry": 20, "exit": 10, "atr_period": 20, "atr_stop": 3.0, "target_vol": 0.6, "max_leverage": 1.0, "allow_short": false}`

Classic 20/10 Turtle system on daily bars.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 34.1% | 60.7% |
| Sharpe | 1.04 | 1.04 |
| Max drawdown | -47.9% | -83.4% |
| Sortino / Calmar | 1.62 / 0.71 | |
| Trades / win rate / profit factor | 74 / 47.3% / 1.66 | |
| Time in market | 38% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 23.9% / 0.75 / -56.5% | |
| Deflated Sharpe / PBO | 0.99 / 0.41 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 94.2% | 2.00 | -26.9% | 2.08 | -45.7% |
| 2018-2022 | 33.3% | 0.96 | -33.0% | 0.78 | -81.5% |
| 2022-2027 | 6.7% | 0.37 | -34.7% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.10, 1× → 1.04, 2× → 0.99, 3× → 0.94.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -3.8% / 34.4% / 95.0%; P(losing first year) 21%.  
Look-ahead check: clean (7 indicators, 15 decisions replayed).

Failed gates: beats buy & hold on Sharpe; walk-forward OOS max drawdown better than -50%.

## Diversified trend - top-20 coins by market cap (point-in-time)

<a id="multi-asset-trend-1d"></a>**Verdict: ✅ Recommended** · strategy `MultiAssetTrend` · 1d · parameters `{"base_lookback_days": 7, "n_horizons": 5, "vol_window_days": 30, "risk_budget": 0.8, "max_weight": 0.35, "rebalance_bars": 1, "min_history_days": 120}`

Trend following is the most robust anomaly across assets; diversifying across coins cuts drawdowns.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 65.8% | 59.2% |
| Sharpe | 1.88 | 1.02 |
| Max drawdown | -29.6% | -83.8% |
| Sortino / Calmar | 3.03 / 2.22 | |
| Trades / win rate / profit factor | 1632 / 29.2% / 1.80 | |
| Time in market | 89% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 25.3% / 0.96 / -47.7% | |
| Deflated Sharpe / PBO | 1.00 / 0.29 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 900.0% | 5.62 | -14.7% | 3.31 | -35.8% |
| 2018-2022 | 56.0% | 1.62 | -23.6% | 0.78 | -81.4% |
| 2022-2027 | 16.4% | 0.77 | -25.7% | 0.48 | -66.9% |

Cost sensitivity (Sharpe): 0× → 2.09, 1× → 1.88, 2× → 1.68, 3× → 1.47.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 16.7% / 64.2% / 149.8%; P(losing first year) 9%.  
Look-ahead check: clean (86 indicators, 0 decisions replayed).

## Cross-sectional momentum rotation + BTC regime filter - top-20 coins

<a id="momentum-rotation-1d"></a>**Verdict: ⚠️ Conditional** · strategy `MomentumRotation` · 1d · parameters `{"lookback_days": 28, "top_k": 5, "regime_ma_days": 100, "rebalance_bars": 7, "min_history_days": 120, "absolute_momentum": true, "regime_symbol": "btc"}`

Liu-Tsyvinski-Wu (2022): momentum prices the crypto cross-section. Regime filter avoids bear markets.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 107.2% | 59.2% |
| Sharpe | 1.38 | 1.02 |
| Max drawdown | -66.0% | -83.8% |
| Sortino / Calmar | 2.25 / 1.62 | |
| Trades / win rate / profit factor | 641 / 42.1% / 1.24 | |
| Time in market | 57% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 55.2% / 0.96 / -77.1% | |
| Deflated Sharpe / PBO | 1.00 / 0.15 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 6603.2% | 4.13 | -46.0% | 3.31 | -35.8% |
| 2018-2022 | 87.8% | 1.22 | -64.0% | 0.78 | -81.4% |
| 2022-2027 | 2.8% | 0.30 | -62.0% | 0.48 | -66.9% |

Cost sensitivity (Sharpe): 0× → 1.52, 1× → 1.38, 2× → 1.27, 3× → 1.13.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -0.9% / 104.2% / 357.4%; P(losing first year) 17%.  
Look-ahead check: clean (1 indicators, 0 decisions replayed).

Failed gates: edge still alive: Sharpe > 0.3 since 2022; max drawdown better than -50%; walk-forward OOS max drawdown better than -50%.

## Volatility breakout - BTC 1h on low-fee perp futures

<a id="vol-breakout-btc-1h-futures"></a>**Verdict: ⚠️ Conditional** · strategy `VolatilityBreakout` · 1h · parameters `{"k": 0.5, "trend_filter_days": 0, "size": 1.0, "target_vol": 0.6}`

Larry Williams breakout: intraday range expansion continues. High turnover -> needs low fees.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 39.5% | 60.7% |
| Sharpe | 1.31 | 1.04 |
| Max drawdown | -54.7% | -83.9% |
| Sortino / Calmar | 2.22 / 0.72 | |
| Trades / win rate / profit factor | 1672 / 51.6% / 1.13 | |
| Time in market | 21% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 27.9% / 1.18 / -36.8% | |
| Deflated Sharpe / PBO | 1.00 / 0.15 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 105.7% | 2.82 | -19.1% | 2.08 | -45.7% |
| 2018-2022 | 49.1% | 1.44 | -25.2% | 0.78 | -81.5% |
| 2022-2027 | 3.2% | 0.25 | -39.4% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.93, 1× → 1.31, 2× → 0.69, 3× → 0.06.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 6.7% / 39.6% / 84.0%; P(losing first year) 12%.  
Look-ahead check: clean (5 indicators, 15 decisions replayed).

Failed gates: edge still alive: Sharpe > 0.3 since 2022; max drawdown better than -50%.

## Volatility breakout - BTC 1h on spot (0.10% fees)

<a id="vol-breakout-btc-1h-spot"></a>**Verdict: ❌ Not recommended** · strategy `VolatilityBreakout` · 1h · parameters `{"k": 0.5, "trend_filter_days": 0, "size": 1.0, "target_vol": 0.6}`

Same rules at retail spot fees - the cost sensitivity control.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 17.0% | 60.7% |
| Sharpe | 0.70 | 1.04 |
| Max drawdown | -73.3% | -83.9% |
| Sortino / Calmar | 1.13 / 0.23 | |
| Trades / win rate / profit factor | 1672 / 47.4% / 1.05 | |
| Time in market | 21% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 14.9% / 0.72 / -48.5% | |
| Deflated Sharpe / PBO | 0.75 / 0.39 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 74.8% | 2.23 | -22.2% | 2.08 | -45.7% |
| 2018-2022 | 27.5% | 0.94 | -33.9% | 0.78 | -81.5% |
| 2022-2027 | -15.6% | -0.50 | -59.3% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 2.03, 1× → 0.70, 2× → -0.65, 3× → -1.98.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -10.3% / 17.2% / 54.0%; P(losing first year) 28%.  
Look-ahead check: clean (5 indicators, 15 decisions replayed).

Failed gates: full-period Sharpe > benchmark-adjusted floor 0.8; beats buy & hold on Sharpe; Deflated Sharpe Ratio > 0.90; Sharpe > 0.5 at 2x costs; positive Sharpe in every sub-period; edge still alive: Sharpe > 0.3 since 2022; max drawdown better than -50%.

## JEV quant consensus (LLM off) - BTC daily

<a id="jev-consensus-btc-1d"></a>**Verdict: ✅ Recommended** · strategy `JEVStrategy` · 1d · parameters `{"target_vol": 0.5, "max_exposure": 1.0, "band": 0.1, "decide_every_bars": 1, "allow_short": false}`

The deterministic analyst-panel baseline that JEV's LLM may confirm or reduce (veto mode). The LLM layer itself can only be judged fairly in forward paper trading (models memorise history).

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 45.2% | 60.7% |
| Sharpe | 1.50 | 1.04 |
| Max drawdown | -27.1% | -83.4% |
| Sortino / Calmar | 2.46 / 1.67 | |
| Trades / win rate / profit factor | 76 / 27.6% / 4.07 | |
| Time in market | 61% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 20.7% / 1.17 / -17.1% | |
| Deflated Sharpe / PBO | 1.00 / 0.17 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 106.9% | 2.37 | -21.8% | 2.08 | -45.7% |
| 2018-2022 | 44.3% | 1.41 | -19.4% | 0.78 | -81.5% |
| 2022-2027 | 16.6% | 0.83 | -22.7% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.57, 1× → 1.50, 2× → 1.42, 3× → 1.35.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile 7.2% / 44.6% / 104.4%; P(losing first year) 12%.  
Look-ahead check: clean (18 indicators, 15 decisions replayed).

## RSI(2) mean reversion (Connors) - BTC daily [negative control]

<a id="rsi2-btc-1d"></a>**Verdict: ❌ Not recommended** · strategy `RSI2MeanReversion` · 1d · parameters `{"rsi_period": 2, "entry_rsi": 10, "trend_ma": 200, "exit_ma": 5}`

Works on equity indices; crypto trends at daily horizons, so we expect it to fail.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 7.2% | 60.7% |
| Sharpe | 0.40 | 1.04 |
| Max drawdown | -33.5% | -83.4% |
| Sortino / Calmar | 0.58 / 0.21 | |
| Trades / win rate / profit factor | 103 / 70.9% / 1.52 | |
| Time in market | 9% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | -0.2% / 0.10 / -32.3% | |
| Deflated Sharpe / PBO | 0.70 / 0.81 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 10.1% | 0.46 | -23.1% | 2.08 | -45.7% |
| 2018-2022 | 2.4% | 0.23 | -32.6% | 0.78 | -81.5% |
| 2022-2027 | 9.4% | 0.66 | -23.2% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 0.50, 1× → 0.40, 2× → 0.30, 3× → 0.19.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -10.4% / 7.2% / 26.1%; P(losing first year) 33%.  
Look-ahead check: clean (3 indicators, 15 decisions replayed).

Failed gates: full-period Sharpe > benchmark-adjusted floor 0.8; beats buy & hold on Sharpe; walk-forward OOS Sharpe > 0.7; Deflated Sharpe Ratio > 0.90; Sharpe > 0.5 at 2x costs; >= 60% of parameter grid with Sharpe > 0.5; PBO < 0.5.

## Grid bot - BTC 1h (spot)

<a id="grid-btc-1h"></a>**Verdict: ❌ Not recommended** · strategy `GridTrading` · 1h · parameters `{"levels": 10, "spacing_pct": 1.5, "stop_below_pct": 0.0}`

Industry-standard retail bot. Short volatility: earns in ranges, holds bags in trends.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 25.7% | 60.7% |
| Sharpe | 0.72 | 1.04 |
| Max drawdown | -69.7% | -83.9% |
| Sortino / Calmar | 1.03 / 0.37 | |
| Trades / win rate / profit factor | 301 / 99.7% / 4.42 | |
| Time in market | 97% | 100% |
| Walk-forward OOS CAGR / Sharpe / max DD | 5.9% / 0.34 / -53.7% | |
| Deflated Sharpe / PBO | 0.96 / 0.04 | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 60.4% | 1.21 | -35.5% | 2.08 | -45.7% |
| 2018-2022 | 18.9% | 0.59 | -69.3% | 0.78 | -81.5% |
| 2022-2027 | 12.9% | 0.51 | -56.7% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 0.81, 1× → 0.72, 2× → 0.64, 3× → 0.56.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -18.9% / 27.3% / 91.9%; P(losing first year) 30%.  
Look-ahead check: clean (0 indicators, 15 decisions replayed).

Failed gates: full-period Sharpe > benchmark-adjusted floor 0.8; beats buy & hold on Sharpe; walk-forward OOS Sharpe > 0.7; max drawdown better than -50%; walk-forward OOS max drawdown better than -50%.

## DCA bot - weekly buys over one year, BTC daily

<a id="dca-btc-1d"></a>**Verdict: ❌ Not recommended** · strategy `DCABot` · 1d · parameters `{"interval_bars": 7, "slices": 52, "dip_ma": 200, "dip_multiplier": 1.0, "take_profit_pct": 0.0}`

Capital deployment rule; equivalent to buy-and-hold after the deployment period.

| | Strategy | Buy & hold |
|---|---:|---:|
| CAGR | 63.5% | 60.7% |
| Sharpe | 1.08 | 1.04 |
| Max drawdown | -83.4% | -83.4% |
| Sortino / Calmar | 1.61 / 0.76 | |
| Trades / win rate / profit factor | 1 / 100.0% / — | |
| Time in market | 100% | 100% |
| Deflated Sharpe / PBO | 1.00 / — | |

| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |
|---|---:|---:|---:|---:|---:|
| 2015-2018 | 275.1% | 2.37 | -35.1% | 2.08 | -45.7% |
| 2018-2022 | 35.0% | 0.78 | -81.5% | 0.78 | -81.5% |
| 2022-2027 | 13.4% | 0.50 | -67.0% | 0.50 | -67.0% |

Cost sensitivity (Sharpe): 0× → 1.08, 1× → 1.08, 2× → 1.08, 3× → 1.08.  
Bootstrap (3-year paths): CAGR 5th/50th/95th percentile -15.9% / 64.4% / 218.1%; P(losing first year) 23%.  
Look-ahead check: clean (1 indicators, 1 decisions replayed).

Failed gates: walk-forward OOS Sharpe > 0.7; >= 60% of parameter grid with Sharpe > 0.5; PBO < 0.5; max drawdown better than -50%; walk-forward OOS max drawdown better than -50%.

---

_Historical simulations are not a promise of future returns. Nothing here is financial advice._
