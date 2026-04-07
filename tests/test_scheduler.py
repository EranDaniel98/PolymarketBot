from polymarket_weather.scheduler import build_schedules, parse_cron_time


def test_parse_cron_time_morning():
    h, m = parse_cron_time("08:00")
    assert h == 8
    assert m == 0


def test_parse_cron_time_afternoon():
    h, m = parse_cron_time("14:30")
    assert h == 14
    assert m == 30


def test_build_schedules_count():
    schedules = build_schedules()
    assert len(schedules) == 10  # 8 interval + 2 cron


def test_build_schedules_has_all_jobs():
    schedules = build_schedules()
    job_ids = {s["id"] for s in schedules}
    assert "metar_poll" in job_ids
    assert "market_scan_and_mismatch" in job_ids
    assert "trade_execution" in job_ids
    assert "position_monitor" in job_ids
    assert "settlement_check" in job_ids
    assert "stale_data_check" in job_ids
    assert "daily_report" in job_ids
    assert "calibration_update" in job_ids


def test_build_schedules_cron_jobs():
    schedules = build_schedules(daily_report="09:30", calibration_update="05:00")
    cron_jobs = [s for s in schedules if s["type"] == "cron"]
    assert len(cron_jobs) == 2
    daily = next(s for s in cron_jobs if s["id"] == "daily_report")
    assert daily["hour"] == 9
    assert daily["minute"] == 30


def test_build_schedules_combined_scan_mismatch():
    """market_scan and mismatch_detection should be combined."""
    schedules = build_schedules(market_scan=300, mismatch_detection=300)
    ids = [s["id"] for s in schedules]
    assert "market_scan_and_mismatch" in ids
    # Should NOT have separate market_scan or mismatch_detection
    assert "market_scan" not in ids
    assert "mismatch_detection" not in ids


def test_build_schedules_custom_intervals():
    schedules = build_schedules(metar_poll=900, trade_execution=30)
    metar = next(s for s in schedules if s["id"] == "metar_poll")
    trade = next(s for s in schedules if s["id"] == "trade_execution")
    assert metar["interval_seconds"] == 900
    assert trade["interval_seconds"] == 30
