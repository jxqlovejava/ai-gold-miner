"""缓存工具 — 进程内 TTL + 跨进程磁盘持久化.

两层缓存消除重复网络拉取:
- ``TtlCache``: 进程内 (单进程多次调用去重), double-checked locking 线程安全。
- ``DiskCache``: 跨进程文件级 (scan 每次都是新进程, yfinance/东财等日频数据
  当天不变, 进程内缓存无法跨进程复用 → 磁盘缓存让后续 scan 直接读文件,
  etf 网络 ~15s → ~0.1s)。mtime 判定 TTL, 原子写防半截文件。

线程安全: ``get_or`` 使用 double-checked locking, 并发冷启动时只执行一次 producer。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from loguru import logger

from gold_miner.config import settings


class TtlCache:
    """线程安全的进程内 TTL 缓存."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._value: Any = None
        self._ts: float = 0.0

    def get(self) -> Any:
        """返回未过期的缓存值; 无缓存或已过期返回 None."""
        with self._lock:
            if self._value is None:
                return None
            if time.time() - self._ts > self._ttl:
                return None
            return self._value

    def set(self, value: Any) -> None:
        """写入缓存."""
        with self._lock:
            self._value = value
            self._ts = time.time()

    def clear(self) -> None:
        """清空缓存 (测试/数据刷新用)."""
        with self._lock:
            self._value = None
            self._ts = 0.0

    def get_or(self, producer: Callable[[], Any]) -> Any:
        """Double-checked locking: 命中缓存返回缓存, 否则执行 producer 并缓存.

        producer 返回 None 表示"无数据/失败", 不缓存 (下次调用会重试)。
        """
        cached = self.get()
        if cached is not None:
            return cached
        with self._lock:
            # 已持有锁, 直接读内部状态 — 不能调用 self.get() (threading.Lock 非重入, 会死锁)
            if self._value is not None and time.time() - self._ts <= self._ttl:
                return self._value
            value = producer()
            if value is not None:
                self._value = value
                self._ts = time.time()
            return value


def _sanitize_key(key: str) -> str:
    """缓存文件名安全化 (仅保留字母数字下划线)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", key)


class DiskCache:
    """跨进程文件级 TTL 缓存.

    序列化分派:
    - ``pd.DataFrame`` → pickle (.pkl)  — 无需 pyarrow
    - 其他 (dict/list/primitive)      → JSON (.json)

    原子写: 写 ``*.tmp`` 再 ``os.replace``, 避免并发读半截文件。
    TTL 用文件 mtime 判定, 过期即 miss (下次重拉)。

    Args:
        key: 缓存标识 (文件名), 自动 sanitize。
        ttl_seconds: 有效期秒数。
        base_dir: 缓存目录, 默认 ``<private_data_path>/cache`` (.gitignore 已覆盖)。
    """

    def __init__(self, key: str, ttl_seconds: float, base_dir: Path | None = None) -> None:
        self._key = _sanitize_key(key)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._dir = Path(base_dir) if base_dir else Path(settings.private_data_path) / "cache"

    def _files(self) -> list[Path]:
        return [
            self._dir / f"{self._key}.pkl",
            self._dir / f"{self._key}.json",
        ]

    def get(self) -> Any:
        """返回未过期缓存; 无/损坏/过期返回 None."""
        for p in self._files():
            try:
                if p.exists() and time.time() - p.stat().st_mtime <= self._ttl:
                    return self._deserialize(p)
            except Exception as e:
                logger.debug(f"DiskCache[{self._key}] 读取失败: {e}")
        return None

    def set(self, value: Any) -> None:
        """写入缓存; None 不写 (调用方失败语义)."""
        if value is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._serialize(value)
        except Exception as e:
            logger.debug(f"DiskCache[{self._key}] 写入失败: {e}")

    def get_or(self, producer: Callable[[], Any]) -> Any:
        """命中缓存返回; 否则执行 producer 并落盘 (double-checked locking)."""
        cached = self.get()
        if cached is not None:
            return cached
        with self._lock:
            cached = self.get()
            if cached is not None:
                return cached
            value = producer()
            self.set(value)
            return value

    def clear(self) -> None:
        """删除缓存文件 (测试/数据刷新用)."""
        for p in self._files():
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    def _serialize(self, value: Any) -> None:
        if isinstance(value, pd.DataFrame):
            final = self._dir / f"{self._key}.pkl"
            tmp = self._dir / f"{self._key}.pkl.tmp"
            value.to_pickle(tmp)
            os.replace(tmp, final)
        else:
            final = self._dir / f"{self._key}.json"
            tmp = self._dir / f"{self._key}.json.tmp"
            tmp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, final)

    @staticmethod
    def _deserialize(path: Path) -> Any:
        if path.suffix == ".pkl":
            return pd.read_pickle(path)
        return json.loads(path.read_text(encoding="utf-8"))
