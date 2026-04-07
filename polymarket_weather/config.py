import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

import yaml  # type: ignore[import-untyped]
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PolymarketConfig:
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    private_key: str = ""
    chain_id: int = 137
    host: str = "https://clob.polymarket.com"


@dataclass
class DatabaseConfig:
    url: str = "postgresql+asyncpg://polymarket:polymarket@localhost:5432/polymarket_weather"


@dataclass
class MetarConfig:
    poll_interval: int = 1800
    stale_threshold: int = 10800
    api_url: str = "https://aviationweather.gov/api/data/metar"
    hours_lookback: int = 3
    user_agent: str = "PolymarketWeatherBot/1.0"
    max_results_per_request: int = 400


@dataclass
class TafConfig:
    poll_interval: int = 21600
    api_url: str = "https://aviationweather.gov/api/data/taf"


@dataclass
class NwpConfig:
    poll_interval: int = 21600
    api_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble"
    models: list[str] = field(default_factory=lambda: ["ecmwf_ifs025"])
    deterministic_url: str = "https://api.open-meteo.com/v1/forecast"
    deterministic_models: list[str] = field(
        default_factory=lambda: ["gfs_seamless", "ecmwf_ifs025", "icon_global"]
    )
    rate_limit_per_minute: int = 600
    rate_limit_per_hour: int = 5000


@dataclass
class WeatherConfig:
    metar: MetarConfig = field(default_factory=MetarConfig)
    taf: TafConfig = field(default_factory=TafConfig)
    nwp: NwpConfig = field(default_factory=NwpConfig)


@dataclass
class ForecastConfig:
    metar_only_hours: int = 6
    blend_cutoff_hours: int = 30
    metar_blend_weight: float = 0.6
    min_confidence: float = 0.70
    long_range_min_confidence: float = 0.80
    long_range_days: int = 5
    distribution_df: int = 7
    rmse_by_horizon: dict[str, float] = field(
        default_factory=lambda: {
            "6h": 1.5,
            "12h": 2.0,
            "24h": 2.5,
            "48h": 3.0,
            "72h": 3.5,
            "120h": 4.0,
            "168h": 4.5,
        }
    )


@dataclass
class MarketsConfig:
    scan_interval: int = 300
    discovery_interval: int = 900
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    discovery_endpoint: str = "/events"
    weather_tag_discovery: bool = True
    # Explicit tag ID override (skips runtime discovery when set). 103040 =
    # 'temperature' on Polymarket Gamma, the narrow daily-high-temp tag.
    weather_tag_id: int | None = 103040
    fallback_keywords: list[str] = field(
        default_factory=lambda: ["temperature", "weather", "degrees", "high temp"]
    )
    allowed_metrics: list[str] = field(default_factory=lambda: ["temperature"])


@dataclass
class EdgeConfig:
    min_edge_metar: float = 0.06
    min_edge_blend: float = 0.08
    min_edge_nwp: float = 0.12
    min_liquidity_usdc: float = 200
    min_confidence: float = 0.70
    min_hours_to_resolution: int = 2
    max_hours_to_resolution: int = 168
    kelly_fraction: float = 0.5


@dataclass
class RiskConfig:
    max_position_usdc: float = 50
    min_trade_size_usdc: float = 5
    max_total_exposure_usdc: float = 600
    max_open_positions: int = 20
    daily_loss_cap_usdc: float = 200
    max_exposure_per_city_usdc: float = 150
    max_exposure_per_date_usdc: float = 200
    max_exposure_per_region_usdc: float = 250
    drawdown_pause_pct: float = 0.15
    drawdown_recovery_mode: str = "auto"
    drawdown_recovery_hours: int = 4
    drawdown_recovery_sizing_pct: float = 0.50
    cooldown_after_exit_seconds: int = 1800
    bootstrap_trades: int = 50
    bootstrap_size_usdc: float = 10
    max_forecast_age_minutes: int = 30


@dataclass
class TradingConfig:
    order_type: str = "limit"
    slippage_tolerance: float = 0.02
    max_retries: int = 3
    exit_on_edge_inversion: bool = True
    edge_inversion_threshold: float = -0.05
    paper_trading: bool = True
    paper_balance: float = 1000
    cancel_before_resolution_minutes: int = 120


