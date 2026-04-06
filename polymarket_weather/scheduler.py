"""APScheduler 4.x job orchestration for the weather arbitrage bot."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScheduleConfig:
    id: str
    interval_seconds: int | None = None  # For interval triggers
    cron_hour: int | None = None          # For cron triggers
    cron_minute: int | None = None        # For cron triggers


def parse_cron_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string into (hour, minute)."""
    parts = time_str.strip().split(":")
    return int(parts[0]), int(parts[1])


def build_schedules(
    metar_poll: int = 1800,
    taf_poll: int = 21600,
    nwp_poll: int = 21600,
    market_scan: int = 300,
    mismatch_detection: int = 300,
    trade_execution: int = 60,
    position_monitor: int = 120,
    settlement_check: int = 600,
    stale_data_check: int = 900,
    daily_report: str = "08:00",
    calibration_update: str = "06:00",
) -> list[dict]:
    """Build schedule configurations for all jobs.

    market_scan and mismatch_detection are combined into one sequential job.
    Returns a list of schedule dicts with id, type, and trigger params.
    """
    schedules = []

    # Interval-based jobs
    interval_jobs = [
        ("metar_poll", metar_poll),
        ("taf_poll", taf_poll),
        ("nwp_poll", nwp_poll),
        ("market_scan_and_mismatch", min(market_scan, mismatch_detection)),
        ("trade_execution", trade_execution),
        ("position_monitor", position_monitor),
        ("settlement_check", settlement_check),
        ("stale_data_check", stale_data_check),
    ]

    for job_id, interval in interval_jobs:
        schedules.append({
            "id": job_id,
            "type": "interval",
            "interval_seconds": interval,
        })

    # Cron-based jobs
    dr_hour, dr_minute = parse_cron_time(daily_report)
    schedules.append({
        "id": "daily_report",
        "type": "cron",
        "hour": dr_hour,
        "minute": dr_minute,
    })

    cal_hour, cal_minute = parse_cron_time(calibration_update)
    schedules.append({
        "id": "calibration_update",
        "type": "cron",
        "hour": cal_hour,
        "minute": cal_minute,
    })

    return schedules
