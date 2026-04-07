"""FastAPI dashboard API endpoints."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from polymarket_weather.api.auth import install_auth, validate_config_update

logger = logging.getLogger(__name__)

app = FastAPI(title="Polymarket Weather Dashboard", version="2.0.0")

# Rate limiting — per-IP. Buckets: reads 60/min, writes 5/min, health 120/min.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# Auth middleware — rejects /api/* requests without a valid X-API-Key header.
# /api/health and static SPA assets bypass. Fails fast at import time if
# DASH_PASS is unset or too short. Can be disabled for tests by setting
# DASHBOARD_AUTH_DISABLED=1 (NEVER set this on Railway).
if os.environ.get("DASHBOARD_AUTH_DISABLED") != "1":
    install_auth(app)

# CORS — allow only the production Railway domain and the local Vite dev server.
# Override with DASHBOARD_CORS_ORIGINS (comma-separated) for extra origins.
_default_origins = [
    "http://localhost:5173",
    "https://polymarketweatherbot-production-12a6.up.railway.app",
]
_extra = os.environ.get("DASHBOARD_CORS_ORIGINS", "")
_origins = _default_origins + [o.strip() for o in _extra.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# --- Pydantic response models ---

class OverviewResponse(BaseModel):
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    open_positions: int = 0
    total_exposure: float = 0.0
    trades_today: int = 0
    win_rate: float = 0.0
    bankroll: float = 0.0
    paper_mode: bool = True
    system_status: str = "running"


class OpportunityItem(BaseModel):
    id: int
    market_id: str
    city: str | None = None
    question: str | None = None
    our_p: float
    market_p: float
    edge: float
    direction: str
    confidence: float
    forecast_source: str
    detected_at: str
    traded: bool
    skip_reason: str | None = None


class PositionItem(BaseModel):
    market_id: str
    direction: str
    entry_price: float
    size_usdc: float
    current_price: float | None = None
    unrealized_pnl: float = 0.0
    city: str = ""
    event_id: str = ""
    entry_time: str = ""
    peak_pnl_pct: float = 0.0


class TradeItem(BaseModel):
    id: int
    market_id: str | None = None
    question: str | None = None
    token_id: str | None = None
    size_usdc: float | None = None
    fill_price: float | None = None
    status: str = ""
    pnl_usdc: float | None = None
    settlement_result: str | None = None
    placed_at: str | None = None
    exit_reason: str | None = None


class WeatherStationItem(BaseModel):
    station_id: str
    city_name: str
    country_code: str
    last_temp_c: float | None = None
    last_report_at: str | None = None
    is_stale: bool = False
    reliability_score: float | None = None


class CalibrationBin(BaseModel):
    bin_lower: float
    bin_upper: float
    predicted_mean: float
    observed_rate: float
    count: int


class ConfigItem(BaseModel):
    key: str
    value: str
    updated_at: str | None = None


class ConfigUpdate(BaseModel):
    key: str
    value: str


class CityMappingItem(BaseModel):
    id: int | None = None
    city_pattern: str
    station_id: str
    priority: int = 0


class SystemEventItem(BaseModel):
    id: int
    event_type: str
    severity: str
    message: str | None = None
    details: dict | None = None
    created_at: str


# --- Shared state (set by app.py at startup) ---

class DashboardState:
    """Mutable state container shared between app.py and API endpoints."""
    def __init__(self) -> None:
        self.session_factory: Any = None
        self.positions: Any = None       # PositionManager
        self.risk: Any = None            # RiskManager
        self.executor: Any = None        # TradeExecutor
        self.config: Any = None          # BotConfig
        self.scanner: Any = None         # WeatherMarketScanner


state = DashboardState()


def set_state(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        setattr(state, k, v)


# --- Endpoints ---

@app.get("/api/overview", response_model=OverviewResponse)
async def get_overview() -> Any:
    result = OverviewResponse(paper_mode=True)
    if state.positions:
        result.open_positions = state.positions.open_count
        result.total_exposure = state.positions.total_exposure
    if state.executor:
        balance = state.executor.get_balance()
        if balance is not None:
            result.bankroll = balance
    if state.config:
        result.paper_mode = state.config.trading.paper_trading
    return result


@app.get("/api/opportunities", response_model=list[OpportunityItem])
async def get_opportunities(traded: bool | None = None, limit: int = 50) -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import Opportunity, PolyMarket
    from sqlalchemy import select
    async with state.session_factory() as session:
        query = select(Opportunity).order_by(Opportunity.detected_at.desc()).limit(limit)
        if traded is not None:
            query = query.where(Opportunity.traded == traded)
        result = await session.execute(query)
        rows = result.scalars().all()
        items = []
        for row in rows:
            mkt = await session.get(PolyMarket, row.market_id)
            items.append(OpportunityItem(
                id=row.id, market_id=row.market_id,
                city=mkt.city_name if mkt else None,
                question=mkt.question if mkt else None,
                our_p=float(row.our_p), market_p=float(row.market_p),
                edge=float(row.edge), direction=row.direction,
                confidence=float(row.confidence),
                forecast_source=row.forecast_source,
                detected_at=row.detected_at.isoformat() if row.detected_at else "",
                traded=row.traded, skip_reason=row.skip_reason,
            ))
        return items


@app.get("/api/positions", response_model=list[PositionItem])
async def get_positions() -> Any:
    if not state.positions:
        return []
    items = []
    for mid, pos in state.positions.positions.items():
        items.append(PositionItem(
            market_id=mid, direction=pos.direction,
            entry_price=pos.entry_price, size_usdc=pos.size_usdc,
            city=pos.city, event_id=pos.event_id,
            entry_time=pos.entry_time.isoformat(),
            peak_pnl_pct=pos.peak_pnl_pct,
        ))
    return items


@app.get("/api/history", response_model=list[TradeItem])
async def get_trade_history(limit: int = 100, offset: int = 0) -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import Trade
    from sqlalchemy import select
    async with state.session_factory() as session:
        query = (select(Trade)
                 .order_by(Trade.placed_at.desc())
                 .offset(offset).limit(limit))
        result = await session.execute(query)
        rows = result.scalars().all()
        return [TradeItem(
            id=r.id, market_id=None, token_id=r.token_id,
            size_usdc=float(r.size_usdc) if r.size_usdc else None,
            fill_price=float(r.fill_price) if r.fill_price else None,
            status=r.status, pnl_usdc=float(r.pnl_usdc) if r.pnl_usdc else None,
            settlement_result=r.settlement_result,
            placed_at=r.placed_at.isoformat() if r.placed_at else None,
            exit_reason=r.exit_reason,
        ) for r in rows]


@app.get("/api/weather", response_model=list[WeatherStationItem])
async def get_weather_stations() -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import IcaoStation
    from sqlalchemy import select
    async with state.session_factory() as session:
        result = await session.execute(
            select(IcaoStation).where(IcaoStation.is_active.is_(True))
        )
        stations = result.scalars().all()
        now = datetime.now(timezone.utc)
        items = []
        for s in stations:
            stale = False
            if s.last_report_at:
                age_hours = (now - s.last_report_at).total_seconds() / 3600
                stale = age_hours > 3
            items.append(WeatherStationItem(
                station_id=s.station_id, city_name=s.city_name,
                country_code=s.country_code,
                last_report_at=s.last_report_at.isoformat() if s.last_report_at else None,
                is_stale=stale,
                reliability_score=float(s.reliability_score) if s.reliability_score else None,
            ))
        return items


@app.get("/api/calibration", response_model=list[CalibrationBin])
async def get_calibration() -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import EdgeCalibration
    from sqlalchemy import select
    async with state.session_factory() as session:
        result = await session.execute(
            select(EdgeCalibration).where(EdgeCalibration.actual_outcome.isnot(None))
        )
        rows = result.scalars().all()
        if not rows:
            return []

        # Group into decile bins
        bins: dict[int, list] = {i: [] for i in range(10)}
        for row in rows:
            p = float(row.our_p)
            bin_idx = min(int(p * 10), 9)
            bins[bin_idx].append(1.0 if row.actual_outcome else 0.0)

        items = []
        for i in range(10):
            if not bins[i]:
                continue
            observed = sum(bins[i]) / len(bins[i])
            items.append(CalibrationBin(
                bin_lower=i * 0.1, bin_upper=(i + 1) * 0.1,
                predicted_mean=(i * 0.1 + (i + 1) * 0.1) / 2,
                observed_rate=observed, count=len(bins[i]),
            ))
        return items


@app.get("/api/config", response_model=list[ConfigItem])
async def get_config() -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import RiskConfigEntry
    from sqlalchemy import select
    async with state.session_factory() as session:
        result = await session.execute(select(RiskConfigEntry))
        rows = result.scalars().all()
        return [ConfigItem(
            key=r.key, value=r.value,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        ) for r in rows]


@app.put("/api/config")
@limiter.limit("5/minute")
async def update_config(request: Request, update: ConfigUpdate) -> Any:
    # Validate input BEFORE touching the DB so bad payloads get a clean 400
    # regardless of DB state. Raises HTTPException(400) on rejection.
    coerced = validate_config_update(update.key, update.value)
    if not state.session_factory:
        raise HTTPException(status_code=503, detail="Database not available")
    logger.info("Dashboard config update: %s=%r", update.key, coerced)
    from polymarket_weather.db.models import RiskConfigEntry
    from sqlalchemy import select
    async with state.session_factory() as session:
        result = await session.execute(
            select(RiskConfigEntry).where(RiskConfigEntry.key == update.key)
        )
        existing = result.scalar_one_or_none()
        stored_value = str(coerced)
        if existing:
            existing.value = stored_value
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session.add(RiskConfigEntry(
                key=update.key, value=stored_value,
                updated_at=datetime.now(timezone.utc),
            ))
        await session.commit()
    return {"status": "ok", "key": update.key, "value": coerced}


@app.get("/api/cities", response_model=list[CityMappingItem])
async def get_city_mappings() -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import CityIcaoMapping
    from sqlalchemy import select
    async with state.session_factory() as session:
        result = await session.execute(select(CityIcaoMapping))
        rows = result.scalars().all()
        return [CityMappingItem(
            id=r.id, city_pattern=r.city_pattern,
            station_id=r.station_id, priority=r.priority,
        ) for r in rows]


@app.put("/api/cities")
@limiter.limit("5/minute")
async def update_city_mapping(request: Request, mapping: CityMappingItem) -> Any:
    if not state.session_factory:
        raise HTTPException(status_code=503, detail="Database not available")
    from polymarket_weather.db.models import CityIcaoMapping
    from sqlalchemy import select
    async with state.session_factory() as session:
        if mapping.id:
            result = await session.execute(
                select(CityIcaoMapping).where(CityIcaoMapping.id == mapping.id)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.city_pattern = mapping.city_pattern
                existing.station_id = mapping.station_id
                existing.priority = mapping.priority
            else:
                raise HTTPException(status_code=404, detail="Mapping not found")
        else:
            session.add(CityIcaoMapping(
                city_pattern=mapping.city_pattern,
                station_id=mapping.station_id,
                priority=mapping.priority,
            ))
        await session.commit()
    return {"status": "ok"}


@app.get("/api/events", response_model=list[SystemEventItem])
async def get_system_events(severity: str | None = None, limit: int = 100) -> Any:
    if not state.session_factory:
        return []
    from polymarket_weather.db.models import SystemEvent
    from sqlalchemy import select
    async with state.session_factory() as session:
        query = select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
        if severity:
            query = query.where(SystemEvent.severity == severity)
        result = await session.execute(query)
        rows = result.scalars().all()
        return [SystemEventItem(
            id=r.id, event_type=r.event_type, severity=r.severity,
            message=r.message, details=r.details,
            created_at=r.created_at.isoformat() if r.created_at else "",
        ) for r in rows]


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Phase 8.1 — scheduler health
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
async def get_jobs() -> Any:
    """Return last-run state for every scheduled job (auth required).

    Useful for surfacing scheduler health in the dashboard. Each job entry
    includes last_started_at, last_finished_at, last_error, success/failure
    counts, and a `healthy` boolean (true if it ran within 2x its interval
    and the last cycle didn't error).
    """
    from polymarket_weather.runtime import get_job_registry
    registry = get_job_registry()
    return [job.to_dict() for job in registry.all()]


# ---------------------------------------------------------------------------
# Phase 8.4 — manual kill switch
# ---------------------------------------------------------------------------

class KillSwitchRequest(BaseModel):
    paused: bool


@app.post("/api/kill_switch")
@limiter.limit("5/minute")
async def set_kill_switch(request: Request, payload: KillSwitchRequest) -> Any:
    """Pause or resume trading via the system_state.is_paused row.

    The risk manager checks is_paused on every trade attempt and rejects
    new orders when paused. Existing positions are NOT closed — this is
    a STOP NEW TRADES switch, not an emergency liquidate.

    For emergency liquidation, use the dashboard's per-position close
    button (forthcoming) or `railway down`.
    """
    if not state.session_factory:
        raise HTTPException(status_code=503, detail="Database not available")

    from polymarket_weather.db import persistence
    await persistence.set_trading_paused(state.session_factory, payload.paused)

    # Mirror into the in-memory RiskManager so it takes effect immediately
    # without waiting for the next DB read.
    if state.risk is not None:
        if payload.paused:
            state.risk.pause()
        else:
            state.risk.resume()

    logger.warning(
        "kill_switch: trading %s by dashboard request",
        "PAUSED" if payload.paused else "RESUMED",
    )
    return {
        "status": "ok",
        "paused": payload.paused,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/kill_switch")
async def get_kill_switch() -> Any:
    """Read the current paused state."""
    if not state.session_factory:
        return {"paused": False, "available": False}
    from polymarket_weather.db import persistence
    paused = await persistence.is_trading_paused(state.session_factory)
    return {"paused": paused, "available": True}


@app.get("/api/metrics")
async def metrics() -> Any:
    """Prometheus-style metrics (text format).

    Protected by the bearer-token middleware alongside all other /api/ routes.
    Includes circuit-breaker states, position counts, DB-backed counters.
    """
    from fastapi.responses import PlainTextResponse

    from polymarket_weather.resilience import CircuitState, all_breakers

    lines: list[str] = []
    lines.append("# HELP pmw_breaker_state Circuit breaker state (0=closed,1=half_open,2=open)")
    lines.append("# TYPE pmw_breaker_state gauge")
    state_map = {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}
    for name, breaker in all_breakers().items():
        lines.append(f'pmw_breaker_state{{name="{name}"}} {state_map[breaker.state]}')
        lines.append(f'pmw_breaker_consecutive_failures{{name="{name}"}} {breaker.consecutive_failures}')

    # Position + bankroll metrics if trade state is available
    if state.positions is not None:
        lines.append("# HELP pmw_open_positions Number of open positions")
        lines.append("# TYPE pmw_open_positions gauge")
        lines.append(f"pmw_open_positions {state.positions.open_count}")
        lines.append("# HELP pmw_total_exposure_usdc Total USDC committed to open positions")
        lines.append("# TYPE pmw_total_exposure_usdc gauge")
        lines.append(f"pmw_total_exposure_usdc {state.positions.total_exposure}")

    if state.risk is not None:
        lines.append("# HELP pmw_daily_loss_usdc Loss accumulated so far today")
        lines.append("# TYPE pmw_daily_loss_usdc gauge")
        lines.append(f"pmw_daily_loss_usdc {state.risk._daily_loss}")
        lines.append("# HELP pmw_completed_trades Total settled trades (bootstrap counter)")
        lines.append("# TYPE pmw_completed_trades counter")
        lines.append(f"pmw_completed_trades {state.risk._completed_trades}")

    lines.append("# HELP pmw_paper_mode 1 if paper trading, 0 if live")
    lines.append("# TYPE pmw_paper_mode gauge")
    paper_mode = bool(state.config and state.config.trading.paper_trading)
    lines.append(f"pmw_paper_mode {int(paper_mode)}")

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# --- Static file serving for production ---

def mount_frontend(frontend_dir: str = "frontend/dist") -> None:
    """Mount React build output for production serving."""
    dist = Path(frontend_dir)
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
        logger.info("Frontend mounted from %s", dist)
