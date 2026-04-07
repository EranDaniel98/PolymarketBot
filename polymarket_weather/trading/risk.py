"""Risk manager — enforces all pre-trade limits and tracks exposure."""

from dataclasses import dataclass


@dataclass
class RiskCheck:
    approved: bool
    reason: str  # "" if approved, descriptive reason if rejected


class RiskManager:
    """Enforces all pre-trade risk limits. All parameters configurable."""

    def __init__(
        self,
        max_position_usdc: float = 50.0,
        max_total_exposure_usdc: float = 600.0,
        max_open_positions: int = 20,
        daily_loss_cap_usdc: float = 200.0,
        max_exposure_per_city_usdc: float = 150.0,
        max_exposure_per_region_usdc: float = 250.0,
        drawdown_pause_pct: float = 0.15,
        bootstrap_trades: int = 50,
        bootstrap_size_usdc: float = 10.0,
        min_trade_size_usdc: float = 5.0,
    ):
        self._max_position = max_position_usdc
        self._max_total_exposure = max_total_exposure_usdc
        self._max_open = max_open_positions
        self._daily_loss_cap = daily_loss_cap_usdc
        self._max_city_exposure = max_exposure_per_city_usdc
        self._max_region_exposure = max_exposure_per_region_usdc
        self._drawdown_pause_pct = drawdown_pause_pct
        self._bootstrap_trades = bootstrap_trades
        self._bootstrap_size = bootstrap_size_usdc
        self._min_trade_size = min_trade_size_usdc

        self._positions: dict[str, dict] = {}
        self._daily_loss: float = 0.0
        self._completed_trades: int = 0
        self._paused: bool = False

    def check_trade(
        self, size_usdc: float, city: str, region: str, market_id: str,
    ) -> RiskCheck:
        """Run all pre-trade checks. Returns first failure or approval."""
        if self._paused:
            return RiskCheck(False, "trading_paused")
        if size_usdc > self._max_position:
            return RiskCheck(False, "max_position")
        if size_usdc < self._min_trade_size:
            return RiskCheck(False, "below_minimum")
        if market_id in self._positions:
            return RiskCheck(False, "duplicate_market")
        if len(self._positions) >= self._max_open:
            return RiskCheck(False, "max_open_positions")
        if self.total_exposure + size_usdc > self._max_total_exposure:
            return RiskCheck(False, "total_exposure")
        if self.city_exposure(city) + size_usdc > self._max_city_exposure:
            return RiskCheck(False, "city_exposure")
        if self.region_exposure(region) + size_usdc > self._max_region_exposure:
            return RiskCheck(False, "region_exposure")
        if self._daily_loss >= self._daily_loss_cap:
            return RiskCheck(False, "daily_loss_cap")
        return RiskCheck(True, "")

    def record_entry(self, market_id: str, city: str, region: str, size: float) -> None:
        self._positions[market_id] = {"city": city, "region": region, "size": size}

    def record_exit(self, market_id: str) -> None:
        self._positions.pop(market_id, None)

    def record_daily_loss(self, amount: float) -> None:
        self._daily_loss += amount

    def record_completed_trade(self) -> None:
        self._completed_trades += 1

    def get_max_size(self) -> float:
        if self._completed_trades < self._bootstrap_trades:
            return self._bootstrap_size
        return self._max_position

    def reset_daily(self) -> None:
        self._daily_loss = 0.0

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def total_exposure(self) -> float:
        return float(sum(p["size"] for p in self._positions.values()))

    @property
    def open_count(self) -> int:
        return len(self._positions)

    def city_exposure(self, city: str) -> float:
        return float(sum(p["size"] for p in self._positions.values() if p["city"] == city))

    def region_exposure(self, region: str) -> float:
        return float(sum(p["size"] for p in self._positions.values() if p["region"] == region))