@dataclass
class CitiesConfig:
    file: str = "config/cities.json"


@dataclass
class SchedulerConfig:
    metar_poll: int = 1800
    taf_poll: int = 21600
    nwp_poll: int = 21600
    market_scan: int = 300
    market_discovery: int = 900
    mismatch_detection: int = 300
    trade_execution: int = 60
    position_monitor: int = 120
    settlement_check: int = 600
    stale_data_check: int = 900
    daily_report: str = "08:00"
    calibration_update: str = "06:00"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    alert_on: dict[str, bool] = field(
        default_factory=lambda: {
            "opportunity_found": True,
            "trade_placed": True,
            "trade_settled": True,
            "risk_limit_approached": True,
            "data_stale": True,
            "system_error": True,
        }
    )


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class CalibrationConfig:
    lookback_days: int = 30
    min_samples_for_reporting: int = 30
    min_samples_for_correction: int = 50
    min_samples_per_bin: int = 10
    apply_correction: bool = False
    max_correction: float = 0.15


@dataclass
class LoggingConfig:
    file_enabled: bool = True
    file_path: str = "logs/bot.jsonl"
    max_size_mb: int = 50
    backup_count: int = 5


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    dash_pass: str = ""


@dataclass
class FeeConfig:
    default_taker_fee: float = 0.01
    maker_fee: float = 0.0
    weather_taker_fee: float = 0.01


@dataclass
class BotConfig:
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    markets: MarketsConfig = field(default_factory=MarketsConfig)
    edge: EdgeConfig = field(default_factory=EdgeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    cities: CitiesConfig = field(default_factory=CitiesConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    fee: FeeConfig = field(default_factory=FeeConfig)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _dict_to_dataclass(cls: type, data: dict[str, Any] | None) -> Any:
    """Recursively convert a nested dict into the target dataclass."""
    if data is None:
        return cls()
    filtered: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        # Resolve the actual type (handles `field(default_factory=…)` annotations)
        ftype = f.type
        # If the annotation is a string (forward ref), resolve it
        if isinstance(ftype, str):
            try:
                hints = get_type_hints(cls)
                ftype = hints.get(f.name, ftype)
            except Exception:
                pass
        if isinstance(v, dict) and isinstance(ftype, type) and hasattr(ftype, "__dataclass_fields__"):
            filtered[f.name] = _dict_to_dataclass(ftype, v)
        else:
            filtered[f.name] = v
    return cls(**filtered)


_ENV_MAP = {
    "DATABASE_URL": ("database", "url"),
    "POLYMARKET_API_KEY": ("polymarket", "api_key"),
    "POLYMARKET_API_SECRET": ("polymarket", "api_secret"),
    "POLYMARKET_API_PASSPHRASE": ("polymarket", "api_passphrase"),
    "POLYMARKET_PRIVATE_KEY": ("polymarket", "private_key"),
    "TELEGRAM_BOT_TOKEN": ("notifications.telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("notifications.telegram", "chat_id"),
    "DASH_PASS": ("web", "dash_pass"),
}


def _apply_env_overrides(config: BotConfig) -> None:
    for env_var, (path, attr) in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        obj = config
        for part in path.split("."):
            obj = getattr(obj, part)
        setattr(obj, attr, value)

    # Railway provides DATABASE_URL as postgresql://; SQLAlchemy async needs postgresql+asyncpg://
    db_url = config.database.url
    if db_url.startswith("postgresql://"):
        config.database.url = "postgresql+asyncpg://" + db_url[len("postgresql://"):]
    elif db_url.startswith("postgres://"):
        config.database.url = "postgresql+asyncpg://" + db_url[len("postgres://"):]

    # Railway injects PORT for web services
    port_env = os.environ.get("PORT")
    if port_env:
        try:
            config.web.port = int(port_env)
            config.web.host = "0.0.0.0"
        except ValueError:
            pass


def load_config(path: Path) -> BotConfig:
    load_dotenv()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    config: BotConfig = _dict_to_dataclass(BotConfig, raw)
    _apply_env_overrides(config)
    return config
