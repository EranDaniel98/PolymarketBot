import pytest
from pathlib import Path
from polymarket_bot.config import load_config, BotConfig


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
polymarket:
  api_key: "test-key"
  api_secret: "test-secret"
  private_key: "0xdeadbeef"
  chain_id: 137
signals:
  news:
    enabled: true
    poll_interval: 300
    weight: 0.2
    newsapi_key: "nk"
  social:
    enabled: false
    poll_interval: 600
    weight: 0.15
  polls:
    enabled: false
    poll_interval: 3600
    weight: 0.25
  llm:
    enabled: false
    weight: 0.25
    model: "claude-sonnet-4-6-20250514"
  bookmaker:
    enabled: false
    poll_interval: 60
    weight: 0.15
risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300
execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3
notifications:
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""
    approval_timeout: 300
  discord:
    enabled: false
    webhook_url: ""
confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
arbitrage:
  poll_interval: 30
  min_spread: 0.05
""")
    config = load_config(config_file)
    assert isinstance(config, BotConfig)
    assert config.polymarket.api_key == "test-key"
    assert config.risk.kelly_fraction == 0.5
    assert config.signals.news.weight == 0.2


def test_load_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
polymarket:
  api_key: "from-file"
  api_secret: "s"
  private_key: "0x1"
  chain_id: 137
signals:
  news: {enabled: false, poll_interval: 300, weight: 0.2}
  social: {enabled: false, poll_interval: 600, weight: 0.15}
  polls: {enabled: false, poll_interval: 3600, weight: 0.25}
  llm: {enabled: false, weight: 0.25, model: "claude-sonnet-4-6-20250514"}
  bookmaker: {enabled: false, poll_interval: 60, weight: 0.15}
risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300
execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3
notifications:
  telegram: {enabled: false, bot_token: "", chat_id: "", approval_timeout: 300}
  discord: {enabled: false, webhook_url: ""}
confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
arbitrage:
  poll_interval: 30
  min_spread: 0.05
""")
    monkeypatch.setenv("POLYMARKET_API_KEY", "from-env")
    config = load_config(config_file)
    assert config.polymarket.api_key == "from-env"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))
