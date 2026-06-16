"""共享 pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """提供一个临时项目目录."""
    return tmp_path


@pytest.fixture
def sample_gold_df() -> pd.DataFrame:
    """返回 30 天模拟金价数据."""
    dates = [datetime.now() - timedelta(days=i) for i in range(30, 0, -1)]
    prices = [2000.0 + i * 0.5 for i in range(30)]
    return pd.DataFrame({
        "timestamp": dates,
        "open": [p - 1 for p in prices],
        "high": [p + 2 for p in prices],
        "low": [p - 2 for p in prices],
        "close": prices,
        "volume": [1000 + i for i in range(30)],
    })


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """返回 mock 配置对象."""
    settings = MagicMock()
    settings.risk_profile = "moderate"
    settings.demo_mode = False
    settings.initial_capital_usd = 100_000.0
    settings.max_position_pct = 0.8
    settings.stop_loss_pct = 0.03
    settings.take_profit_pct = 0.06
    settings.data_dir = Path("./data")
    settings.private_data_dir = Path("./data/private")
    settings.log_level = "INFO"
    monkeypatch.setattr("gold_miner.config.settings", settings)
    return settings


@pytest.fixture
def mock_signal_bundle() -> SignalBundle:
    """返回带有一个看多信号的 SignalBundle."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="央行购金",
        dimension="fundamental",
        direction=SignalDirection.BULLISH,
        strength=SignalStrength.MODERATE,
        score=0.5,
        description="央行购金利好",
    ))
    return bundle
