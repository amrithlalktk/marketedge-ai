from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "MarketEdge AI"
    environment: str = "development"  # development | test | production
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://marketedge:marketedge@localhost:5432/marketedge"
    redis_url: Optional[str] = "redis://localhost:6379/0"
    celery_broker_url: Optional[str] = None
    celery_always_eager: bool = False

    secret_key: str = "change-me-in-production-please-32+chars"
    encryption_key: Optional[str] = None  # Fernet key for TOTP secrets and provider API keys
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    cookie_secure: bool = False
    cors_origins: Annotated[List[str], NoDecode] = ["http://localhost:3000"]  # comma-separated in env

    # data providers
    market_data_provider: str = "sample"  # sample | csv
    csv_data_dir: str = "./data/csv"
    fundamentals_provider: str = "none"  # none | csv
    sample_universe_size: int = 60
    benchmark_symbol: str = "DEMO_NIFTY50"
    vix_symbol: Optional[str] = "DEMO_INDIAVIX"
    market: str = "NSE"

    # multi-market (Phase 4). Providers per market group: sample | binance (crypto) | twelvedata | csv
    markets_enabled: Annotated[List[str], NoDecode] = ["NSE", "CRYPTO", "US", "EUROPE", "ASIA", "FX"]
    crypto_data_provider: str = "sample"
    global_data_provider: str = "sample"
    fx_data_provider: str = "sample"
    benchmark_crypto: str = "DEMO_BTC"
    benchmark_us: str = "DEMO_US500"
    benchmark_europe: str = "DEMO_EU600"
    benchmark_asia: str = "DEMO_ASIA50"
    benchmark_fx: str = "DEMO_DXY"
    vix_us: Optional[str] = "DEMO_VIX_US"
    binance_spot_url: str = "https://data-api.binance.vision"
    binance_futures_url: str = "https://fapi.binance.com"
    upstox_history_days: int = 2200          # Upstox (NSE) daily history on a full ingest
    upstox_redirect_uri: str = "http://localhost:3000/api/v1/upstox/callback"  # must match the Upstox app's Redirect URL exactly
    angel_history_days: int = 2200           # Angel One (NSE) daily history on a full ingest
    angel_universe_size: int = 500           # NSE point-in-time top-N by traded value (Angel One)
    binance_universe_size: int = 40          # N of the point-in-time top-N universe
    binance_pit_universe: bool = True
    # full history (Binance spot opened July 2017) so trading and delisted pairs cover the SAME period —
    # a shorter window for trading pairs would leave early years populated only by later-delisted coins
    binance_history_days: int = 3450        # pool = all eligible USDT pairs incl. delisted (archive); top-N per date
    binance_exclude: Annotated[List[str], NoDecode] = []  # extra base assets to leave out, comma-separated (e.g. tokenized stocks)
    twelvedata_rate_per_min: int = 8
    twelvedata_api_key: Optional[str] = None

    # news, calendars, AI analyst (Phase 5)
    news_provider: str = "sample"        # sample | finnhub | none
    calendar_provider: str = "sample"    # sample | finnhub | csv | none
    finnhub_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None  # env or ANTHROPIC_API_KEY_FILE; Admin → Providers (encrypted) takes precedence
    analyst_model: str = "claude-opus-5-5"
    analyst_effort: str = "medium"       # low | medium | high | xhigh | max
    analyst_max_tokens: int = 16000
    analyst_timeout_s: float = 120.0
    analyst_per_hour: int = 20           # per-user limit (cost control)

    # notifications (Phase 7) — secrets preferably via Admin → Providers (encrypted)
    public_app_url: str = "http://localhost:3000"
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_starttls: bool = True
    telegram_bot_token: Optional[str] = None
    telegram_bot_username: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None
    vapid_public_key: Optional[str] = None
    vapid_private_key: Optional[str] = None
    vapid_subject: str = "mailto:admin@example.com"
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_template: Optional[str] = None
    whatsapp_access_token: Optional[str] = None
    alerts_max_standard: int = 10
    paper_portfolios_max_standard: int = 1

    # options (Phase 2)
    options_data_provider: str = "sample"  # sample | csv
    options_underlying: str = "DEMO_NIFTY50"  # instrument symbol of the underlying index
    options_display_name: str = "DEMO NIFTY"
    risk_free_rate: float = 0.065
    dividend_yield: float = 0.012

    # Peers allowed to set X-Forwarded-For (loopback + private ranges used by Docker networks).
    trusted_proxies: Annotated[List[str], NoDecode] = ["127.0.0.1/32", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    scan_workers: int = 1   # per-symbol processes for scans/backtests (Linux; run those Celery workers with --pool=solo)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Phase 8 ops
    metrics_token: Optional[str] = None      # bearer token for /metrics; unset → /metrics disabled in production
    allow_sample_in_production: bool = False  # sample (DEMO_) data is refused in production unless explicitly allowed
    # Simple mode: features switched OFF (UI hidden, API routes not mounted, scheduled jobs skipped). Nothing is
    # deleted — remove a name to bring it back. Names: news, calendar, analyst, ml, analytics, backtest, strategies, options
    features_disabled: Annotated[List[str], NoDecode] = []
    allow_registration: bool = True          # False → only existing accounts (single-user installs)
    default_market: Optional[str] = None   # market the UI opens on; unset → first enabled market on real (non-sample) data
    prewarm_top_n: int = 40                 # symbols whose analysis/candles are cached right after each scan
    audit_retention_days: int = 365
    notification_retention_days: int = 90
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10

    bootstrap_admin_email: Optional[str] = None
    bootstrap_admin_password: Optional[str] = None

    @field_validator("cors_origins", "trusted_proxies", "markets_enabled", "binance_exclude", "features_disabled", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @property
    def trusted_proxy_networks(self):
        import ipaddress

        return [ipaddress.ip_network(n, strict=False) for n in self.trusted_proxies]

    def feature_on(self, name: str) -> bool:
        return name not in self.features_disabled

    @property
    def broker_url(self) -> Optional[str]:
        return self.celery_broker_url or self.redis_url

    def validate_production(self) -> None:
        if self.environment == "production":
            if self.secret_key.startswith("change-me") or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters in production")
            if not self.encryption_key:
                raise RuntimeError("ENCRYPTION_KEY must be set in production")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production")
            sample = [k for k in ("market_data_provider", "options_data_provider", "crypto_data_provider", "global_data_provider",
                                  "fx_data_provider", "news_provider", "calendar_provider") if getattr(self, k, None) == "sample"]
            if sample and not self.allow_sample_in_production:
                raise RuntimeError(f"sample (DEMO_) data providers configured in production: {', '.join(sample)}. "
                                   "Configure licensed providers or set ALLOW_SAMPLE_IN_PRODUCTION=true for a labelled demo deployment")


def _file_secrets() -> dict:
    """`FOO_FILE=/run/secrets/foo` supplies FOO from a mounted file (Docker/Kubernetes secrets).
    An explicit FOO in the environment wins; unreadable files fail loudly."""
    import os

    out = {}
    for key, path in os.environ.items():
        if not key.endswith("_FILE") or not path:
            continue
        field = key[:-5].lower()
        if field not in Settings.model_fields or os.environ.get(key[:-5]):
            continue
        with open(path, encoding="utf-8") as fh:
            out[field] = fh.read().strip()
    return out


@lru_cache
def get_settings() -> Settings:
    return Settings(**_file_secrets())
