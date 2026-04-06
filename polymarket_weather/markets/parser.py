"""
Market question parser for Polymarket weather markets.

Extracts structured data from question text using layered regex patterns.
Handles: range markets (2°F/2°C buckets), above/below thresholds,
named thresholds (freezing/boiling), and negative temperatures.
"""

import re
from dataclasses import dataclass


@dataclass
class ParsedMarket:
    city: str                          # Detected city name (lowercase)
    metric: str                        # "temperature" | "precipitation" | "snow" | "wind"
    threshold: float                   # Lower bound (or single threshold for above/below)
    threshold_upper: float | None      # Upper bound for range markets
    unit: str                          # "F" | "C"
    direction: str                     # "above" | "below" | "range"


# Named threshold lookup tables
NAMED_THRESHOLDS_F = {"freezing": 32.0, "boiling": 212.0}
NAMED_THRESHOLDS_C = {"freezing": 0.0, "boiling": 100.0}

# Default city aliases (20 cities from spec) used when caller provides no list
DEFAULT_CITY_ALIASES: list[str] = [
    "new york city",
    "new york",
    "nyc",
    "los angeles",
    "la",
    "chicago",
    "houston",
    "phoenix",
    "philadelphia",
    "san antonio",
    "san diego",
    "dallas",
    "san jose",
    "austin",
    "jacksonville",
    "fort worth",
    "columbus",
    "charlotte",
    "indianapolis",
    "denver",
    "seattle",
    "washington dc",
    "boston",
    "miami",
    "atlanta",
    "london",
    "paris",
    "tokyo",
    "sydney",
    "toronto",
]


def detect_city(question: str, known_aliases: list[str] | None = None) -> str | None:
    """Find a city name in the question text.

    Matches longest alias first to handle 'new york city' before 'new york'.
    """
    q = question.lower()
    if known_aliases is None:
        known_aliases = DEFAULT_CITY_ALIASES
    # Sort by length descending so "new york city" matches before "new york"
    for alias in sorted(known_aliases, key=len, reverse=True):
        # Use word-boundary matching so "la" doesn't match inside "atlantis"
        pattern = r'\b' + re.escape(alias.lower()) + r'\b'
        if re.search(pattern, q):
            return alias.lower()
    return None


def _detect_unit(text: str) -> str:
    """Return 'C' if celsius indicators found, else 'F' (default)."""
    lower = text.lower()
    if "celsius" in lower or re.search(r'°\s*c\b', lower) or re.search(r'\bc\b', lower):
        # Avoid matching 'c' in words — only standalone or after °
        if "celsius" in lower or re.search(r'(?:degrees?\s*c|°\s*c)\b', lower, re.IGNORECASE):
            return "C"
    if "fahrenheit" in lower or re.search(r'(?:degrees?\s*f|°\s*f)\b', lower, re.IGNORECASE):
        return "F"
    return "F"  # Polymarket is overwhelmingly Fahrenheit


def _unit_from_match(unit_str: str | None) -> str:
    """Normalise a regex-captured unit string to 'F' or 'C'."""
    if unit_str is None:
        return "F"
    u = unit_str.strip().lower()
    if u in ("c", "celsius"):
        return "C"
    return "F"


def _detect_metric(question: str) -> str:
    """Detect the weather metric from question text."""
    q = question.lower()
    if any(kw in q for kw in ("snow", "snowfall", "blizzard")):
        return "snow"
    if any(kw in q for kw in ("precipitation", "rainfall", "rain", "inches of")):
        return "precipitation"
    if any(kw in q for kw in ("wind", "gust", "mph", "knots")):
        return "wind"
    # Default — temperature
    return "temperature"


# ===========================================================================
# Phase 5.1: dispatch-table parser
#
# Each pattern is a small `(regex, factory)` tuple. The main loop tries them
# in order and returns the first match. Factories receive the regex match,
# the prepared (city, metric, original_question) context, and return a
# ParsedMarket. They are tiny, declarative, and trivial to test in isolation.
#
# Order matters: more specific patterns must come before less specific ones
# (e.g. "be between" before "between", range patterns before single-value).
# ===========================================================================

# Common regex fragments
_NUM = r"(-?\d+(?:\.\d+)?)"
_UNIT = r"(fahrenheit|celsius|f|c)"
_UNIT_OPT = r"(fahrenheit|celsius|f|c)?"


def _make_range(direction: str = "range"):
    """Factory builder for two-bound range patterns."""
    def factory(m, city, metric, question):
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "", metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit, direction=direction,
        )
    return factory


def _make_single(direction: str, unit_group: int = 2, *, fallback_detect: bool = False):
    """Factory builder for single-threshold patterns (above/below/etc)."""
    def factory(m, city, metric, question):
        if fallback_detect and m.group(unit_group) is None:
            unit = _detect_unit(question)
        else:
            unit = _unit_from_match(m.group(unit_group))
        return ParsedMarket(
            city=city or "", metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit, direction=direction,
        )
    return factory


