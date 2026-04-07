"""Position manager — tracks open positions, computes PnL, detects exits."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TrackedPosition:
    market_id: str
    direction: str         # "YES" or "NO"
    entry_price: float
    size_usdc: float
    city: str
    event_id: str
    entry_time: datetime
    peak_pnl_pct: float = 0.0

    def compute_pnl(self, current_price: float) -> float:
        """Compute unrealized PnL based on current market price."""
        if self.direction == "YES":
            return (current_price - self.entry_price) * self.size_usdc / self.entry_price
        else:  # NO
            return (self.entry_price - current_price) * self.size_usdc / self.entry_price

    def compute_pnl_pct(self, current_price: float) -> float:
        """Compute PnL as a percentage of entry."""
        if self.entry_price <= 0:
            return 0.0
        if self.direction == "YES":
            return (current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - current_price) / self.entry_price

    def compute_settlement_pnl(self, outcome: str, fee: float = 0.01) -> float:
        """Compute PnL at market settlement.

        For YES position: pnl = ((1.0 if outcome == YES else 0.0) - entry_price) * size - fees
        For NO position:  pnl = ((1.0 if outcome == NO else 0.0) - entry_price) * size - fees
        """
        if self.direction == "YES":
            payout = 1.0 if outcome == "YES" else 0.0
        else:
            payout = 1.0 if outcome == "NO" else 0.0

        shares = self.size_usdc / self.entry_price
        gross_pnl = (payout - self.entry_price) * shares
        fees = self.size_usdc * fee
        return gross_pnl - fees


class PositionManager:
    """Tracks all open positions and manages exits."""

    def __init__(self, edge_inversion_threshold: float = -0.05):
        self._positions: dict[str, TrackedPosition] = {}
        self._edge_inversion_threshold = edge_inversion_threshold

    @property
    def positions(self) -> dict[str, TrackedPosition]:
        return self._positions

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.size_usdc for p in self._positions.values())

    def track_entry(
        self, market_id: str, direction: str, entry_price: float,
        size_usdc: float, city: str, event_id: str,
    ) -> None:
        self._positions[market_id] = TrackedPosition(
            market_id=market_id, direction=direction,
            entry_price=entry_price, size_usdc=size_usdc,
            city=city, event_id=event_id,
            entry_time=datetime.now(timezone.utc),
        )

    def track_exit(self, market_id: str) -> None:
        self._positions.pop(market_id, None)

    def update_peak(self, market_id: str, current_price: float) -> None:
        pos = self._positions.get(market_id)
        if pos:
            pnl_pct = pos.compute_pnl_pct(current_price)
            if pnl_pct > pos.peak_pnl_pct:
                pos.peak_pnl_pct = pnl_pct

    def check_exit(
        self, market_id: str, current_price: float, current_edge: float,
    ) -> tuple[bool, str]:
        """Check if a position should be exited.

        Returns (should_exit, reason).
        """
        pos = self._positions.get(market_id)
        if not pos:
            return False, ""

        # Edge inversion: our forecast now disagrees with our position
        if current_edge < self._edge_inversion_threshold:
            return True, "edge_inversion"

        return False, ""

    def get_position(self, market_id: str) -> TrackedPosition | None:
        return self._positions.get(market_id)
