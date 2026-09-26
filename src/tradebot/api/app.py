"""REST + WebSocket API and dashboard server.

Security defaults: binds to 127.0.0.1; if ``TRADEBOT_API_TOKEN`` is set every
``/api`` route (and the WebSocket, via ``?token=``) requires
``Authorization: Bearer <token>``. Exchange keys never pass through the API -
they live in environment variables on the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .. import __version__
from .. import strategies as registry
from ..config import AppSettings, BotConfig, JEVSettings, load_settings
from ..core.jsonutil import sanitize
from ..live.manager import BotManager
from .jobs import BacktestRequest, JobManager

log = logging.getLogger("tradebot.api")


def _static_dir() -> Path | None:
    """Built dashboard: $TRADEBOT_STATIC_DIR, the copy bundled in the package, or dashboard/dist."""
    here = Path(__file__).resolve()
    env = os.environ.get("TRADEBOT_STATIC_DIR")
    cands = [Path(env)] if env else []
    cands += [here.parent / "static", here.parents[3] / "dashboard" / "dist"]
    for cand in cands:
        if (cand / "index.html").exists():
            return cand
    return None


class CommandBody(BaseModel):
    command: str
    arg: str | None = None


class StartBody(BaseModel):
    fresh: bool = False


class KillBody(BaseModel):
    reason: str = "global kill switch (dashboard)"
    flatten: bool = False


class DownloadBody(BaseModel):
    source: str = "free"  # free | ccxt
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    since: str | None = "2020-01-01"


class AskBody(BaseModel):
    exchange: str = "bitstamp"
    symbol: str = "BTC/USD"
    timeframe: str = "1d"
    jev: JEVSettings | None = None


def create_app(
    settings: AppSettings | None = None,
    manager: BotManager | None = None,
    results_dir: str | Path | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    token = settings.api.token()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mgr: BotManager = app.state.manager
        mgr.attach_loop(asyncio.get_running_loop())
        mgr.autostart()
        yield
        mgr.shutdown()
        app.state.jobs.shutdown()

    app = FastAPI(title="Trading-Bot API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.api.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )
    app.state.settings = settings
    app.state.manager = manager or BotManager(settings)
    app.state.jobs = JobManager(app.state.manager.db, app.state.manager.store)
    app.state.results_dir = Path(results_dir or settings.results_dir)

    def auth(request: Request) -> None:
        if not token:
            return
        header = request.headers.get("authorization", "")
        supplied = (
            header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
        )
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "invalid or missing API token")

    mgr: BotManager = app.state.manager
    jobs: JobManager = app.state.jobs
    guard = [Depends(auth)]

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "auth_required": bool(token)}

    @app.get("/api/auth/check", dependencies=guard)
    def auth_check() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/overview", dependencies=guard)
    def overview() -> dict[str, Any]:
        bots = [mgr.status(c.id) for c in mgr.configs()]
        running = [b for b in bots if b.get("status") == "running"]
        equity = sum(b.get("equity") or 0.0 for b in bots)
        capital = sum(b.get("capital") or 0.0 for b in bots)
        positions = [
            {**p, "bot_id": b["id"], "bot": b.get("name")} for b in bots for p in (b.get("positions") or [])
        ]
        return sanitize(
            {
                "bots_total": len(bots),
                "bots_running": len(running),
                "equity": equity,
                "capital": capital,
                "pnl": equity - capital,
                "pnl_pct": (equity / capital - 1) * 100 if capital else 0.0,
                "positions": positions,
                "risk_states": {b["id"]: (b.get("risk") or {}).get("state", "running") for b in bots},
                "recent_trades": mgr.db.trades(limit=15),
                "recent_decisions": mgr.db.decisions(limit=8),
                "recent_events": mgr.db.events(limit=15),
                "notifications": mgr.notifier.channels(),
                "bots": bots,
            }
        )

    # ------------------------------------------------------------------ strategies & research
    @app.get("/api/strategies", dependencies=guard)
    def strategies() -> list[dict[str, Any]]:
        verdicts = {}
        summary = app.state.results_dir / "summary.json"
        if summary.exists():
            for row in json.loads(summary.read_text()).get("studies", []):
                verdicts.setdefault(row["strategy"], []).append(
                    {
                        "key": row["key"],
                        "verdict": row["verdict"],
                        "sharpe": row["sharpe"],
                        "wf_oos_sharpe": row["wf_oos_sharpe"],
                    }
                )
        return [
            {**info.to_dict(), "research": verdicts.get(name, [])} for name, info in registry.REGISTRY.items()
        ]

    @app.get("/api/research", dependencies=guard)
    def research() -> Any:
        p = app.state.results_dir / "summary.json"
        if not p.exists():
            raise HTTPException(404, "no research results yet - run `tradebot research run`")
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/api/research/{key}", dependencies=guard)
    def research_study(key: str) -> Any:
        if not key.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "bad key")
        p = app.state.results_dir / f"{key}.json"
        if not p.exists():
            raise HTTPException(404, "unknown study")
        d = json.loads(p.read_text())
        d.get("full", {}).pop("daily_equity", None)
        return JSONResponse(d)

    # ------------------------------------------------------------------ bots
    @app.get("/api/bots", dependencies=guard)
    def list_bots() -> list[dict[str, Any]]:
        return [mgr.status(c.id) for c in mgr.configs()]

    @app.post("/api/bots", dependencies=guard)
    def create_bot(cfg: BotConfig) -> dict[str, Any]:
        if mgr.get_config(cfg.id):
            raise HTTPException(409, f"bot {cfg.id} already exists")
        try:
            registry.get(cfg.strategy)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        if cfg.mode == "live" and not cfg.exchange.credentials():
            raise HTTPException(
                400,
                "live mode needs exchange API keys in the environment variables named by exchange.api_key_env / secret_env",
            )
        mgr.save_config(cfg)
        return mgr.status(cfg.id)

    def _cfg_or_404(bot_id: str) -> BotConfig:
        cfg = mgr.get_config(bot_id)
        if cfg is None:
            raise HTTPException(404, "bot not found")
        return cfg

    @app.get("/api/bots/{bot_id}", dependencies=guard)
    def get_bot(bot_id: str) -> dict[str, Any]:
        cfg = _cfg_or_404(bot_id)
        return {**mgr.status(bot_id), "config": cfg.model_dump()}

    @app.put("/api/bots/{bot_id}", dependencies=guard)
    def update_bot(bot_id: str, cfg: BotConfig) -> dict[str, Any]:
        _cfg_or_404(bot_id)
        if cfg.id != bot_id:
            raise HTTPException(400, "id mismatch")
        try:
            mgr.save_config(cfg)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return mgr.status(bot_id)

    @app.delete("/api/bots/{bot_id}", dependencies=guard)
    def delete_bot(bot_id: str) -> dict[str, Any]:
        _cfg_or_404(bot_id)
        mgr.delete(bot_id)
        return {"ok": True}

    @app.post("/api/bots/{bot_id}/start", dependencies=guard)
    def start_bot(bot_id: str, body: StartBody | None = None) -> dict[str, Any]:
        _cfg_or_404(bot_id)
        mgr.start(bot_id, fresh=bool(body and body.fresh))
        return mgr.status(bot_id)

    @app.post("/api/bots/{bot_id}/stop", dependencies=guard)
    def stop_bot(bot_id: str) -> dict[str, Any]:
        _cfg_or_404(bot_id)
        mgr.stop(bot_id)
        return mgr.status(bot_id)

    @app.post("/api/bots/{bot_id}/command", dependencies=guard)
    def bot_command(bot_id: str, body: CommandBody) -> dict[str, Any]:
        _cfg_or_404(bot_id)
        try:
            mgr.command(bot_id, body.command, body.arg)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/bots/{bot_id}/equity", dependencies=guard)
    def bot_equity(bot_id: str, max_points: int = 1500) -> list[dict[str, Any]]:
        return mgr.db.equity(bot_id, max_points=max_points)

    @app.get("/api/bots/{bot_id}/trades", dependencies=guard)
    def bot_trades(bot_id: str, limit: int = 500) -> list[dict[str, Any]]:
        return mgr.db.trades(bot_id, limit)

    @app.get("/api/bots/{bot_id}/orders", dependencies=guard)
    def bot_orders(bot_id: str, limit: int = 300, active_only: bool = False) -> list[dict[str, Any]]:
        return mgr.db.orders(bot_id, limit, active_only)

    @app.get("/api/bots/{bot_id}/events", dependencies=guard)
    def bot_events(bot_id: str, limit: int = 300) -> list[dict[str, Any]]:
        return mgr.db.events(bot_id, limit)

    @app.get("/api/bots/{bot_id}/decisions", dependencies=guard)
    def bot_decisions(bot_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return mgr.db.decisions(bot_id, limit)

    @app.get("/api/bots/{bot_id}/candles", dependencies=guard)
    def bot_candles(bot_id: str, limit: int = Query(500, le=5000)) -> dict[str, Any]:
        cfg = _cfg_or_404(bot_id)
        r = mgr.runners.get(bot_id)
        sym = cfg.symbols[0]
        if r is not None and sym in r.buffers and len(r.buffers[sym]):
            arr = r.buffers[sym][-limit:]
        else:
            try:
                arr = mgr.store.load(
                    cfg.replay.exchange if cfg.mode == "replay" else cfg.exchange.id, sym, cfg.timeframe
                )[-limit:]
            except FileNotFoundError:
                arr = []
        trades = mgr.db.trades(bot_id, 300)
        return sanitize(
            {
                "symbol": sym,
                "timeframe": cfg.timeframe,
                "candles": [[int(c[0]), *map(float, c[1:5])] for c in arr],
                "trades": trades,
            }
        )

    # ------------------------------------------------------------------ global views
    @app.post("/api/kill-switch", dependencies=guard)
    def kill_switch(body: KillBody) -> dict[str, Any]:
        n = mgr.kill_all(body.reason, flatten=body.flatten)
        return {"ok": True, "bots_halted": n}

    @app.get("/api/trades", dependencies=guard)
    def all_trades(limit: int = 500) -> list[dict[str, Any]]:
        return mgr.db.trades(None, limit)

    @app.get("/api/decisions", dependencies=guard)
    def all_decisions(limit: int = 200) -> list[dict[str, Any]]:
        return mgr.db.decisions(None, limit)

    @app.get("/api/events", dependencies=guard)
    def all_events(limit: int = 300) -> list[dict[str, Any]]:
        return mgr.db.events(None, limit)

    # ------------------------------------------------------------------ backtests
    @app.post("/api/backtests", dependencies=guard)
    def create_backtest(req: BacktestRequest) -> dict[str, Any]:
        try:
            return {"id": jobs.submit_backtest(req)}
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/backtests", dependencies=guard)
    def list_backtests() -> list[dict[str, Any]]:
        return sanitize(mgr.db.list_backtests())

    @app.get("/api/backtests/{bid}", dependencies=guard)
    def get_backtest(bid: str) -> Any:
        d = mgr.db.get_backtest(bid)
        if d is None:
            raise HTTPException(404, "unknown backtest")
        return JSONResponse(sanitize(d))

    # ------------------------------------------------------------------ data
    @app.get("/api/data", dependencies=guard)
    def datasets() -> list[dict[str, Any]]:
        return mgr.store.list()

    @app.post("/api/data/download", dependencies=guard)
    def download(body: DownloadBody) -> dict[str, Any]:
        from ..data import sources

        if body.source == "free":

            def fn() -> dict[str, Any]:
                return {
                    "bitstamp": sources.download_bitstamp_btc_minutes(mgr.store),
                    "coinmetrics": sources.download_coinmetrics_daily(mgr.store),
                }

            return {"job": jobs.submit("download", fn)}
        return {
            "job": jobs.submit(
                "download",
                sources.download_ccxt,
                exchange_id=body.exchange,
                symbol=body.symbol,
                timeframe=body.timeframe,
                since=body.since,
                store=mgr.store,
            )
        }

    @app.get("/api/jobs/{jid}", dependencies=guard)
    def job(jid: str) -> dict[str, Any]:
        j = jobs.get(jid)
        if j is None:
            raise HTTPException(404, "unknown job")
        return j

    # ------------------------------------------------------------------ JEV
    @app.get("/api/jev/status", dependencies=guard)
    def jev_status(base_url: str | None = None, model: str | None = None) -> dict[str, Any]:
        from ..jev.llm import LLMClient

        s = JEVSettings()
        client = LLMClient(base_url or s.base_url, model or s.model, timeout_s=5)
        bots = [c for c in mgr.configs() if c.strategy == "JEVStrategy"]
        return {
            "defaults": s.model_dump(),
            "endpoint": client.health(),
            "bots": [{"id": b.id, "name": b.name, "jev": b.jev.model_dump()} for b in bots],
        }

    @app.post("/api/jev/ask", dependencies=guard)
    def jev_ask(body: AskBody) -> dict[str, Any]:
        from ..jev.ask import ask_jev

        try:
            return sanitize(
                ask_jev(
                    mgr.store,
                    body.exchange,
                    body.symbol,
                    body.timeframe,
                    body.jev or JEVSettings(enabled=True),
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ------------------------------------------------------------------ websocket
    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        if token and not secrets.compare_digest(websocket.query_params.get("token", ""), token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        q = mgr.subscribe()
        try:
            while True:
                event = await q.get()
                await websocket.send_text(json.dumps(event, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            mgr.unsubscribe(q)

    # ------------------------------------------------------------------ dashboard (SPA)
    static = _static_dir()
    if static is not None:

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (static / path).resolve()
            if path and f.is_file() and static in f.parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        def no_dashboard() -> dict[str, Any]:
            return {
                "message": "API running. Dashboard not built: cd dashboard && npm ci && npm run build",
                "docs": "/docs",
            }

    return app
