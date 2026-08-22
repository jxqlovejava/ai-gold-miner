"""信号快照落盘测试."""
import json
from pathlib import Path

from gold_miner.signals.snapshot import save_signal_snapshot


class _FakeBundle:
    def __init__(self, counts):
        self._counts = counts

    def dimension_direction_counts(self):
        return self._counts


def _write(tmp_path: Path, counts) -> dict:
    target = tmp_path / "signal_snapshot.json"
    save_signal_snapshot(_FakeBundle(counts), 894.5, path=target)
    return json.loads(target.read_text(encoding="utf-8"))


def test_clarity_bullish(tmp_path):
    snap = _write(tmp_path, (4, 1, 0, 1))
    assert snap["direction_clarity"] == "bullish"
    assert snap["bull_dims"] == 4
    assert snap["bear_dims"] == 1
    assert snap["dispute_dims"] == 0
    assert snap["insufficient_dims"] == 1
    assert snap["current_price"] == 894.5
    assert "timestamp" in snap


def test_clarity_bearish(tmp_path):
    snap = _write(tmp_path, (1, 4, 0, 0))
    assert snap["direction_clarity"] == "bearish"


def test_clarity_mixed_when_close(tmp_path):
    # 4:4 与 4:3 都是方向不明（无分歧维度时不标 conflicted）
    assert _write(tmp_path, (4, 4, 0, 0))["direction_clarity"] == "mixed"
    assert _write(tmp_path, (4, 3, 0, 0))["direction_clarity"] == "mixed"


def test_clarity_conflicted_with_dispute(tmp_path):
    # 有效维度平手 + 存在分歧维度 → conflicted（r013 观望信号）
    snap = _write(tmp_path, (2, 2, 3, 0))
    assert snap["direction_clarity"] == "conflicted"
    assert snap["dispute_dims"] == 3


def test_creates_parent_dir(tmp_path):
    target = tmp_path / "sub" / "dir" / "signal_snapshot.json"
    save_signal_snapshot(_FakeBundle((2, 2, 0, 0)), 900.0, path=target)
    assert target.exists()
