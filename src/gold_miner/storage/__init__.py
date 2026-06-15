"""存储工厂 — 根据配置返回 PersonalDataStore 实例."""

from __future__ import annotations

from pathlib import Path

from gold_miner.config import settings
from gold_miner.storage.local import LocalFileStore


def get_store(private_data_dir: str | Path | None = None) -> LocalFileStore:
    """返回配置的数据存储实例.

    Args:
        private_data_dir: 可选，覆盖默认的 private_data_dir。
            主要用于测试传入临时目录实现隔离。

    目前仅支持 local 类型，未来可扩展为 encrypted、remote 等。
    """
    if private_data_dir is not None:
        return LocalFileStore(private_data_dir)
    return LocalFileStore(settings.private_data_path)
