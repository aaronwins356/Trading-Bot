"""Application and bot configuration (YAML + environment variables).

Secrets are **never** stored in YAML or the database: configs reference the
*names* of environment variables (``api_key_env: BINANCE_API_KEY``), which are
read at runtime (optionally from a ``.env`` file).
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Mode = Literal["paper", "live", "replay"]


class ExchangeSettings(BaseModel):
    id: str = "binance"  # any CCXT exchange id
    market_type: Literal["spot", "futures"] = "spot"
    api_key_env: str | None = None
    secret_env: str | None = None
    password_env: str | None = None  # some exchanges (OKX, KuCoin) need a passphrase
    sandbox: bool = False  # use the exchange's testnet when available
    options: dict[str, Any] = Field(default_factory=dict)

    def credentials(self) -> dict[str, str]:
        creds = {}
        for field_name, key in (
            ("api_key_env", "apiKey"),
            ("secret_env", "secret"),
            ("password_env", "password"),
        ):
            env = getattr(self, field_name)
            if env and os.environ.get(env):
                creds[key] = os.environ[env]
        return creds


class CostSettings(BaseModel):
    """Fees/slippage for the simulated exchange (paper / replay / backtests)."""

    fee_maker: float = 0.001
    fee_taker: float = 0.001
    slippage_bps: float = 5.0
    leverage: float = 1.0
    funding_rate_8h: float = 0.0


class RiskSettings(BaseModel):
    max_position_pct: float = 1.0
    max_gross_exposure: float = 1.0
    max_open_positions: int = 10
    min_order_notional: float = 10.0
    max_order_notional: float | None = None
    max_price_deviation_pct: float = 10.0
    daily_loss_limit_pct: float = 6.0
    max_drawdown_pct: float = 30.0
    max_orders_per_minute: int = 30
    flatten_on_halt: bool = False
    protections: list[dict[str, Any]] = Field(default_factory=list)


class JEVSettings(BaseModel):
    """JEV - the open-source decision-making AI. Any OpenAI-compatible endpoint works:
    Ollama (default), vLLM, LM Studio, llama.cpp server, TGI, or a hosted open-model API."""

    enabled: bool = False
    base_url: str = "http://localhost:11434/v1"
    model: str = "gpt-oss:20b"
    api_key_env: str | None = None
    authority: Literal["advisory", "veto", "full"] = "veto"
    temperature: float = 0.2
    max_tokens: int = 800
    timeout_s: float = 90.0
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    min_confidence: float = 0.5
    max_exposure: float = 1.0
    decide_every_bars: int = 1
    anonymize: bool = (
        True  # hide ticker/dates/absolute prices from the model (reduces memorisation/look-ahead)
    )
    analysts: list[str] = Field(default_factory=lambda: ["tsmom", "donchian", "ma_trend", "breakout_20d"])


class ReplaySettings(BaseModel):
    exchange: str = "bitstamp"
    start: str | None = None
    speed: float = 0.0  # seconds to sleep per candle (0 = as fast as possible)
    warmup_bars: int = 400


class BotConfig(BaseModel):
    id: str = Field(default_factory=lambda: "bot-" + secrets.token_hex(3))
    name: str = "My bot"
    strategy: str = "TrendVolTarget"
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1d"
    mode: Mode = "paper"
    capital: float = 10_000.0  # paper starting balance / live capital allocated to this bot
    hp: dict[str, Any] = Field(default_factory=dict)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    costs: CostSettings = Field(default_factory=CostSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    jev: JEVSettings = Field(default_factory=JEVSettings)
    replay: ReplaySettings = Field(default_factory=ReplaySettings)
    warmup_bars: int = 400
    candle_delay_s: float = 5.0  # wait after candle close before fetching (exchange lag)
    autostart: bool = False

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v):
            raise ValueError("bot id may only contain letters, digits, '_', '-', '.'")
        return v


class NotificationSettings(BaseModel):
    telegram_token_env: str | None = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str | None = "TELEGRAM_CHAT_ID"
    discord_webhook_env: str | None = "DISCORD_WEBHOOK_URL"
    webhook_url_env: str | None = "TRADEBOT_WEBHOOK_URL"
    events: list[str] = Field(default_factory=lambda: ["trade_open", "trade_close", "risk", "error", "jev"])


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    token_env: str = "TRADEBOT_API_TOKEN"  # when set, every /api call needs "Authorization: Bearer <token>"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    def token(self) -> str | None:
        return os.environ.get(self.token_env) or None


class AppSettings(BaseModel):
    db_url: str = ""  # default: sqlite:///<data_dir>/tradebot.db
    data_dir: str = ""
    results_dir: str = "research/results"
    api: ApiSettings = Field(default_factory=ApiSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    bots: list[BotConfig] = Field(default_factory=list)

    def resolved_data_dir(self) -> Path:
        from .data.store import default_data_dir

        return Path(self.data_dir).expanduser() if self.data_dir else default_data_dir()

    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        env = os.environ.get("TRADEBOT_DB_URL")
        if env:
            return env
        d = self.resolved_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{d / 'tradebot.db'}"


def load_settings(path: str | Path | None = None) -> AppSettings:
    """Load settings from YAML (default: $TRADEBOT_CONFIG or config/config.yaml) and .env."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    p = Path(path or os.environ.get("TRADEBOT_CONFIG", "config/config.yaml"))
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
        return AppSettings.model_validate(raw)
    return AppSettings()
