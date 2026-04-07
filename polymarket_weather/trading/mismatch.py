"""Mismatch detector — compares forecast probability vs market price to find edge."""

from dataclasses import dataclass


@dataclass
class EdgeResult:
    raw_edge: float      # |our_p - market_p|
    direction: str       # "YES" or "NO"
    ev: float            # Expected value = raw_edge (corrected formula)


@dataclass
class OpportunitySignal:
    market_id: str
    our_p: float
    market_p: float
    edge: float          # raw_edge
    direction: str       # YES or NO
    confidence: float
    forecast_source: str
    hours_to_resolution: float
    station_stale: bool


def compute_edge(our_p: float, market_p: float) -> EdgeResult:
    """Compute edge between our forecast probability and market price.

    If our_p > market_p: YES is underpriced, buy YES.
    If our_p < market_p: YES is overpriced, buy NO.
    EV = raw_edge (NOT edge * (1 - market_p) — that formula was wrong).
    """
    if our_p >= market_p:
        raw_edge = our_p - market_p
        direction = "YES"
    else:
        raw_edge = market_p - our_p
        direction = "NO"
    return EdgeResult(raw_edge=raw_edge, direction=direction, ev=raw_edge)


def compute_kelly_size(
    edge: float,
    market_price: float,
    direction: str,
    bankroll: float,
    kelly_fraction: float,
    fee: float,
    max_position: float,
    min_position: float,
) -> float:
    """Compute position size using fractional Kelly criterion.

    Math (Phase 4.2 — derived, not heuristic):
      For a binary prediction-market bet at price `p`, paying $1 on win:
        - YES bet pays (1-p)/p per unit risked → Kelly f = edge / (1 - p)
        - NO bet pays p/(1-p) per unit risked  → Kelly f = edge / p
      Fees reduce the effective edge linearly: effective_edge = edge - fee.
      If effective_edge <= 0 we skip the trade entirely (fees eat the edge).

    Safety rails:
      - Price clamped to [0.05, 0.95] to prevent blow-up at extremes.
      - Raw fractional Kelly capped at 0.25 (a 25% bankroll bet is the
        absolute hard cap regardless of edge).
      - Final size capped at max_position and floored at min_position.
    """
    if edge <= 0 or bankroll <= 0:
        return 0.0

    # Phase 4.2: subtract fee from edge BEFORE computing Kelly. The old code
    # used `effective_kelly = raw_kelly * (1 - fee/edge)` which is a heuristic
    # approximation; subtracting from edge directly is the derived form.
    effective_edge = edge - fee
    if effective_edge <= 0:
        return 0.0  # Fees eat the edge — don't trade

    # Clamp price to avoid blow-up at extremes
    clamped_price = max(0.05, min(0.95, market_price))

    # Compute Kelly fraction based on direction (using effective_edge)
    if direction == "YES":
        raw_kelly = effective_edge / (1 - clamped_price)
    elif direction == "NO":
        raw_kelly = effective_edge / clamped_price
    else:
        return 0.0

    # Hard cap at 0.25 of bankroll before fractional Kelly
    capped = min(raw_kelly, 0.25)

    # Apply fractional Kelly and bankroll
    size = bankroll * capped * kelly_fraction

    # Clamp to position limits
    size = min(size, max_position)

    # Below minimum → don't trade
    if size < min_position:
        return 0.0

    return round(size, 2)


def filter_opportunity(
    opp: OpportunitySignal,
    min_edge: float,
    min_confidence: float,
    min_hours: float,
    max_hours: float,
) -> bool:
    """Check if an opportunity passes all threshold filters."""
    if opp.edge < min_edge:
        return False
    if opp.confidence < min_confidence:
        return False
    if opp.hours_to_resolution < min_hours:
        return False
    if opp.hours_to_resolution > max_hours:
        return False
    if opp.station_stale:
        return False
    return True


def get_min_edge_for_source(
    forecast_source: str,
    min_edge_metar: float = 0.06,
    min_edge_blend: float = 0.08,
    min_edge_nwp: float = 0.12,
) -> float:
    """Return tiered minimum edge based on forecast source."""
    if forecast_source == "metar":
        return min_edge_metar
    elif forecast_source == "metar_nwp":
        return min_edge_blend
    elif forecast_source == "nwp_ensemble":
        return min_edge_nwp
    return min_edge_nwp  # Default to most conservative
