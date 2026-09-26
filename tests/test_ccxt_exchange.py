"""Live CCXT adapter against a fake exchange: the failure modes that lose real money."""

from __future__ import annotations

import math

import ccxt
import pytest

from tradebot.config import ExchangeSettings
from tradebot.core.types import Order, OrderRole, OrderStatus, OrderType, Side
from tradebot.execution.ccxt_exchange import CCXTExchange


class FakeClient:
    def __init__(self, fee_in_base: bool = False, has_stops: bool = True) -> None:
        self.markets = {
            "BTC/USDT": {
                "base": "BTC",
                "quote": "USDT",
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}},
                "taker": 0.001,
                "maker": 0.001,
                "contractSize": 1,
            }
        }
        self.has = {"createStopMarketOrder": has_stops, "fetchClosedOrders": True}
        self.orders: dict[str, dict] = {}
        self.seq = 0
        self.price = 100.0
        self.fee_in_base = fee_in_base
        self.balances = {"USDT": 10_000.0, "BTC": 0.0}
        self.create_error: Exception | None = None
        self.accept_before_error = False
        self.created_params: list[dict] = []

    def load_markets(self):
        return self.markets

    def amount_to_precision(self, symbol, amount):
        return str(math.floor(amount * 1e6) / 1e6)

    def price_to_precision(self, symbol, price):
        return str(round(price, 2))

    def _fee(self, amount, price):
        if self.fee_in_base:
            return {"cost": amount * 0.001, "currency": "BTC"}
        return {"cost": amount * price * 0.001, "currency": "USDT"}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        params = params or {}
        self.created_params.append(params)
        self.seq += 1
        oid = str(self.seq)
        is_trigger = "triggerPrice" in params or "stopLossPrice" in params
        o = {
            "id": oid,
            "clientOrderId": params.get("clientOrderId"),
            "symbol": symbol,
            "type": type_,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0.0,
            "average": None,
            "fee": None,
        }
        self.orders[oid] = o
        if self.create_error is not None:
            err, self.create_error = self.create_error, None
            if not self.accept_before_error:
                del self.orders[oid]
            raise err
        if type_ == "market" and not is_trigger:
            self._fill(oid, amount, self.price)
        return dict(o)

    def _fill(self, oid, amount, price):
        o = self.orders[oid]
        prev = o["filled"]
        o["filled"] = prev + amount
        o["average"] = ((o["average"] or 0) * prev + price * amount) / o["filled"]
        fee = self._fee(o["filled"], o["average"])
        o["fee"] = fee
        o["status"] = "closed" if o["filled"] >= o["amount"] - 1e-12 else "open"
        sign = 1 if o["side"] == "buy" else -1
        net_base = amount - (amount * 0.001 if self.fee_in_base and sign > 0 else 0)
        self.balances["BTC"] += sign * net_base
        self.balances["USDT"] -= sign * amount * price

    def fetch_order(self, oid, symbol):
        return dict(self.orders[oid])

    def cancel_order(self, oid, symbol):
        if self.orders[oid]["status"] == "open":
            self.orders[oid]["status"] = "canceled"

    def fetch_open_orders(self, symbol):
        return [dict(o) for o in self.orders.values() if o["status"] == "open"]

    def fetch_closed_orders(self, symbol, limit=50):
        return [dict(o) for o in self.orders.values() if o["status"] != "open"]

    def fetch_balance(self):
        return {k: {"total": v} for k, v in self.balances.items()}


def make(client: FakeClient | None = None) -> tuple[CCXTExchange, FakeClient]:
    c = client or FakeClient()
    ex = CCXTExchange(
        ExchangeSettings(id="binance"), capital=10_000, bot_id="test-bot", client=c, symbols=["BTC/USDT"]
    )
    ex.mark("BTC/USDT", c.price)
    return ex, c


def test_market_buy_is_idempotent_and_booked():
    ex, c = make()
    seen_status = []
    ex.add_order_listener(lambda o: seen_status.append(o.status))
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    assert seen_status[0] is OrderStatus.NEW  # persisted before the network call
    assert c.created_params[0]["clientOrderId"] == o.client_id and o.client_id.startswith("tb")
    assert o.status is OrderStatus.FILLED
    assert ex.position("BTC/USDT").qty == pytest.approx(1.0)
    assert o.fee == pytest.approx(0.1)  # 0.1% of $100
    assert not ex.reconcile()


