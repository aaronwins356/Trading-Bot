"""Command-line interface: ``tradebot --help``."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    help="Trading-Bot: research, backtest, paper/live trade, dashboard, and JEV (open-source decision AI).",
    no_args_is_help=True,
)
data_app = typer.Typer(help="Download / import / list market data.", no_args_is_help=True)
research_app = typer.Typer(help="Run the strategy research pipeline.", no_args_is_help=True)
bot_app = typer.Typer(help="Run bots headless (without the API).", no_args_is_help=True)
jev_app = typer.Typer(help="JEV decision engine tools.", no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(research_app, name="research")
app.add_typer(bot_app, name="bot")
app.add_typer(jev_app, name="jev")
con = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_hp(items: list[str] | None) -> dict[str, Any]:
    hp: dict[str, Any] = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            hp[k] = json.loads(v)
        except json.JSONDecodeError:
            hp[k] = v
    return hp


def _print_metrics(m: dict[str, Any], title: str = "Results") -> None:
    t = Table(title=title, show_header=False)
    keys = [
        ("total_return_pct", "Total return %"),
        ("cagr_pct", "CAGR %"),
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("calmar", "Calmar"),
        ("max_drawdown_pct", "Max drawdown %"),
        ("volatility_pct", "Volatility %"),
        ("total_trades", "Trades"),
        ("win_rate", "Win rate %"),
        ("profit_factor", "Profit factor"),
        ("exposure_pct", "Time in market %"),
        ("fees_paid", "Fees paid"),
        ("psr", "Probabilistic Sharpe"),
    ]
    for k, label in keys:
        if k in m:
            t.add_row(label, str(m[k]))
    b = m.get("benchmark")
    if b:
        t.add_row("Buy & hold CAGR %", str(b.get("cagr_pct")))
        t.add_row("Buy & hold Sharpe", str(b.get("sharpe")))
        t.add_row("Buy & hold max DD %", str(b.get("max_drawdown_pct")))
    con.print(t)


def _spec(
    strategy: str,
    exchange: str,
    symbols: list[str],
    timeframe: str,
    fee: float,
    slippage: float,
    futures: bool,
    leverage: float,
    funding: float,
    capital: float,
):
    from . import strategies as registry
    from .backtest.engine import BacktestConfig
    from .backtest.runner import BacktestSpec
    from .data.store import DataStore

    cls = registry.get(strategy)
    store = DataStore()
    cfg = BacktestConfig(
        starting_balance=capital,
        fee_maker=fee,
        fee_taker=fee,
        slippage_bps=slippage,
        exchange_type="futures" if futures else "spot",
        leverage=leverage if futures else 1.0,
        funding_rate_8h=funding if futures else 0.0,
    )
    if registry.is_portfolio(cls):
        from .data.sources import load_market_caps
        from .research.pipeline import point_in_time_universe

        data = {
            i["symbol"]: store.load("coinmetrics", i["symbol"], "1d")
            for i in store.list()
            if i["exchange"] == "coinmetrics" and i["timeframe"] == "1d"
        }
        uni = point_in_time_universe(data, load_market_caps(store), 20)
        return BacktestSpec(cls, data, "1d", cfg, vars={"universe": uni}, benchmark_symbol="BTC/USD")
    data = {s: store.load(exchange, s, timeframe) for s in symbols}
    return BacktestSpec(cls, data, timeframe, cfg)


# ============================================================================ server


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind address (default from config: 127.0.0.1)"),
    port: int = typer.Option(None, help="Port (default 8080)"),
    config: Path = typer.Option(None, "--config", "-c", help="YAML config (default config/config.yaml)"),
    verbose: bool = False,
) -> None:
    """Start the API + dashboard (http://127.0.0.1:8080)."""
    import uvicorn

    from .api.app import create_app
    from .config import load_settings

    _setup_logging(verbose)
    settings = load_settings(config)
    h, p = host or settings.api.host, port or settings.api.port
    if h not in ("127.0.0.1", "localhost") and not settings.api.token():
        con.print(
            f"[bold red]Refusing to bind {h} without an API token.[/] Set {settings.api.token_env} or bind 127.0.0.1."
        )
        raise typer.Exit(1)
    con.print(f"[bold]Dashboard:[/] http://{h}:{p}   [dim](API docs at /docs)[/]")
    uvicorn.run(create_app(settings), host=h, port=p, log_level="warning")


# ============================================================================ data


@data_app.command("free")
def data_free() -> None:
    """Download free research data: Bitstamp BTC/USD 1m (2012-now) + Coin Metrics daily (~45 coins)."""
    from .data.sources import download_bitstamp_btc_minutes, download_coinmetrics_daily

    _setup_logging(False)
    con.print(download_coinmetrics_daily())
    con.print(download_bitstamp_btc_minutes(timeframes=("15m", "1h", "4h", "1d")))


@data_app.command("download")
def data_download(
    exchange: str = typer.Option("binance"),
    symbol: str = typer.Option("BTC/USDT"),
    timeframe: str = typer.Option("1h"),
    since: str = typer.Option("2020-01-01"),
    until: str = typer.Option(None),
) -> None:
    """Download candles from any CCXT exchange (resumes from the last stored candle)."""
    from .data.sources import download_ccxt

    n = download_ccxt(
        exchange, symbol, timeframe, since, until, progress=lambda k: con.print(f"  {k} candles...", end="\r")
    )
    con.print(f"\nsaved {n} candles")


@data_app.command("import-csv")
def data_import(
    path: Path,
    exchange: str = typer.Option(...),
    symbol: str = typer.Option(...),
    timeframe: str = typer.Option(...),
) -> None:
    """Import OHLCV candles from a CSV file."""
    from .data.sources import import_csv

    con.print(f"imported {import_csv(path, exchange, symbol, timeframe)} rows")


@data_app.command("list")
def data_list() -> None:
    from .data.store import DataStore

    t = Table("exchange", "symbol", "tf", "candles", "start", "end")
    for d in DataStore().list():
        t.add_row(
            d["exchange"], d["symbol"], d["timeframe"], str(d["candles"]), d["start"][:10], d["end"][:10]
        )
    con.print(t)


# ============================================================================ research tools


@app.command()
def strategies() -> None:
    """List built-in strategies and their research status."""
    from . import strategies as registry

    t = Table("name", "kind", "timeframe", "status", "summary")
    for name, info in registry.REGISTRY.items():
        t.add_row(name, info.kind, info.timeframe, info.status, info.summary)
    con.print(t)


@app.command()
def backtest(
    strategy: str,
    exchange: str = typer.Option("bitstamp"),
    symbol: list[str] = typer.Option(["BTC/USD"]),
    timeframe: str = typer.Option("1d"),
    start: str = typer.Option("2018-01-01"),
    end: str = typer.Option(None),
    hp: list[str] = typer.Option(None, help="key=value (repeatable)"),
    fee: float = typer.Option(0.001, help="fee per side (0.001 = 0.10%)"),
    slippage: float = typer.Option(5.0, help="slippage per side in bps"),
    futures: bool = typer.Option(False, help="perpetual futures accounting (shorts, leverage, funding)"),
    leverage: float = typer.Option(1.0),
    funding: float = typer.Option(0.0001, help="funding per 8h when --futures"),
    capital: float = typer.Option(10_000.0),
    json_out: Path = typer.Option(None, "--json", help="write the full result as JSON"),
) -> None:
    """Backtest a strategy on stored data."""
    spec = _spec(strategy, exchange, symbol, timeframe, fee, slippage, futures, leverage, funding, capital)
    t0 = time.time()
    res = spec.run(_parse_hp(hp) or None, start, end)
    _print_metrics(res.metrics, f"{strategy} on {', '.join(symbol)} {timeframe} ({time.time() - t0:.1f}s)")
    if json_out:
        from .core.jsonutil import dumps

        json_out.write_text(dumps(res.to_dict(include_orders=True)))
        con.print(f"wrote {json_out}")


@app.command()
def optimize(
    strategy: str,
    exchange: str = typer.Option("bitstamp"),
    symbol: list[str] = typer.Option(["BTC/USD"]),
    timeframe: str = typer.Option("1d"),
    start: str = typer.Option("2018-01-01"),
    end: str = typer.Option(None),
    trials: int = typer.Option(100),
    objective: str = typer.Option("sharpe"),
    fee: float = typer.Option(0.001),
    slippage: float = typer.Option(5.0),
) -> None:
    """Optuna (TPE) hyperparameter search. Beware: validate the result out of sample (see `walkforward`)."""
    from .backtest.metrics import deflated_sharpe_ratio
    from .backtest.optimize import optuna_search, trial_sharpes_per_period

    spec = _spec(strategy, exchange, symbol, timeframe, fee, slippage, False, 1.0, 0.0, 10_000)
    best, results = optuna_search(spec, trials, start, end, objective)
    con.print(f"[bold]best params:[/] {best}")
    top = max(results, key=lambda r: r.score(objective, 5))
    _print_metrics(top.metrics, "Best trial (in-sample!)")
    dsr = deflated_sharpe_ratio(top.daily_returns.values, len(results), trial_sharpes_per_period(results))
    con.print(f"Deflated Sharpe Ratio after {len(results)} trials: [bold]{dsr:.3f}[/] (want > 0.9)")


@app.command()
def walkforward(
    strategy: str,
    grid: str = typer.Option(..., help='JSON grid, e.g. \'{"entry":[20,55],"exit":[10,20]}\''),
    exchange: str = typer.Option("bitstamp"),
    symbol: list[str] = typer.Option(["BTC/USD"]),
    timeframe: str = typer.Option("1d"),
    start: str = typer.Option("2016-01-01"),
    end: str = typer.Option(time.strftime("%Y-%m-%d")),
    train_years: float = typer.Option(3.0),
    test_years: float = typer.Option(1.0),
    fee: float = typer.Option(0.001),
    slippage: float = typer.Option(5.0),
) -> None:
    """Walk-forward analysis: rolling optimisation + stitched out-of-sample equity."""
    from .backtest.walkforward import walk_forward

    spec = _spec(strategy, exchange, symbol, timeframe, fee, slippage, False, 1.0, 0.0, 10_000)
    wf = walk_forward(spec, json.loads(grid), start, end, train_years, test_years)
    t = Table("test window", "params", "OOS Sharpe", "OOS return %")
    for f in wf.folds:
        t.add_row(
            f"{f.test_start.date()} → {f.test_end.date()}",
            json.dumps(f.hp),
            str(f.test_metrics.get("sharpe")),
            str(f.test_metrics.get("total_return_pct")),
        )
    con.print(t)
    _print_metrics(wf.oos_metrics, "Stitched out-of-sample")


@app.command()
def lookahead(
    strategy: str,
    exchange: str = typer.Option("bitstamp"),
    symbol: str = typer.Option("BTC/USD"),
    timeframe: str = typer.Option("1d"),
    start: str = typer.Option("2019-01-01"),
    bars: int = typer.Option(2000),
) -> None:
    """Detect look-ahead bias in a strategy's indicators and decisions."""
    from . import strategies as registry
    from .backtest.lookahead import check_lookahead
    from .data.store import DataStore

    candles = DataStore().load(exchange, symbol, timeframe, start=start)[:bars]
    rep = check_lookahead(registry.get(strategy), candles, timeframe)
    con.print_json(data=rep.to_dict())
    raise typer.Exit(1 if rep.has_bias else 0)


@research_app.command("run")
def research_run(
    only: list[str] = typer.Option(None),
    fast: bool = False,
    out: Path = typer.Option(Path("research/results")),
) -> None:
    """Run every research study (or --only KEY) and write results for the dashboard."""
    from .research.pipeline import run_all

    _setup_logging(False)
    summary = run_all(
        out,
        only=only,
        fast=fast,
        on_study=lambda r: con.print(f"[bold]{r['key']}[/] → {r['verdict']} ({r['runtime_sec']}s)"),
    )
    _leaderboard(summary)


@research_app.command("report")
def research_report(
    results: Path = typer.Option(Path("research/results")),
    out: Path = typer.Option(Path("docs/STRATEGIES.md")),
) -> None:
    """Render docs/STRATEGIES.md from the research results."""
    from .research.report import write

    con.print(f"wrote {write(results, out)}")


@research_app.command("show")
def research_show(path: Path = typer.Option(Path("research/results/summary.json"))) -> None:
    """Print the research leaderboard."""
    _leaderboard(json.loads(Path(path).read_text()))


def _leaderboard(s: dict) -> None:
    t = Table("study", "verdict", "CAGR %", "Sharpe", "MaxDD %", "OOS Sharpe", "OOS CAGR %", "DSR", "PBO")
    for r in s["studies"]:
        t.add_row(
            r["key"],
            r["verdict"],
            str(r["cagr_pct"]),
            str(r["sharpe"]),
            str(r["max_drawdown_pct"]),
            str(r["wf_oos_sharpe"]),
            str(r["wf_oos_cagr_pct"]),
            str(r["dsr"]),
            str(r["pbo"]),
        )
    con.print(t)


# ============================================================================ bots


@bot_app.command("run")
def bot_run(
    bot_id: str,
    config: Path = typer.Option(None, "--config", "-c"),
    fresh: bool = False,
    verbose: bool = False,
) -> None:
    """Run one bot from the YAML config in the foreground (Ctrl+C to stop)."""
    from .config import load_settings
    from .live.manager import BotManager

    _setup_logging(verbose)
    mgr = BotManager(load_settings(config))
    r = mgr.start(bot_id, fresh=fresh)
    con.print(f"running {bot_id} ({r.cfg.mode}); Ctrl+C to stop")
    try:
        while r.status in ("starting", "running"):
            time.sleep(1)
    except KeyboardInterrupt:
        con.print("stopping...")
    finally:
        mgr.shutdown()
    con.print_json(data=r.snapshot())


@bot_app.command("list")
def bot_list(config: Path = typer.Option(None, "--config", "-c")) -> None:
    from .config import load_settings
    from .live.manager import BotManager

    mgr = BotManager(load_settings(config))
    t = Table("id", "name", "strategy", "mode", "status", "equity")
    for c in mgr.configs():
        s = mgr.status(c.id)
        t.add_row(c.id, c.name, c.strategy, c.mode, str(s.get("status")), f"{s.get('equity', 0):,.2f}")
    con.print(t)


@app.command()
def demo(port: int = 8080) -> None:
    """Create replay demo bots (no exchange needed) and open the dashboard."""
    import uvicorn

    from .api.app import create_app
    from .config import AppSettings, BotConfig, JEVSettings, ReplaySettings
    from .live.manager import BotManager

    _setup_logging(False)
    settings = AppSettings()
    mgr = BotManager(settings)
    if not mgr.store.exists("bitstamp", "BTC/USD", "1d") or not mgr.store.exists("bitstamp", "BTC/USD", "4h"):
        con.print("[bold]First run:[/] downloading free BTC history (Bitstamp 1m since 2012, ~140 MB)...")
        from .data.sources import download_bitstamp_btc_minutes, download_coinmetrics_daily

        download_bitstamp_btc_minutes(mgr.store, timeframes=("1h", "4h", "1d"))
        download_coinmetrics_daily(mgr.store)
    demos = [
        BotConfig(
            id="demo-tsmom",
            name="BTC trend · TSMOM (replay)",
            strategy="TrendVolTarget",
            symbols=["BTC/USD"],
            timeframe="1d",
            mode="replay",
            replay=ReplaySettings(start="2023-01-01", speed=0.05),
        ),
        BotConfig(
            id="demo-turtle",
            name="BTC Turtle 4h (replay)",
            strategy="DonchianBreakout",
            symbols=["BTC/USD"],
            timeframe="4h",
            mode="replay",
            replay=ReplaySettings(start="2024-01-01", speed=0.02),
        ),
        BotConfig(
            id="demo-jev",
            name="JEV decision AI (replay)",
            strategy="JEVStrategy",
            symbols=["BTC/USD"],
            timeframe="1d",
            mode="replay",
            replay=ReplaySettings(start="2023-06-01", speed=0.05),
            jev=JEVSettings(enabled=False),
        ),
    ]
    for cfg in demos:
        if mgr.get_config(cfg.id) is None:
            mgr.save_config(cfg)
    for cfg in demos:
        mgr.start(cfg.id, fresh=True)
    con.print(f"[bold]Demo running:[/] http://127.0.0.1:{port}")
    uvicorn.run(create_app(settings, manager=mgr), host="127.0.0.1", port=port, log_level="warning")


# ============================================================================ JEV


@jev_app.command("check")
def jev_check(base_url: str = "http://localhost:11434/v1", model: str = "gpt-oss:20b") -> None:
    """Check that an OpenAI-compatible model server is reachable and serving the model."""
    from .jev.llm import LLMClient

    con.print_json(data=LLMClient(base_url, model, timeout_s=10).health())


@jev_app.command("ask")
def jev_ask(
    exchange: str = "bitstamp",
    symbol: str = "BTC/USD",
    timeframe: str = "1d",
    base_url: str = "http://localhost:11434/v1",
    model: str = "gpt-oss:20b",
    authority: str = "veto",
    no_llm: bool = False,
) -> None:
    """One JEV decision on the latest stored candles (no orders)."""
    from .config import JEVSettings
    from .data.store import DataStore
    from .jev.ask import ask_jev

    res = ask_jev(
        DataStore(),
        exchange,
        symbol,
        timeframe,
        JEVSettings(enabled=not no_llm, base_url=base_url, model=model, authority=authority),
    )  # type: ignore[arg-type]
    con.print_json(data=json.loads(json.dumps(res, default=str)))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
