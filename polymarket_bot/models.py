from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Direction(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Signal:
    source: str
    market_id: str
    direction: Direction
    confidence: float
    reasoning: str
    timestamp: datetime

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")


@dataclass
class Market:
    id: str
    question: str
    end_date: datetime
    tokens: dict[str, str]
    current_price: float
    no_price: float = 0.0
    category: str = ""
    description: str = ""
    volume: float = 0.0
    correlation_tags: list[str] = field(default_factory=list)
    slug: str = ""
    platform_mappings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalEvent:
    signal: Signal
    market: Market


@dataclass(frozen=True)
class ArbitrageOpportunity:
    market_ids: dict[str, str]
    platforms: list[str]
    prices: dict[str, float]
    spread: float
    estimated_profit: float
    confidence: float
    time_sensitivity: str


@dataclass
class TradeDecision:
    market_id: str
    direction: Direction
    amount: float
    confidence: float
    signals: list[Signal]
    order_type: OrderType
    tokens: dict[str, str] = field(default_factory=dict)
    question: str = ""
    is_exit: bool = False
    arb_opportunity: ArbitrageOpportunity | None = None


@dataclass
class TradeExecution:
    market_id: str
    direction: Direction
    amount: float
    price: float
    order_id: str
    status: OrderStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fees: float = 0.0
    realized_pnl: float = 0.0
    error: str | None = None
