"""Live trading through CCXT (100+ exchanges).

``CCXTExchange`` subclasses the simulator so the *ledger* (positions, PnL, fees,
closed trades) is byte-for-byte the same code in backtest, paper and live. Only
order routing differs: orders go to the exchange and fills are booked from the
exchange's reports during :meth:`sync`.

Safety properties:

* **Idempotent client order ids** - persisted (via the order listener) *before*
  the network call; after a crash the bot looks orders up by client id instead of
  re-sending them.
* **Bot-scoped capital** - the bot trades a fixed capital allocation and keeps its
  own position ledger (Freqtrade-style), reconciled against exchange balances /
  positions every sync. A shortfall raises a discrepancy the runner turns into a halt.
* **Exchange-native stops** when supported (``stopLossPrice`` / ``triggerPrice``);
  otherwise stops are emulated client-side and a warning is logged (they cannot
  protect you while the bot is offline).
* Amount/price precision and min-notional limits come from the exchange markets.

Always test with ``sandbox: true`` (exchange testnet) before trading real money.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import ExchangeSettings
from ..core.types import ExchangeType, Liquidity, Order, OrderRole, OrderStatus, OrderType, Side
from .simulated import ExecutionConfig, SimulatedExchange

log = logging.getLogger("tradebot.live")


class ExchangeDiscrepancy(Exception):
    pass


def build_ccxt_client(settings: ExchangeSettings) -> Any:
    import ccxt

    if not hasattr(ccxt, settings.id):
        raise ValueError(f"unknown CCXT exchange id '{settings.id}'")
    opts: dict[str, Any] = {"enableRateLimit": True, **settings.credentials()}
    options = dict(settings.options)
    if settings.market_type == "futures":
        options.setdefault("defaultType", "swap")
    if options:
        opts["options"] = options
    client = getattr(ccxt, settings.id)(opts)
    if settings.sandbox:
        client.set_sandbox_mode(True)
    return client


class CCXTExchange(SimulatedExchange):
    def __init__(
        self,
        settings: ExchangeSettings,
        capital: float,
        bot_id: str,
        leverage: float = 1.0,
        client: Any = None,
        symbols: list[str] | None = None,
    ) -> None:
        etype = ExchangeType.FUTURES if settings.market_type == "futures" else ExchangeType.SPOT
        super().__init__(
            ExecutionConfig(
                starting_balance=capital,
                exchange_type=etype,
                leverage=leverage if etype is ExchangeType.FUTURES else 1.0,
                slippage_bps=0.0,
            )
        )
        self.settings = settings
        self.bot_id = bot_id
        self.client = client or build_ccxt_client(settings)
        self.client.load_markets()
        self._cid_prefix = "tb" + "".join(ch for ch in bot_id if ch.isalnum())[-8:]
        self._cid_seq = int(time.time()) % 100_000
        self._client_stops: set[str] = set()
        self._fee_base_seen: dict[str, float] = {}
        self._quote_seen: dict[str, float] = {}  # gross quote notional booked per order (for partial fills)
        self._unknown: dict[str, int] = {}  # order id -> reconciliation attempts
        self.symbols = symbols or []
        for s in self.symbols:
            if s not in self.client.markets:
                raise ValueError(f"{settings.id} has no market {s}")
        if etype is ExchangeType.FUTURES and leverage > 1:
            for s in self.symbols:
                try:
                    self.client.set_leverage(int(leverage), s)
                except Exception as exc:  # not all exchanges support it via API
                    log.warning("set_leverage(%s, %s) failed: %s", leverage, s, exc)
        taker = None
        if self.symbols:
            taker = self.client.markets[self.symbols[0]].get("taker")
        if taker:
            self.config.fee_taker = float(taker)
            self.config.fee_maker = float(self.client.markets[self.symbols[0]].get("maker") or taker)

    # ------------------------------------------------------------------ helpers
    def _next_cid(self) -> str:
        self._cid_seq += 1
        return f"{self._cid_prefix}{self._cid_seq:x}{int(time.time() * 1000) % 1_000_000:x}"[:32]

    def _market(self, symbol: str) -> dict[str, Any]:
        return self.client.markets[symbol]

    def _amount(self, symbol: str, qty: float) -> float:
        return float(self.client.amount_to_precision(symbol, qty))

    def _price(self, symbol: str, price: float) -> float:
        return float(self.client.price_to_precision(symbol, price))

    def _supports_stops(self) -> bool:
        has = getattr(self.client, "has", {}) or {}
        return bool(
            has.get("createStopMarketOrder")
            or has.get("createTriggerOrder")
            or has.get("createStopLossOrder")
            or has.get("createStopOrder")
        )

    # ------------------------------------------------------------------ order entry
    def submit(self, order: Order) -> Order:
        order.created_at = order.created_at or self.now
        order.updated_at = self.now
        self.orders[order.id] = order
        price = self.last_price.get(order.symbol)
        reason = self._validate(order, price)
        if not reason:
            reason = self._validate_market_limits(order, price or 0.0)
        if reason:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reason
            self._notify_order(order)
            return order
        order.qty = self._amount(order.symbol, order.qty)
        if order.price is not None:
            order.price = self._price(order.symbol, order.price)
        order.client_id = order.client_id or self._next_cid()
        order.status = OrderStatus.NEW
        self._notify_order(order)  # persisted BEFORE the network call (idempotency)
        if order.type is OrderType.STOP and not self._supports_stops():
            order.status = OrderStatus.OPEN
            self._client_stops.add(order.id)
            self._active.setdefault(order.symbol, []).append(order)
            log.warning(
                "%s: emulating stop order client-side (exchange lacks native stops)", self.settings.id
            )
            self._notify_order(order)
            return order
        try:
            resp = self._create(order)
        except Exception as exc:
            if _is_definitive_rejection(exc):
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"exchange rejected: {exc}"
                self._notify_order(order)
                log.error("create_order rejected: %s", exc)
                return order
            existing = self._find_by_client_id(order)
            if existing is None:
                # outcome unknown (timeout / network): keep it pending and reconcile by client id on sync()
                order.status = OrderStatus.NEW
                order.reject_reason = f"unknown outcome, reconciling: {exc}"
                self._unknown[order.id] = 0
                self._active.setdefault(order.symbol, []).append(order)
                self._notify_order(order)
                log.error("create_order outcome unknown (%s); will reconcile by client id", exc)
                return order
            resp = existing  # the request reached the exchange before the error
        order.exchange_id = str(resp.get("id")) if resp.get("id") is not None else None
        order.status = OrderStatus.OPEN
        self._active.setdefault(order.symbol, []).append(order)
        self._notify_order(order)
        self._apply_report(order, resp)
        return order

    def _validate_market_limits(self, order: Order, price: float) -> str:
        m = self._market(order.symbol)
        limits = m.get("limits") or {}
        amt = (limits.get("amount") or {}).get("min")
        cost = (limits.get("cost") or {}).get("min")
        qty = self._amount(order.symbol, order.qty)
        if qty <= 0:
            return "quantity rounds to zero at exchange precision"
        if amt and qty < float(amt):
            return f"quantity {qty} below exchange minimum {amt}"
        px = order.price or price
        if cost and px and qty * px < float(cost) and not order.reduce_only:
            return f"notional {qty * px:.2f} below exchange minimum {cost}"
        return ""

    def _create(self, order: Order) -> dict[str, Any]:
        params: dict[str, Any] = {"clientOrderId": order.client_id}
        if order.reduce_only and self.config.exchange_type is ExchangeType.FUTURES:
            params["reduceOnly"] = True
        side = str(order.side)
        if order.type is OrderType.MARKET:
            return self.client.create_order(order.symbol, "market", side, order.qty, None, params)
        if order.type is OrderType.LIMIT:
            return self.client.create_order(order.symbol, "limit", side, order.qty, order.price, params)
        # stop-market
        if order.role is OrderRole.STOP_LOSS:
            params["stopLossPrice"] = order.price
        else:
            params["triggerPrice"] = order.price
        return self.client.create_order(order.symbol, "market", side, order.qty, None, params)

    def _find_by_client_id(self, order: Order) -> dict[str, Any] | None:
        try:
            for o in self.client.fetch_open_orders(order.symbol):
                if o.get("clientOrderId") == order.client_id:
                    return o
            if getattr(self.client, "has", {}).get("fetchClosedOrders"):
                for o in self.client.fetch_closed_orders(order.symbol, limit=50):
                    if o.get("clientOrderId") == order.client_id:
                        return o
        except Exception as exc:
            log.warning("lookup by client id failed: %s", exc)
        return None

    def cancel(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or not order.is_active:
            return False
        if order.id in self._client_stops or not order.exchange_id:
            self._client_stops.discard(order.id)
            return super().cancel(order_id)
        try:
            self.client.cancel_order(order.exchange_id, order.symbol)
        except Exception as exc:
            log.warning("cancel_order %s failed: %s (will re-check status)", order.exchange_id, exc)
        # catch fills that happened before the cancel landed
        try:
            self._apply_report(order, self.client.fetch_order(order.exchange_id, order.symbol))
        except Exception as exc:
            log.warning("fetch_order after cancel failed: %s", exc)
        if order.is_active:
            return super().cancel(order_id)
        return order.status is OrderStatus.CANCELED

    # ------------------------------------------------------------------ fills
    def _apply_report(self, order: Order, rep: dict[str, Any]) -> None:
        """Book any newly filled quantity reported by the exchange."""
        filled = float(rep.get("filled") or 0.0)
        gross_booked = order.filled_qty + self._fee_base_seen.get(order.id, 0.0)
        new_qty = filled - gross_booked
        if new_qty > 1e-12:
            avg = float(
                rep.get("average")
                or rep.get("price")
                or order.price
                or self.last_price.get(order.symbol, 0.0)
            )
            # incremental price: back out the previously booked part of the average
            prev_quote = self._quote_seen.get(order.id, 0.0)
            inc_px = (avg * filled - prev_quote) / new_qty if avg > 0 and prev_quote > 0 else avg
            self._quote_seen[order.id] = prev_quote + inc_px * new_qty
            fee_quote, fee_base = _split_fees(rep, order.symbol)
            status = str(rep.get("status") or "")
            final = status == "closed" or filled >= order.qty * (1 - 1e-9)
            liq = Liquidity.MAKER if order.type is OrderType.LIMIT else Liquidity.TAKER
            base_inc = (
                max(fee_base - self._fee_base_seen.get(order.id, 0.0), 0.0) if fee_base is not None else 0.0
            )
            if fee_quote is None and fee_base is None:
                fee = (
                    new_qty
                    * inc_px
                    * (self.config.fee_maker if liq is Liquidity.MAKER else self.config.fee_taker)
                )
            else:
                fee = max((fee_quote or 0.0) - order.fee, 0.0)
            book_qty, book_px = new_qty, inc_px
            if base_inc > 0 and order.side is Side.BUY and self.config.exchange_type is ExchangeType.SPOT:
                # fee taken in the coin we bought: we hold fewer coins for the same quote spent
                book_qty = new_qty - base_inc
                book_px = inc_px * new_qty / book_qty
                self._fee_base_seen[order.id] = self._fee_base_seen.get(order.id, 0.0) + base_inc
            elif base_inc > 0:
                fee += base_inc * inc_px
            self._book_fill(order, book_qty, book_px, fee, liq, final=final)
        status = str(rep.get("status") or "")
        if status in ("canceled", "cancelled", "expired", "rejected") and order.is_active:
            order.status = OrderStatus.CANCELED if status != "rejected" else OrderStatus.REJECTED
            order.updated_at = self.now
            self._remove_active(order)
            self._notify_order(order)

    def sync(self) -> None:
        """Poll the exchange for order updates (fills/cancels) and trigger emulated stops."""
        for order in list(self.active_orders()):
            if order.id in self._client_stops:
                px = self.last_price.get(order.symbol)
                if px and (
                    (order.side is Side.SELL and px <= order.price)
                    or (order.side is Side.BUY and px >= order.price)
                ):
                    self._client_stops.discard(order.id)
                    self._remove_active(order)
                    order.status = OrderStatus.CANCELED
                    self._notify_order(order)
                    self.submit(
                        Order(
                            order.symbol,
                            order.side,
                            OrderType.MARKET,
                            order.remaining_qty,
                            role=order.role,
                            reduce_only=order.reduce_only,
                            tag="emulated stop",
                        )
                    )
                continue
            if not order.exchange_id:
                if order.id in self._unknown:
                    found = self._find_by_client_id(order)
                    if found is not None:
                        self._unknown.pop(order.id, None)
                        order.exchange_id = str(found.get("id"))
                        order.status, order.reject_reason = OrderStatus.OPEN, ""
                        self._notify_order(order)
                        self._apply_report(order, found)
                    else:
                        self._unknown[order.id] += 1
                        if self._unknown[order.id] >= 3:
                            self._unknown.pop(order.id, None)
                            self._remove_active(order)
                            order.status = OrderStatus.REJECTED
                            order.reject_reason = "not found on exchange after 3 reconciliation attempts"
                            self._notify_order(order)
                continue
            try:
                self._apply_report(order, self.client.fetch_order(order.exchange_id, order.symbol))
            except Exception as exc:
                log.warning("fetch_order %s failed: %s", order.exchange_id, exc)

    def process_bar(self, symbol: str, bar: Any, sub_bars: Any = None) -> None:
        from ..core.candles import CLOSE

        self.mark(symbol, float(bar[CLOSE]))
        self.sync()

    # ------------------------------------------------------------------ reconciliation
    def reconcile(self) -> list[str]:
        """Compare the bot ledger with the exchange. Returns human-readable discrepancies."""
        problems: list[str] = []
        try:
            if self.config.exchange_type is ExchangeType.SPOT:
                bal = self.client.fetch_balance()
                for sym, pos in self.positions.items():
                    if not pos.is_open:
                        continue
                    base = self._market(sym)["base"]
                    have = float((bal.get(base) or {}).get("total") or 0.0)
                    if have + 1e-9 < abs(pos.qty) * 0.999:
                        problems.append(
                            f"{sym}: ledger holds {pos.qty} {base} but the exchange balance is {have}"
                        )
            else:
                ex_pos = {p["symbol"]: p for p in self.client.fetch_positions(list(self.positions) or None)}
                for sym, pos in self.positions.items():
                    p = ex_pos.get(sym)
                    size = float((p or {}).get("contracts") or 0.0) * float(
                        self._market(sym).get("contractSize") or 1.0
                    )
                    side = (p or {}).get("side")
                    signed = size if side == "long" else -size
                    if pos.is_open and abs(signed - pos.qty) > max(1e-9, abs(pos.qty) * 0.01):
                        problems.append(f"{sym}: ledger position {pos.qty} vs exchange {signed}")
        except Exception as exc:
            problems.append(f"reconciliation failed: {exc}")
        return problems

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int = 500
    ) -> list[list[float]]:
        return self.client.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)


def _split_fees(rep: dict[str, Any], symbol: str) -> tuple[float | None, float | None]:
    """Cumulative fees of an order as (quote amount, base amount).

    Fees charged in a third currency (e.g. BNB) cannot be valued without a price, so
    they return (None, None) and the caller falls back to the configured fee rate.
    """
    fees = rep.get("fees") or ([rep["fee"]] if rep.get("fee") else [])
    fees = [f for f in fees if f and f.get("cost") is not None]
    if not fees:
        return None, None
    base, _, quote = symbol.partition("/")
    quote = quote.split(":")[0]
    q = b = 0.0
    for f in fees:
        cur, cost = f.get("currency"), float(f["cost"])
        if cur == base:
            b += cost
        elif cur in (quote, None):
            q += cost
        else:
            return None, None
    return q, b


def _is_definitive_rejection(exc: Exception) -> bool:
    """Exchange said no (bad params, funds, symbol) - as opposed to a timeout where the outcome is unknown."""
    try:
        import ccxt

        return isinstance(
            exc,
            ccxt.InvalidOrder
            | ccxt.InsufficientFunds
            | ccxt.BadSymbol
            | ccxt.AuthenticationError
            | ccxt.PermissionDenied,
        )
    except ImportError:  # pragma: no cover
        return False
