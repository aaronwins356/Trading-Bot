"""Local OHLCV storage: one Parquet file per (exchange, symbol, timeframe).

Layout::

    <data_dir>/<exchange>/<BASE-QUOTE>/<timeframe>.parquet

Columns: timestamp (ms, int64), open, high, low, close, volume.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import candles as C
from ..core import timeframes as tfs


def default_data_dir() -> Path:
    env = os.environ.get("TRADEBOT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    # repo checkout: <repo>/data ; installed package: ~/.tradebot/data
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "src" / "tradebot").exists():
            return parent / "data"
    return Path.home() / ".tradebot" / "data"


def _sym_dir(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "_")


def _dir_sym(name: str) -> str:
    return name.replace("_", ":").replace("-", "/", 1)


class DataStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else default_data_dir()

    def path(self, exchange: str, symbol: str, timeframe: str) -> Path:
        return self.root / exchange / _sym_dir(symbol) / f"{timeframe}.parquet"

    def exists(self, exchange: str, symbol: str, timeframe: str) -> bool:
        return self.path(exchange, symbol, timeframe).exists()

    def save(
        self, exchange: str, symbol: str, timeframe: str, candles: np.ndarray, merge: bool = True
    ) -> Path:
        p = self.path(exchange, symbol, timeframe)
        p.parent.mkdir(parents=True, exist_ok=True)
        if merge and p.exists():
            old = self.load(exchange, symbol, timeframe)
            candles = np.vstack([old, candles])
        df = pd.DataFrame(candles, columns=C.COLUMNS)
        df["timestamp"] = df["timestamp"].astype("int64")
        df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        df.to_parquet(p, index=False)
        return p

    def load(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: str | int | None = None,
        end: str | int | None = None,
    ) -> np.ndarray:
        p = self.path(exchange, symbol, timeframe)
        if not p.exists():
            derived = self._derive(exchange, symbol, timeframe)
            if derived is None:
                raise FileNotFoundError(
                    f"No data for {exchange} {symbol} {timeframe} at {p}. "
                    f"Run: tradebot data download --exchange {exchange} --symbol {symbol} --timeframe {timeframe}"
                )
            arr = derived
        else:
            df = pd.read_parquet(p)
            arr = df[C.COLUMNS].to_numpy(dtype="float64")
        return C.slice_time(arr, _ms(start), _ms(end))

    def _derive(self, exchange: str, symbol: str, timeframe: str) -> np.ndarray | None:
        """Build a higher timeframe from the finest stored one (e.g. 4h from 1m)."""
        target = tfs.to_ms(timeframe)
        for base in sorted(self.timeframes(exchange, symbol), key=tfs.to_ms):
            if tfs.to_ms(base) < target and target % tfs.to_ms(base) == 0:
                df = pd.read_parquet(self.path(exchange, symbol, base))
                arr = C.resample(df[C.COLUMNS].to_numpy(dtype="float64"), timeframe)
                self.save(exchange, symbol, timeframe, arr, merge=False)
                return arr
        return None

    def timeframes(self, exchange: str, symbol: str) -> list[str]:
        d = self.root / exchange / _sym_dir(symbol)
        return [p.stem for p in d.glob("*.parquet")] if d.exists() else []

    def list(self) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for ex_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for sym_dir in sorted(p for p in ex_dir.iterdir() if p.is_dir()):
                for f in sorted(sym_dir.glob("*.parquet")):
                    try:
                        ts = pd.read_parquet(f, columns=["timestamp"])["timestamp"]
                    except Exception:  # corrupt/partial file
                        continue
                    if not len(ts):
                        continue
                    out.append(
                        {
                            "exchange": ex_dir.name,
                            "symbol": _dir_sym(sym_dir.name),
                            "timeframe": f.stem,
                            "candles": len(ts),
                            "start": pd.Timestamp(int(ts.iloc[0]), unit="ms", tz="UTC").isoformat(),
                            "end": pd.Timestamp(int(ts.iloc[-1]), unit="ms", tz="UTC").isoformat(),
                        }
                    )
        return out


def _ms(v: str | int | None) -> int | None:
    if v is None:
        return None
    if isinstance(v, int | np.integer):
        return int(v)
    ts = pd.Timestamp(v)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.value // 1_000_000)