def test_fee_charged_in_base_coin_reduces_holdings_so_full_exit_works():
    ex, _ = make(FakeClient(fee_in_base=True))
    ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    pos = ex.position("BTC/USDT")
    assert pos.qty == pytest.approx(0.999)  # we hold 0.999 BTC, not 1.0
    assert pos.entry_price == pytest.approx(100 / 0.999)  # same $100 spent
    assert not ex.reconcile()
    o = ex.submit(Order("BTC/USDT", Side.SELL, OrderType.MARKET, pos.qty, reduce_only=True))
    assert o.status is OrderStatus.FILLED
    assert not ex.position("BTC/USDT").is_open
    assert ex.closed_trades[-1].pnl < 0  # round trip cost ~0.2%


def test_resting_limit_partial_fills_are_booked_incrementally():
    ex, c = make()
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.LIMIT, 2.0, 95.0))
    assert o.status is OrderStatus.OPEN
    c._fill(o.exchange_id, 0.5, 95.0)
    ex.sync()
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert ex.position("BTC/USDT").qty == pytest.approx(0.5)
    c._fill(o.exchange_id, 1.5, 94.0)
    ex.sync()
    assert o.status is OrderStatus.FILLED
    pos = ex.position("BTC/USDT")
    assert pos.qty == pytest.approx(2.0)
    assert pos.entry_price == pytest.approx((0.5 * 95 + 1.5 * 94) / 2)


def test_cancel_race_books_the_fill_that_happened_first():
    ex, c = make()
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.LIMIT, 1.0, 99.0))
    c._fill(o.exchange_id, 1.0, 99.0)  # fills on the exchange before our cancel lands
    ex.cancel(o.id)
    assert o.status is OrderStatus.FILLED
    assert ex.position("BTC/USDT").qty == pytest.approx(1.0)


def test_timeout_after_exchange_accepted_is_recovered_by_client_id():
    ex, c = make()
    c.create_error = ccxt.RequestTimeout("timeout")
    c.accept_before_error = True
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.LIMIT, 1.0, 99.0))
    # found immediately via fetch_open_orders(clientOrderId) -> no duplicate order is ever sent
    assert o.exchange_id == "1" and o.status is OrderStatus.OPEN
    assert len(c.orders) == 1


def test_unknown_outcome_is_reconciled_then_rejected_if_never_seen():
    ex, c = make()
    c.create_error = ccxt.NetworkError("connection reset")
    c.accept_before_error = False
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.LIMIT, 1.0, 99.0))
    assert o.status is OrderStatus.NEW and "unknown" in o.reject_reason
    for _ in range(3):
        ex.sync()
    assert o.status is OrderStatus.REJECTED
    assert not ex.active_orders()


def test_definitive_rejection_is_not_retried():
    ex, c = make()
    c.create_error = ccxt.InsufficientFunds("no money")
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    assert o.status is OrderStatus.REJECTED and "rejected" in o.reject_reason


def test_exchange_minimums_are_enforced_locally():
    ex, _ = make()
    o = ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 0.00001))
    assert o.status is OrderStatus.REJECTED


def test_native_stop_uses_stop_loss_param():
    ex, c = make()
    ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    ex.submit(
        Order("BTC/USDT", Side.SELL, OrderType.STOP, 1.0, 90.0, role=OrderRole.STOP_LOSS, reduce_only=True)
    )
    assert c.created_params[-1].get("stopLossPrice") == 90.0


def test_client_side_stop_emulation_when_exchange_lacks_stops():
    ex, c = make(FakeClient(has_stops=False))
    ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    sl = ex.submit(
        Order("BTC/USDT", Side.SELL, OrderType.STOP, 1.0, 90.0, role=OrderRole.STOP_LOSS, reduce_only=True)
    )
    assert sl.status is OrderStatus.OPEN and sl.exchange_id is None
    c.price = 89.0
    ex.mark("BTC/USDT", 89.0)
    ex.sync()
    assert not ex.position("BTC/USDT").is_open  # emulated stop fired a market exit


def test_reconcile_flags_missing_coins():
    ex, c = make()
    ex.submit(Order("BTC/USDT", Side.BUY, OrderType.MARKET, 1.0))
    c.balances["BTC"] = 0.2  # someone withdrew / sold manually
    problems = ex.reconcile()
    assert problems and "BTC/USDT" in problems[0]
