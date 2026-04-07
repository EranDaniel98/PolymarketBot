"""Async pub/sub event bus.

Handlers are called sequentially in subscription order. A failing handler
does NOT stop subsequent handlers — it's caught and logged with exc_info —
but its exception IS returned in `publish`'s result list so callers can
react to failures. Phase 3.5 fix: previously the errors were fully swallowed
which made debugging and reliability instrumentation impossible.
"""

import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, event: Any) -> list[Exception]:
        """Publish `event` to all subscribers of `event_type`.

        Returns a list of exceptions raised by handlers. Empty list means
        every handler succeeded. Callers decide how to react (raise, retry,
        alert, etc.).
        """
        handlers = self._subscribers.get(event_type, [])
        errors: list[Exception] = []
        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                handler_name = getattr(handler, "__name__", repr(handler))
                logger.exception(
                    "EventBus handler %s failed for event %s",
                    handler_name, event_type,
                )
                errors.append(exc)
        return errors
