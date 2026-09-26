"""Persistence (SQLAlchemy 2.0). SQLite by default; any SQLAlchemy URL works (e.g. Postgres).

Everything a restarted bot needs is persisted: bot configs, orders, fills, closed
trades, equity snapshots, JEV decisions, risk/audit events and a JSON state blob
(simulated account, kill switch, strategy vars).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ..core.jsonutil import sanitize
from ..core.types import ClosedTrade, Order


def now_ms() -> int:
    return int(time.time() * 1000)


class Base(DeclarativeBase):
    pass


class BotRow(Base):
    __tablename__ = "bots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="stopped")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class BotStateRow(Base):
    __tablename__ = "bot_state"
    bot_id: Mapped[str] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class OrderRow(Base):
    __tablename__ = "orders"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(12))
    role: Mapped[str] = mapped_column(String(16))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float] = mapped_column(Float, default=0.0)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    reduce_only: Mapped[bool] = mapped_column(default=False)
    tag: Mapped[str] = mapped_column(Text, default="")
    reject_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_orders_bot_order", "bot_id", "id", unique=True),)


class TradeRow(Base):
    __tablename__ = "trades"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[int] = mapped_column(BigInteger)
    closed_at: Mapped[int] = mapped_column(BigInteger, index=True)
    pnl: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float)
    funding: Mapped[float] = mapped_column(Float, default=0.0)
    return_pct: Mapped[float] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[str] = mapped_column(String(100), default="")


class EquityRow(Base):
    __tablename__ = "equity"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[int] = mapped_column(BigInteger)
    equity: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (Index("ix_equity_bot_ts", "bot_id", "ts"),)


class DecisionRow(Base):
    __tablename__ = "decisions"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    authority: Mapped[str] = mapped_column(String(16))
    consensus: Mapped[float] = mapped_column(Float)
    llm_exposure: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_exposure: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSON)
    raw_response: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class EventRow(Base):
    __tablename__ = "events"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class BacktestRow(Base):
    __tablename__ = "backtests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    status: Mapped[str] = mapped_column(String(16))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    request: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn: Any, _: Any) -> None:  # WAL: concurrent readers (API) + writer (bots)
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)
        self._lock = threading.RLock()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock, self._session() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    # ------------------------------------------------------------------ bots
    def upsert_bot(self, bot_id: str, name: str, config: dict[str, Any], status: str | None = None) -> None:
        with self.session() as s:
            row = s.get(BotRow, bot_id)
            if row is None:
                s.add(BotRow(id=bot_id, name=name, config=config, status=status or "stopped"))
            else:
                row.name, row.config, row.updated_at = name, config, now_ms()
                if status:
                    row.status = status

    def set_bot_status(self, bot_id: str, status: str, reason: str = "") -> None:
        with self.session() as s:
            row = s.get(BotRow, bot_id)
            if row is not None:
                row.status, row.status_reason, row.updated_at = status, reason, now_ms()

    def get_bot(self, bot_id: str) -> BotRow | None:
        with self.session() as s:
            return s.get(BotRow, bot_id)

    def list_bots(self) -> list[BotRow]:
        with self.session() as s:
            return list(s.scalars(select(BotRow).order_by(BotRow.created_at)))

    def delete_bot(self, bot_id: str) -> None:
        with self.session() as s:
            for model in (OrderRow, TradeRow, EquityRow, DecisionRow, EventRow):
                s.execute(delete(model).where(model.bot_id == bot_id))
            s.execute(delete(BotStateRow).where(BotStateRow.bot_id == bot_id))
            s.execute(delete(BotRow).where(BotRow.id == bot_id))

    # ------------------------------------------------------------------ state
    def save_state(self, bot_id: str, state: dict[str, Any]) -> None:
        with self.session() as s:
            row = s.get(BotStateRow, bot_id)
            if row is None:
                s.add(BotStateRow(bot_id=bot_id, state=state))
            else:
                row.state, row.updated_at = state, now_ms()

    def load_state(self, bot_id: str) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.get(BotStateRow, bot_id)
            return dict(row.state) if row else None

    # ------------------------------------------------------------------ orders & trades
    def upsert_order(self, bot_id: str, o: Order) -> None:
        with self.session() as s:
            row = s.scalar(select(OrderRow).where(OrderRow.bot_id == bot_id, OrderRow.id == o.id))
            vals = {
                "client_id": o.client_id,
                "exchange_id": o.exchange_id,
                "symbol": o.symbol,
                "side": str(o.side),
                "type": str(o.type),
                "role": str(o.role),
                "qty": o.qty,
                "price": o.price,
                "status": str(o.status),
                "filled_qty": o.filled_qty,
                "avg_fill_price": o.avg_fill_price,
                "fee": o.fee,
                "reduce_only": o.reduce_only,
                "tag": o.tag,
                "reject_reason": o.reject_reason,
                "created_at": o.created_at,
                "updated_at": o.updated_at or o.created_at,
            }
            if row is None:
                s.add(OrderRow(bot_id=bot_id, id=o.id, **vals))
            else:
                for k, v in vals.items():
                    setattr(row, k, v)

    def add_trade(self, bot_id: str, t: ClosedTrade) -> None:
        with self.session() as s:
            s.add(TradeRow(bot_id=bot_id, **{k: v for k, v in t.to_dict().items() if k != "bars_held"}))

    def add_equity(
        self, bot_id: str, ts: int, equity: float, balance: float, exposure: float, price: float | None = None
    ) -> None:
        with self.session() as s:
            s.add(
                EquityRow(
                    bot_id=bot_id, ts=ts, equity=equity, balance=balance, exposure=exposure, price=price
                )
            )

    def add_event(
        self,
        bot_id: str,
        level: str,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
        ts: int | None = None,
    ) -> None:
        with self.session() as s:
            s.add(
                EventRow(
                    bot_id=bot_id,
                    ts=ts or now_ms(),
                    level=level,
                    kind=kind,
                    message=message[:4000],
                    data=_jsonable(data or {}),
                )
            )

    def add_decision(self, bot_id: str, **kw: Any) -> None:
        kw["context"] = _jsonable(kw.get("context", {}))
        with self.session() as s:
            s.add(DecisionRow(bot_id=bot_id, **kw))

    # ------------------------------------------------------------------ queries
    def orders(self, bot_id: str, limit: int = 200, active_only: bool = False) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(OrderRow).where(OrderRow.bot_id == bot_id)
            if active_only:
                q = q.where(OrderRow.status.in_(["new", "open", "partially_filled"]))
            rows = s.scalars(q.order_by(OrderRow.created_at.desc(), OrderRow.pk.desc()).limit(limit))
            return [_row(r, exclude={"pk"}) for r in rows]

    def trades(self, bot_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(TradeRow)
            if bot_id:
                q = q.where(TradeRow.bot_id == bot_id)
            rows = s.scalars(q.order_by(TradeRow.closed_at.desc()).limit(limit))
            return [_row(r, exclude={"pk"}) for r in rows]

    def equity(self, bot_id: str, since: int | None = None, max_points: int = 2000) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(EquityRow).where(EquityRow.bot_id == bot_id)
            if since:
                q = q.where(EquityRow.ts >= since)
            rows = list(s.scalars(q.order_by(EquityRow.ts)))
        step = max(1, len(rows) // max_points)
        picked = rows[::step]
        if rows and picked[-1] is not rows[-1]:
            picked.append(rows[-1])
        return [
            {"ts": r.ts, "equity": r.equity, "balance": r.balance, "exposure": r.exposure, "price": r.price}
            for r in picked
        ]

    def decisions(self, bot_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(DecisionRow)
            if bot_id:
                q = q.where(DecisionRow.bot_id == bot_id)
            rows = s.scalars(q.order_by(DecisionRow.ts.desc(), DecisionRow.pk.desc()).limit(limit))
            return [_row(r, exclude={"pk"}) for r in rows]

    def events(
        self, bot_id: str | None = None, limit: int = 200, kinds: list[str] | None = None
    ) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(EventRow)
            if bot_id:
                q = q.where(EventRow.bot_id == bot_id)
            if kinds:
                q = q.where(EventRow.kind.in_(kinds))
            rows = s.scalars(q.order_by(EventRow.ts.desc(), EventRow.pk.desc()).limit(limit))
            return [_row(r, exclude={"pk"}) for r in rows]

    # ------------------------------------------------------------------ backtests
    def save_backtest(self, bid: str, **kw: Any) -> None:
        with self.session() as s:
            row = s.get(BacktestRow, bid)
            if row is None:
                s.add(BacktestRow(id=bid, **kw))
            else:
                for k, v in kw.items():
                    setattr(row, k, v)

    def get_backtest(self, bid: str) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.get(BacktestRow, bid)
            if row is None:
                return None
            d = _row(row, exclude={"result_json"})
            d["result"] = json.loads(row.result_json) if row.result_json else None
            return d

    def list_backtests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(select(BacktestRow).order_by(BacktestRow.created_at.desc()).limit(limit))
            return [_row(r, exclude={"result_json"}) for r in rows]


def _row(r: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    return {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name not in exclude}


def _jsonable(d: Any) -> Any:
    return sanitize(d)
