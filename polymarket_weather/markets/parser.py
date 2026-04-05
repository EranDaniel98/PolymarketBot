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


def parse_market_question(
    question: str, known_aliases: list[str] | None = None
) -> "ParsedMarket | None":
    """Parse a Polymarket weather question into structured data.

    Handles these patterns (from actual Polymarket markets):
    1. Range: "between X and Y degrees Fahrenheit" or "X to Y degrees F"
    2. Or above: "X degrees Fahrenheit or above"
    3. Or below: "X degrees Fahrenheit or below"
    4. Exceed/above: "exceed X degrees", "above X degrees"
    5. Below/under: "below X degrees", "under X degrees"
    6. Named thresholds: "below freezing"
    7. Negative temperatures: "-5 degrees"

    Returns None if the question doesn't match any weather pattern.
    """
    q = question.lower()

    city = detect_city(question, known_aliases)
    metric = _detect_metric(question)

    # ------------------------------------------------------------------
    # Pattern 3 (most specific first): "be between X and Y degrees F/C"
    # ------------------------------------------------------------------
    m = re.search(
        r'be\s+between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit,
            direction="range",
        )

    # ------------------------------------------------------------------
    # Pattern 1: "between X and Y degrees Fahrenheit/Celsius"
    # ------------------------------------------------------------------
    m = re.search(
        r'between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit,
            direction="range",
        )

    # ------------------------------------------------------------------
    # Pattern 2: "X to Y degrees Fahrenheit/Celsius"
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit,
            direction="range",
        )

    # ------------------------------------------------------------------
    # Pattern 4 (range dash): "X-Y°F" or "X-Y degrees F"
    # Must come before single-value patterns to avoid the lower bound
    # being parsed as an "above" threshold.
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s*[-\u2013]\s*(-?\d+(?:\.\d+)?)\s*[°]?\s*(f|c|fahrenheit|celsius)\b',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit,
            direction="range",
        )

    # ------------------------------------------------------------------
    # Pattern 5: "X degrees Fahrenheit or above"
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)\s+or\s+above',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="above",
        )

    # ------------------------------------------------------------------
    # Pattern 6: "X degrees Fahrenheit or below"
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)\s+or\s+below',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="below",
        )

    # ------------------------------------------------------------------
    # Pattern 15: "X°F or above/below"
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s*°\s*(f|c)\s+or\s+(above|below)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2))
        direction = m.group(3)
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction=direction,
        )

    # ------------------------------------------------------------------
    # Pattern 7: "exceed X degrees Fahrenheit"
    # ------------------------------------------------------------------
    m = re.search(
        r'exceed\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="above",
        )

    # ------------------------------------------------------------------
    # Pattern 8: "above/over/higher than/more than X degrees"
    # ------------------------------------------------------------------
    m = re.search(
        r'(?:above|over|higher\s+than|more\s+than)\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)?',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2)) if m.group(2) else _detect_unit(question)
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="above",
        )

    # ------------------------------------------------------------------
    # Pattern 9: "below/under/lower than/less than X degrees"
    # ------------------------------------------------------------------
    m = re.search(
        r'(?:below|under|lower\s+than|less\s+than)\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)?',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2)) if m.group(2) else _detect_unit(question)
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="below",
        )

    # ------------------------------------------------------------------
    # Pattern 10: "drop to X degrees"
    # ------------------------------------------------------------------
    m = re.search(
        r'drop\s+to\s+(-?\d+(?:\.\d+)?)\s+degrees?\s*(fahrenheit|celsius|f|c)?',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(2)) if m.group(2) else _detect_unit(question)
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=None,
            unit=unit,
            direction="below",
        )

    # ------------------------------------------------------------------
    # Pattern 11: Named threshold "below/under freezing/boiling"
    # ------------------------------------------------------------------
    m = re.search(r'(?:below|under)\s+(freezing|boiling)', q)
    if m:
        name = m.group(1)
        unit = _detect_unit(question)
        thresholds = NAMED_THRESHOLDS_C if unit == "C" else NAMED_THRESHOLDS_F
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=thresholds[name],
            threshold_upper=None,
            unit=unit,
            direction="below",
        )

    # ------------------------------------------------------------------
    # Pattern 12: Named threshold "above/over freezing/boiling"
    # ------------------------------------------------------------------
    m = re.search(r'(?:above|over)\s+(freezing|boiling)', q)
    if m:
        name = m.group(1)
        unit = _detect_unit(question)
        thresholds = NAMED_THRESHOLDS_C if unit == "C" else NAMED_THRESHOLDS_F
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=thresholds[name],
            threshold_upper=None,
            unit=unit,
            direction="above",
        )

    # ------------------------------------------------------------------
    # Pattern 13: "between X and Y degrees" (no unit — default F)
    # ------------------------------------------------------------------
    m = re.search(
        r'between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)\s+degrees?',
        q,
    )
    if m:
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit="F",
            direction="range",
        )

    # ------------------------------------------------------------------
    # Pattern 14: "X and Y°F" range
    # ------------------------------------------------------------------
    m = re.search(
        r'(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)\s*°\s*(f|c)',
        q,
    )
    if m:
        unit = _unit_from_match(m.group(3))
        return ParsedMarket(
            city=city or "",
            metric=metric,
            threshold=float(m.group(1)),
            threshold_upper=float(m.group(2)),
            unit=unit,
            direction="range",
        )

    # No pattern matched — not a weather temperature question
    return None
