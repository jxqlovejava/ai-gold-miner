"""进程内 TTL 缓存工具 — 消除重复网络拉取.

用于同一进程内多个信号生成器/数据获取器重复下载同一份数据时的去重
(如 etf 生成器 `_cross_asset_signals` 重复拉取、smart_money 与 etf 并行抢拉 GLD 持仓)。

线程安全: ``get_or`` 使用 double-checked locking, 并发冷启动时只执行一次 producer。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


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
