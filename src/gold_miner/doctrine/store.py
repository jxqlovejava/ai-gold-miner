"""投资军规自定义配置持久化 — JSON存储."""

from __future__ import annotations

from pathlib import Path

from gold_miner.storage import get_store


class DoctrineStore:
    """军规启用状态持久化."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._store = get_store(private_data_dir=data_dir)

    def load_state(self) -> dict[str, bool]:
        return self._store.load_doctrine_state()

    def save_state(self, state: dict[str, bool]) -> None:
        self._store.save_doctrine_state(state)

    def is_enabled(self, rule_id: str) -> bool:
        state = self.load_state()
        return state.get(rule_id, True)  # 默认启用

    def toggle(self, rule_id: str) -> bool:
        state = self.load_state()
        current = state.get(rule_id, True)
        state[rule_id] = not current
        self.save_state(state)
        return not current
