from abc import ABC, abstractmethod
from enum import Enum

from polymarket_bot.models import TradeDecision


class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


class Notifier(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_alert(self, message: str, level: NotificationLevel) -> None: ...

    @abstractmethod
    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None: ...

    @abstractmethod
    async def request_approval(self, decision: TradeDecision) -> bool: ...
