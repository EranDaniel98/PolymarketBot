from abc import ABC, abstractmethod
from polymarket_bot.models import Market, Signal


class SignalPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def evaluate(self, market: Market) -> Signal | None: ...
