import pytest
from pathlib import Path


def test_load_config_from_yaml(tmp_path):
    yaml_content = """
database:
  url: "postgresql+asyncpg://user:pass@localhost/test"
weather:
  metar:
    poll_interval: 1800
    stale_threshold: 10800
    api_url: "https://aviationweather.gov/api/data/metar"
    hours_lookback: 3
  nwp:
    poll_interval: 21600
    api_url: "https://api.open-meteo.com/v1/ensemble"
    models: ["ecmwf_ifs025"]
forecast:
  metar_only_hours: 6
  blend_cutoff_hours: 30
  metar_blend_weight: 0.6
  min_confidence: 0.70
  distribution_df: 7
edge:
  min_edge_metar: 0.06
  min_edge_nwp: 0.12
  kelly_fraction: 0.5
risk:
  max_position_usdc: 50
  max_total_exposure_usdc: 600
  max_open_positions: 20
trading:
  paper_trading: true
  paper_balance: 1000
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)

    from polymarket_weather.config import load_config
    config = load_config(config_file)

    assert config.database.url == "postgresql+asyncpg://user:pass@localhost/test"
    assert config.weather.metar.poll_interval == 1800
    assert config.weather.nwp.models == ["ecmwf_ifs025"]
    assert config.forecast.distribution_df == 7
    assert config.edge.min_edge_metar == 0.06
    assert config.risk.max_total_exposure_usdc == 600
    assert config.trading.paper_trading is True


def test_defaults_when_section_missing(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  url: test\n")

    from polymarket_weather.config import load_config
    config = load_config(config_file)

    # All other sections should use defaults
    assert config.weather.metar.poll_interval == 1800
    assert config.forecast.metar_only_hours == 6
    assert config.risk.max_position_usdc == 50
    assert config.trading.paper_trading is True


def test_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  url: placeholder\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://real:real@prod/db")

    from polymarket_weather.config import load_config
    config = load_config(config_file)
    assert config.database.url == "postgresql+asyncpg://real:real@prod/db"


def test_missing_config_file():
    from polymarket_weather.config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_empty_config_uses_all_defaults(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    from polymarket_weather.config import load_config
    config = load_config(config_file)
    assert config.polymarket.chain_id == 137
    assert config.weather.metar.api_url == "https://aviationweather.gov/api/data/metar"
