# Writing strategies

Two kinds of strategies share the same engine, risk layer and dashboard.

## 1. Route strategies (Jesse-style)

One instance per symbol. If you know [Jesse](https://github.com/jesse-ai/jesse), you
know this API: port a Jesse strategy by changing imports.

```python
from tradebot import indicators as ta
from tradebot.strategy import utils
from tradebot.strategy.base import Strategy


class GoldenCross(Strategy):
    def hyperparameters(self):
        return [
            {"name": "fast", "type": int, "min": 10, "max": 100, "default": 50},
            {"name": "slow", "type": int, "min": 100, "max": 300, "default": 200},
        ]

    # optional: compute indicators once, vectorised and causal (Freqtrade-style)
    def precompute(self, candles):
        return {
            "fast": ta.sma(candles, self.hp["fast"], sequential=True),
            "slow": ta.sma(candles, self.hp["slow"], sequential=True),
        }

    def should_long(self) -> bool:
        return self.ind("fast") > self.ind("slow")

    def go_long(self):
        qty = utils.size_to_qty(self.available_margin * 0.99, self.price, fee_rate=self.fee_rate)
        self.buy = qty, self.price                 # price == current -> MARKET
        self.stop_loss = qty, self.price * 0.90    # placed when the entry fills

    def update_position(self):
        if self.ind("fast") < self.ind("slow"):
            self.liquidate()
```

Lifecycle at every candle close: `before()` → cancel stale entries
(`should_cancel_entry`) → `update_position()` if open (changes to `stop_loss`,
`take_profit`, `buy`/`sell` are detected and re-submitted) → `should_short()` /
`should_long()` → `go_long()` / `go_short()` → `filters()` → orders → `after()`.

Order legs: `(qty, price)` or `[(qty, price), ...]`. Price equal to the current price →
market; beyond it → stop; better than it → limit. Events: `on_open_position`,
`on_close_position(order, trade)`, `on_increased_position`, `on_reduced_position`, `on_cancel`.

Useful properties: `price`, `open/high/low/close/volume`, `candles` (trailing window,
columns `[ts, open, high, low, close, volume]` - **note: Jesse orders them
`[ts, open, close, high, low, volume]`**; use the named constants in
`tradebot.core.candles`), `position`, `is_long/is_short/is_open/is_close`, `balance`,
`available_margin`, `portfolio_value`, `fee_rate`, `leverage`, `hp`, `vars`,
`shared_vars`, `trades`, `orders`, `time`, `index`, `get_candles(ex, symbol, tf)`.

Extensions beyond Jesse:

- `order_target_exposure(x, band=0.1)` - move the position to `x` × equity (like
  Zipline's `order_target_percent`); ideal for volatility-targeted strategies.
- `precompute()` + `self.ind(name)` - vectorised indicators, checked by `tradebot lookahead`.
- `signal()` - expose a [-1, 1] view for the JEV analyst panel.

## 2. Portfolio strategies (target weights)

```python
from tradebot.strategy.portfolio import PortfolioStrategy


class TopMomentum(PortfolioStrategy):
    rebalance_band = 0.03   # skip trades smaller than 3% of equity

    def hyperparameters(self):
        return [{"name": "rebalance_bars", "type": int, "min": 1, "max": 30, "default": 7}]

    def target_weights(self) -> dict[str, float]:
        scores = {s: self.closes(s)[-1] / self.closes(s)[-29] - 1 for s in self.tradable(min_history=60)}
        top = sorted(scores, key=scores.get, reverse=True)[:3]
        return {s: 1 / 3 for s in top}
```

The engine converts weights to orders (sells first to free margin), respects the band,
caps gross exposure at the account leverage minus a fee buffer, and handles symbols
whose data ends (delisting). An optional point-in-time universe can be injected via
`vars["universe"] = {symbol: bool_array}`.

## Testing your strategy

```bash
tradebot backtest my_package.my_module:GoldenCross --timeframe 1d --start 2018-01-01
tradebot lookahead my_package.my_module:GoldenCross         # must report has_bias: false
tradebot walkforward my_package.my_module:GoldenCross --grid '{"fast":[20,50],"slow":[100,200]}'
```

Then paper-trade it: add a bot with `strategy: my_package.my_module:GoldenCross`.
