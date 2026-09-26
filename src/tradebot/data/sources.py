"""Market-data downloaders.

* :func:`download_ccxt` - any of CCXT's 100+ exchanges (paginated ``fetch_ohlcv``).
* :func:`download_bitstamp_btc_minutes` - free BTC/USD 1-minute history since 2012
  (github.com/ff137/bitstamp-btcusd-minute-data, updated daily). No API key.
* :func:`download_coinmetrics_daily` - free daily reference prices + market caps for
  ~45 coins including dead ones (github.com/coinmetrics/data) - used for
  survivorship-bias-aware portfolio research.
"""

from __future__ import annotations

import io
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..core import candles as C
from ..core import timeframes as tfs
from .store import DataStore

log = logging.getLogger("tradebot.data")

BITSTAMP_BULK = "https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz"
BITSTAMP_UPDATES = "https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data/updates/btcusd_bitstamp_1min_latest.csv"
COINMETRICS_CSV = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"

# assets with daily prices in the Coin Metrics community dataset (incl. delisted/collapsed ones)
COINMETRICS_ASSETS = [
    "btc", "eth", "ltc", "doge", "dash", "xmr", "xrp", "dgb", "xem", "xlm", "etc", "rep", "zec", "bnb",
    "omg", "neo", "bch", "zrx", "mana", "knc", "link", "bat", "btg", "ada", "mkr", "eos", "trx", "xtz",
    "bsv", "ht", "cro", "algo", "ftt", "snx", "comp", "yfi", "crv", "dot", "sushi", "uni", "aave", "icp", "ldo",
]  # fmt: skip
STABLECOINS = {"usdt", "usdc", "dai", "busd", "tusd", "usdp"}


def _get(url: str, timeout: float = 600.0) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def download_bitstamp_btc_minutes(
    store: DataStore | None = None, timeframes: Iterable[str] = ("1h", "4h", "1d")
) -> dict[str, int]:
    """Download BTC/USD 1m candles (2012 -> today) and derive higher timeframes."""
    store = store or DataStore()
    log.info("downloading Bitstamp BTC/USD 1m bulk history (~90 MB)...")
    bulk = pd.read_csv(io.BytesIO(_get(BITSTAMP_BULK)), compression="gzip")
    upd = pd.read_csv(io.BytesIO(_get(BITSTAMP_UPDATES)))
    df = pd.concat([bulk, upd]).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    df["timestamp"] = df["timestamp"].astype("int64") * 1000
    arr = df[C.COLUMNS].to_numpy(dtype="float64")
    store.save("bitstamp", "BTC/USD", "1m", arr, merge=False)
    out = {"1m": len(arr)}
    for tf in timeframes:
        r = C.resample(arr, tf)
        r = C.closed_only(r, tf, int(arr[-1, C.TS]) + 60_000)
        store.save("bitstamp", "BTC/USD", tf, r, merge=False)
        out[tf] = len(r)
    return out


