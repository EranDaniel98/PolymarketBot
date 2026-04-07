"""Minimal async circuit breaker + retry helpers.

Avoids an extra dependency on pybreaker/tenacity by implementing exactly
what the bot needs: a per-endpoint breaker that trips after N consecutive
failures and half-opens after a cooldown to probe recovery.

Usage:
    breaker = CircuitBreaker("metar", failure_threshold=5, reset_timeout=60.0)
    try:
        result = await breaker.call(httpx_client.get, url, params=params)
    except CircuitOpenError:
        # Breaker is open; fall back to cached data
        pass

States:
  - closed: normal operation, calls pass through
  - open:   too many failures; immediately raises CircuitOpenError
  - half_open: after reset_timeout, allow ONE probe call
    - probe succeeds → back to closed
    - probe fails   → back to open, cooldown restarts
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the breaker is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def call(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Invoke `func` through the breaker. Raises CircuitOpenError if open."""
        async with self._lock:
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if time.monotonic() - self._opened_at < self._reset_timeout:
                    raise CircuitOpenError(f"breaker '{self.name}' is open")
                # Cooldown elapsed → allow one probe
                self._state = CircuitState.HALF_OPEN
                logger.info("breaker '%s' half-open (probe)", self.name)

        try:
            result = await func(*args, **kwargs)
        except Exception:
            await self._record_failure()
            raise
        else:
            await self._record_success()
            return result

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                logger.info("breaker '%s' closed (recovered)", self.name)
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    async def _record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._state is CircuitState.HALF_OPEN:
                # Probe failed → re-open with fresh cooldown
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning("breaker '%s' re-opened (probe failed)", self.name)
                return
            if self._consecutive_failures >= self._failure_threshold:
                if self._state is not CircuitState.OPEN:
                    logger.warning(
                        "breaker '%s' OPENED after %d consecutive failures",
                        self.name, self._consecutive_failures,
                    )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


# Global registry so the /api/metrics endpoint can report breaker states
_registry: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    *,
    failure_threshold: int = 5,
    reset_timeout: float = 60.0,
) -> CircuitBreaker:
    """Get-or-create a named breaker. Thread-safe because dict ops are atomic."""
    br = _registry.get(name)
    if br is None:
        br = CircuitBreaker(name, failure_threshold=failure_threshold, reset_timeout=reset_timeout)
        _registry[name] = br
    return br


def all_breakers() -> dict[str, CircuitBreaker]:
    return dict(_registry)
