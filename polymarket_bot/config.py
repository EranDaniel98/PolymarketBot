import os
from pathlib import Path
from dataclasses import dataclass, fields

import yaml


class _SecretStr(str):
    """String that masks itself in repr/str to prevent accidental secret leakage."""
    def __repr__(self) -> str:
        return "'**********'" if self else "''"
    def __str__(self) -> str:
        return super().__str__()  # Full value when explicitly cast to str
    def secret_value(self) -> str:
        return super().__str__()


@dataclass
class PolymarketConfig:
    api_key: str = ""
    api_secret: str = ""
    private_key: str = ""
    chain_id: int = 137

    def __post_init__(self):
        if self.private_key:
            self.private_key = _SecretStr(self.private_key)
        if self.api_secret:
            self.api_secret = _SecretStr(self.api_secret)


@dataclass
class NewsSignalConfig:
    enabled: bool = False
    poll_interval: int = 300
    weight: float = 0.00
    newsapi_key: str = ""


@dataclass
class SocialSignalConfig:
    enabled: bool = False
    poll_interval: int = 600
    weight: float = 0.00
    subreddits: list[str] | None = None


@dataclass
class PollSignalConfig:
    enabled: bool = True
    poll_interval: int = 3600
    weight: float = 0.10


@dataclass
class LLMSignalConfig:
    enabled: bool = True
    weight: float = 0.25
    model: str = "claude-sonnet-4-20250514"
    screening_model: str = "claude-3-haiku-20240307"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    ensemble_enabled: bool = False
    aggregation: str = "trimmed_mean"
    ensemble_models: list[dict] | None = None


@dataclass
class BookmakerSignalConfig:
    enabled: bool = True
    poll_interval: int = 60
    weight: float = 0.10
    odds_api_key: str = ""


@dataclass
class FavoriteLongshotConfig:
    enabled: bool = True
    weight: float = 0.20
    min_price_short: float = 0.92
    max_price_long: float = 0.08
    min_volume: float = 5000
    min_days: int = 3


@dataclass
class DivergenceConfig:
    enabled: bool = True
    weight: float = 0.15
    min_divergence: float = 0.08
    min_forecasters: int = 50
    min_days: int = 3


@dataclass
class WeatherConfig:
    enabled: bool = True
    weight: float = 0.20


@dataclass
class WhaleSignalConfig:
    enabled: bool = False
    weight: float = 0.15
    poll_interval: int = 30
    single_trade_threshold: float = 10000
    cumulative_threshold: float = 25000
    window_seconds: int = 300
    tracked_wallets: list[str] | None = None


@dataclass
class FastTraderConfig:
    enabled: bool = False


@dataclass
class SignalsConfig:
    news: NewsSignalConfig = None
    social: SocialSignalConfig = None
    polls: PollSignalConfig = None
    llm: LLMSignalConfig = None
    bookmaker: BookmakerSignalConfig = None
    favorite_longshot: FavoriteLongshotConfig = None
    divergence: DivergenceConfig = None
    weather: WeatherConfig = None
    whale: WhaleSignalConfig = None
    fast_trader: FastTraderConfig = None

    def __post_init__(self):
        self.news = self.news or NewsSignalConfig()
        self.social = self.social or SocialSignalConfig()
        self.polls = self.polls or PollSignalConfig()
        self.llm = self.llm or LLMSignalConfig()
        self.bookmaker = self.bookmaker or BookmakerSignalConfig()
        self.favorite_longshot = self.favorite_longshot or FavoriteLongshotConfig()
        self.divergence = self.divergence or DivergenceConfig()
        self.weather = self.weather or WeatherConfig()
        self.whale = self.whale or WhaleSignalConfig()
        self.fast_trader = self.fast_trader or FastTraderConfig()


@dataclass
class RiskConfig:
    max_position_pct: float = 0.05
    max_exposure_pct: float = 0.50
    max_daily_loss_pct: float = 0.10
    max_correlated_exposure_pct: float = 0.15
    min_edge: float = 0.03
    kelly_fraction: float = 0.5
    bootstrap_trades: int = 50
    bootstrap_size_pct: float = 0.01
    cooldown_seconds: int = 300


@dataclass
class ExecutionConfig:
    default_order_type: str = "limit"
    max_slippage: float = 0.01
    max_retries: int = 3
    paper_trading: bool = True


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    approval_timeout: int = 300


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = None
    discord: DiscordConfig = None

    def __post_init__(self):
        self.telegram = self.telegram or TelegramConfig()
        self.discord = self.discord or DiscordConfig()


@dataclass
class ConfidenceThresholds:
    auto_execute: float = 0.8
    notify: float = 0.5
    min_signal_sources: int = 2


@dataclass
class StructuralArbConfig:
    enabled: bool = True
    fee_rate: float = 0.02
    min_profit_pct: float = 0.005
    max_position_usd: float = 50.0
    cancel_timeout: int = 60


@dataclass
class ArbitrageConfig:
    poll_interval: int = 30
    min_spread: float = 0.05
    structural_arb: StructuralArbConfig = None

    def __post_init__(self):
        self.structural_arb = self.structural_arb or StructuralArbConfig()


@dataclass
class WebConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class BotConfig:
    polymarket: PolymarketConfig = None
    signals: SignalsConfig = None
    risk: RiskConfig = None
    execution: ExecutionConfig = None
    notifications: NotificationsConfig = None
    confidence_thresholds: ConfidenceThresholds = None
    arbitrage: ArbitrageConfig = None
    web: WebConfig = None

    def __post_init__(self):
        self.polymarket = self.polymarket or PolymarketConfig()
        self.signals = self.signals or SignalsConfig()
        self.risk = self.risk or RiskConfig()
        self.execution = self.execution or ExecutionConfig()
        self.notifications = self.notifications or NotificationsConfig()
        self.confidence_thresholds = self.confidence_thresholds or ConfidenceThresholds()
        self.arbitrage = self.arbitrage or ArbitrageConfig()
        self.web = self.web or WebConfig()


def _dict_to_dataclass(cls, data: dict):
    if data is None:
        return cls()
    field_names = {f.name for f in fields(cls)}
    filtered = {}
    for k, v in data.items():
        if k in field_names:
            f = next(f for f in fields(cls) if f.name == k)
            if hasattr(f.type, '__dataclass_fields__') or (isinstance(f.type, type) and hasattr(f.type, '__dataclass_fields__')):
                filtered[k] = _dict_to_dataclass(f.type, v) if isinstance(v, dict) else v
            else:
                filtered[k] = v
    return cls(**filtered)


_ENV_MAP = {
    "POLYMARKET_API_KEY": ("polymarket", "api_key"),
    "POLYMARKET_API_SECRET": ("polymarket", "api_secret"),
    "POLYMARKET_PRIVATE_KEY": ("polymarket", "private_key"),
    "NEWSAPI_KEY": ("signals.news", "newsapi_key"),
    "ANTHROPIC_API_KEY": ("signals.llm", "anthropic_api_key"),
    "OPENAI_API_KEY": ("signals.llm", "openai_api_key"),
    "ODDS_API_KEY": ("signals.bookmaker", "odds_api_key"),
    "TELEGRAM_BOT_TOKEN": ("notifications.telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("notifications.telegram", "chat_id"),
    "DISCORD_WEBHOOK_URL": ("notifications.discord", "webhook_url"),
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


def load_config(path: Path) -> BotConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)

    config = _dict_to_dataclass(BotConfig, raw)
    _apply_env_overrides(config)
    return config
