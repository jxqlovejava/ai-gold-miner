"""投资军规自定义配置持久化 — JSON存储."""

from __future__ import annotations

import json
from pathlib import Path

from gold_miner.config import settings


class DoctrineStore:
    """军规启用状态持久化."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else settings.data_path
        self.file_path = self.data_dir / "doctrine_state.json"

    def load_state(self) -> dict[str, bool]:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self, state: dict[str, bool]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def is_enabled(self, rule_id: str) -> bool:
        state = self.load_state()
        return state.get(rule_id, True)  # 默认启用

    def toggle(self, rule_id: str) -> bool:
        state = self.load_state()
        current = state.get(rule_id, True)
        state[rule_id] = not current
        self.save_state(state)
        return not current
