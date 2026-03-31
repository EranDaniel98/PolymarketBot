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

    def can_evaluate(self, market: Market) -> bool:
        """Return False to skip this market entirely. Default: evaluate all markets."""
        return True

    @property
    def eval_interval(self) -> int | None:
        """Seconds between evaluations. None = use global signal_interval.

        Override in subclasses to reduce evaluation frequency for signals
        with long half-lives (e.g., weather=1800s, FLB=1800s).
        """
        return None
