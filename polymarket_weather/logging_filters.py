"""Logging filters that scrub secrets from log records before they ship.

Installed on the root logger at startup. Covers three common leak patterns:
  - Telegram bot tokens (e.g. '1234567890:AABB...')
  - Hex Ethereum private keys (0x + 64 hex chars)
  - Database URLs with embedded user:password credentials

The filter mutates both record.msg (the format string) and record.args (the
interpolated positional args), because logging libraries often pass the secret
as a %s arg rather than as part of the message itself.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Ethereum private key (0x + exactly 64 hex chars)
    (re.compile(r"0x[a-fA-F0-9]{64}\b"), "<PRIVATE_KEY_REDACTED>"),
    # Telegram bot token: <9-10 digits>:<30+ url-safe chars>
    (re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{30,}\b"), "<TELEGRAM_TOKEN_REDACTED>"),
    # Postgres/MySQL URL with credentials: scheme://user:pass@host
    (
        re.compile(r"(postgres(?:ql)?(?:\+asyncpg)?|mysql|mongodb)://[^:/@\s]+:[^@\s]+@"),
        r"\1://<REDACTED>:<REDACTED>@",
    ),
    # Polymarket API passphrase / secret (32-char hex blobs following known labels)
    (
        re.compile(r"(api_passphrase|api_secret|POLYMARKET_API_PASSPHRASE|POLYMARKET_API_SECRET)\s*[=:]\s*[^\s'\"]+"),
        r"\1=<REDACTED>",
    ),
]


def _scrub(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    out = value
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class SecretRedactionFilter(logging.Filter):
    """Filter that redacts secrets from log records in-place.

    Applied to the root logger so it catches everything regardless of which
    component emitted the log. Returns True (i.e. keep the record) after mutating.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub the pre-format message
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)

        # Scrub the args tuple (positional format args)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_scrub(a) for a in record.args)

        # Scrub any pre-formatted exc_text / stack_info — these are strings attached
        # by handlers when formatting exceptions. We don't touch them here because
        # they're built lazily at format time; instead we scrub the final message
        # via a formatter wrapper below.
        return True


class SecretRedactionFormatter(logging.Formatter):
    """Formatter wrapper that scrubs the final formatted string.

    Used as a belt-and-suspenders on top of SecretRedactionFilter: some exception
    tracebacks only reveal the secret string AFTER the formatter runs, so we
    re-scrub the final output.
    """

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return _scrub(self._inner.format(record)) or ""


def install_on_root() -> None:
    """Install the redaction filter + formatter on the root logger.

    Idempotent: calling twice will not install twice.
    """
    root = logging.getLogger()
    marker = "_pmw_redaction_installed"
    if getattr(root, marker, False):
        return
    redact = SecretRedactionFilter()
    root.addFilter(redact)
    for handler in root.handlers:
        handler.addFilter(redact)
        if handler.formatter is not None and not isinstance(
            handler.formatter, SecretRedactionFormatter
        ):
            handler.setFormatter(SecretRedactionFormatter(handler.formatter))
    setattr(root, marker, True)