def _named_threshold_factory(direction: str):
    """Factory for 'above/below freezing/boiling' patterns."""
    def factory(m, city, metric, question):
        name = m.group(1)
        unit = _detect_unit(question)
        thresholds = NAMED_THRESHOLDS_C if unit == "C" else NAMED_THRESHOLDS_F
        return ParsedMarket(
            city=city or "", metric=metric,
            threshold=thresholds[name],
            threshold_upper=None,
            unit=unit, direction=direction,
        )
    return factory


def _between_no_unit_factory():
    def factory(m, city, metric, question):
        return ParsedMarket(
            city=city or "", metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit="F", direction="range",
        )
    return factory


def _degrees_or_dir_factory():
    """Factory for 'X°F or above/below' — direction comes from the regex."""
    def factory(m, city, metric, question):
        unit = _unit_from_match(m.group(2))
        return ParsedMarket(
            city=city or "", metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit, direction=m.group(3),
        )
    return factory


# Pattern table — most specific first.
_PATTERNS: list[tuple[re.Pattern, callable]] = [
    # Range patterns (must precede single-value)
    (
        re.compile(rf"be\s+between\s+{_NUM}\s+and\s+{_NUM}\s+degrees?\s*{_UNIT}"),
        _make_range(),
    ),
    (
        re.compile(rf"between\s+{_NUM}\s+and\s+{_NUM}\s+degrees?\s*{_UNIT}"),
        _make_range(),
    ),
    (
        re.compile(rf"{_NUM}\s+to\s+{_NUM}\s+degrees?\s*{_UNIT}"),
        _make_range(),
    ),
    # Dash range: "65-70°F" or "65–70 F"
    (
        re.compile(rf"{_NUM}\s*[-\u2013]\s*{_NUM}\s*[°]?\s*{_UNIT}\b"),
        _make_range(),
    ),

    # "X degrees F or above/below"
    (
        re.compile(rf"{_NUM}\s+degrees?\s*{_UNIT}\s+or\s+above"),
        _make_single("above"),
    ),
    (
        re.compile(rf"{_NUM}\s+degrees?\s*{_UNIT}\s+or\s+below"),
        _make_single("below"),
    ),
    # "X°F or above/below"
    (
        re.compile(rf"{_NUM}\s*°\s*(f|c)\s+or\s+(above|below)"),
        _degrees_or_dir_factory(),
    ),

    # "exceed X degrees F"
    (
        re.compile(rf"exceed\s+{_NUM}\s+degrees?\s*{_UNIT}"),
        _make_single("above"),
    ),

    # "above/over/higher than/more than X degrees [F]"
    (
        re.compile(
            rf"(?:above|over|higher\s+than|more\s+than)\s+{_NUM}\s+degrees?\s*{_UNIT_OPT}"
        ),
        _make_single("above", fallback_detect=True),
    ),
    # "below/under/lower than/less than X degrees [F]"
    (
        re.compile(
            rf"(?:below|under|lower\s+than|less\s+than)\s+{_NUM}\s+degrees?\s*{_UNIT_OPT}"
        ),
        _make_single("below", fallback_detect=True),
    ),
    # "drop to X degrees"
    (
        re.compile(rf"drop\s+to\s+{_NUM}\s+degrees?\s*{_UNIT_OPT}"),
        _make_single("below", fallback_detect=True),
    ),

    # Named thresholds: "below/above freezing/boiling"
    (
        re.compile(r"(?:below|under)\s+(freezing|boiling)"),
        _named_threshold_factory("below"),
    ),
    (
        re.compile(r"(?:above|over)\s+(freezing|boiling)"),
        _named_threshold_factory("above"),
    ),

    # No-unit fallbacks (default Fahrenheit)
    (
        re.compile(rf"between\s+{_NUM}\s+and\s+{_NUM}\s+degrees?"),
        _between_no_unit_factory(),
    ),
    (
        re.compile(rf"{_NUM}\s+and\s+{_NUM}\s*°\s*(f|c)"),
        _make_range(),
    ),
]


def parse_market_question(
    question: str, known_aliases: list[str] | None = None
) -> "ParsedMarket | None":
    """Parse a Polymarket weather question into structured data.

    Iterates the dispatch table in order; first match wins. Returns None if
    no pattern matches. See _PATTERNS for the full pattern list.
    """
    q = question.lower()
    city = detect_city(question, known_aliases)
    metric = _detect_metric(question)

    for pattern, factory in _PATTERNS:
        m = pattern.search(q)
        if m:
            return factory(m, city, metric, question)
    return None
