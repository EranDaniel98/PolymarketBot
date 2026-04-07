"""Tests for the secret-redaction logging filter."""

import logging


from polymarket_weather.logging_filters import (
    SecretRedactionFilter,
    _scrub,
)


def _make_record(msg, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redacts_ethereum_private_key():
    out = _scrub("private_key=0xf2513f60d55f2ed7b491c6e6f19c004c0728dbed0c36b714b054d7556b97f22b end")
    assert "0xf2513f60" not in out
    assert "<PRIVATE_KEY_REDACTED>" in out


def test_redacts_telegram_token():
    out = _scrub("Bot token 8654318610:AAHgvaB1QavQkuPD8dAQW9GMBeoTqS7FTgc rejected")
    assert "AAHgvaB1QavQkuPD8dAQW9GMBeoTqS7FTgc" not in out
    assert "<TELEGRAM_TOKEN_REDACTED>" in out


def test_redacts_postgres_url_credentials():
    out = _scrub("Connecting to postgresql+asyncpg://alice:sup3rsecret@db.example.com/prod")
    assert "sup3rsecret" not in out
    assert "alice" not in out
    assert "<REDACTED>" in out
    assert "db.example.com/prod" in out  # host preserved


def test_redacts_api_passphrase_assignment():
    out = _scrub("POLYMARKET_API_PASSPHRASE=ac57c758f0a6805ed380adc70e8b3f1d0")
    assert "ac57c758" not in out
    assert "<REDACTED>" in out


def test_filter_mutates_record_msg():
    f = SecretRedactionFilter()
    r = _make_record("wallet 0xf2513f60d55f2ed7b491c6e6f19c004c0728dbed0c36b714b054d7556b97f22b active")
    assert f.filter(r) is True
    assert "0xf2513f60" not in r.msg


def test_filter_mutates_positional_args():
    f = SecretRedactionFilter()
    r = _make_record("wallet %s active", "0xf2513f60d55f2ed7b491c6e6f19c004c0728dbed0c36b714b054d7556b97f22b")
    assert f.filter(r) is True
    assert "0xf2513f60" not in r.args[0]


def test_filter_preserves_innocuous_strings():
    f = SecretRedactionFilter()
    r = _make_record("scan found %d weather markets", 42)
    f.filter(r)
    assert r.msg == "scan found %d weather markets"
    assert r.args == (42,)


def test_redaction_handles_non_string_values():
    assert _scrub(42) == 42
    assert _scrub(None) is None
    assert _scrub([1, 2]) == [1, 2]


def test_short_hex_not_redacted_as_private_key():
    # 0x + 10 chars — not a private key, should NOT be redacted.
    out = _scrub("event_id=0xabc1234567")
    assert "0xabc1234567" in out