def download_coinmetrics_daily(
    store: DataStore | None = None, assets: Iterable[str] = COINMETRICS_ASSETS
) -> dict[str, int]:
    """Download daily reference prices & market caps. Candles are close-only (open = prior close).

    Also writes ``coinmetrics/_mcap.parquet`` with point-in-time market caps for universe selection.
    """
    store = store or DataStore()
    caps: dict[str, pd.Series] = {}
    out: dict[str, int] = {}
    for a in assets:
        if a in STABLECOINS:
            continue
        try:
            raw = _get(COINMETRICS_CSV.format(asset=a), timeout=120)
        except httpx.HTTPError as exc:
            log.warning("coinmetrics %s failed: %s", a, exc)
            continue
        d = pd.read_csv(io.BytesIO(raw), low_memory=False)
        if "time" not in d:
            continue
        d["time"] = pd.to_datetime(d["time"], utc=True)
        d = d.set_index("time")
        px = None
        for col in ("ReferenceRateUSD", "PriceUSD"):
            if col in d:
                px = d[col] if px is None else px.combine_first(d[col])
        if px is None:
            continue
        px = px.dropna()
        px = px[px > 0]
        if len(px) < 60:
            continue
        o = px.shift(1).fillna(px.iloc[0])
        df = pd.DataFrame(
            {"open": o, "high": np.maximum(o, px), "low": np.minimum(o, px), "close": px, "volume": 0.0}
        )
        store.save("coinmetrics", f"{a.upper()}/USD", "1d", C.from_dataframe(df), merge=False)
        out[a] = len(df)
        for col in ("CapMrktCurUSD", "CapMrktEstUSD"):
            if col in d and d[col].notna().any():
                caps[f"{a.upper()}/USD"] = d[col]
                break
    if caps:
        mc = pd.DataFrame(caps).sort_index()
        p = store.root / "coinmetrics" / "_mcap.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        mc.to_parquet(p)
    return out


def load_market_caps(store: DataStore | None = None) -> pd.DataFrame:
    store = store or DataStore()
    p = store.root / "coinmetrics" / "_mcap.parquet"
    if not p.exists():
        raise FileNotFoundError("market caps missing - run `tradebot data free`")
    return pd.read_parquet(p)


def download_ccxt(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since: str | int | None = None,
    until: str | int | None = None,
    store: DataStore | None = None,
    limit: int = 1000,
    progress: Callable[[int], None] | None = None,
    exchange_config: dict | None = None,
) -> int:
    """Paginated OHLCV download via CCXT; resumes from the last stored candle."""
    import ccxt

    store = store or DataStore()
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True, **(exchange_config or {})})
    tf_ms = tfs.to_ms(timeframe)
    start = _to_ms(since) if since is not None else None
    if store.exists(exchange_id, symbol, timeframe) and since is None:
        have = store.load(exchange_id, symbol, timeframe)
        if len(have):
            start = int(have[-1, C.TS]) + tf_ms
    if start is None:
        start = int(pd.Timestamp("2017-01-01", tz="UTC").value // 1_000_000)
    end = _to_ms(until) if until is not None else int(time.time() * 1000)
    rows: list[list[float]] = []
    cursor = start
    while cursor < end:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
        if not batch:
            break
        batch = [b for b in batch if b[0] < end]
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
        if progress:
            progress(len(rows))
    if not rows:
        return 0
    arr = np.asarray(rows, dtype="float64")
    now_ms = int(time.time() * 1000)
    arr = C.closed_only(arr, timeframe, now_ms)
    store.save(exchange_id, symbol, timeframe, arr, merge=True)
    return len(arr)


def _to_ms(v: str | int) -> int:
    if isinstance(v, int):
        return v
    ts = pd.Timestamp(v)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.value // 1_000_000)


def import_csv(
    path: str | Path, exchange: str, symbol: str, timeframe: str, store: DataStore | None = None
) -> int:
    """Import a CSV with timestamp/open/high/low/close/volume columns (timestamp in s or ms, or ISO dates)."""
    store = store or DataStore()
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("timestamp") or cols.get("time") or cols.get("date") or df.columns[0]
    t = df[tcol]
    if np.issubdtype(t.dtype, np.number):
        ms = t.astype("int64") * (1000 if t.max() < 10_000_000_000 else 1)
    else:
        ms = pd.to_datetime(t, utc=True).astype("int64") // 1_000_000
    out = pd.DataFrame(
        {
            "timestamp": ms,
            "open": df[cols["open"]],
            "high": df[cols["high"]],
            "low": df[cols["low"]],
            "close": df[cols["close"]],
            "volume": df[cols["volume"]] if "volume" in cols else 0.0,
        }
    )
    arr = out.to_numpy(dtype="float64")
    store.save(exchange, symbol, timeframe, arr, merge=True)
    return len(arr)
