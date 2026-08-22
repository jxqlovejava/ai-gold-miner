"""ProxyManager 代理探测与 DNS 注入测试.

覆盖: 端口探测复用本机已运行代理 / 无代理时优雅降级 / DNS nameserver-policy 注入 /
fail-fast: 代理启动失败只尝试一次 + 等待超时收敛.
"""

from __future__ import annotations

import socket
import time
from unittest import mock

from gold_miner.proxy.manager import ProxyManager, get_proxied_client


class TestProxyDetection:
    def test_detect_available_port_finds_listener(self) -> None:
        """本机有代理在监听时, fresh 进程也能探测到并复用."""
        mgr = ProxyManager()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            mgr.port = port  # 指向我们的假监听器
            assert mgr._detect_available_port() == port
            assert mgr.is_running is True
        finally:
            s.close()

    def test_no_proxy_returns_none(self) -> None:
        """无代理监听时探测返回 None (优雅降级为直连)."""
        mgr = ProxyManager()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # 关闭后端口空闲
        assert mgr._probe_port(port) is False


class TestDnsInjection:
    def test_ensure_dns_block_injects_when_missing(self) -> None:
        text = "mixed-port: 17890\nproxies:\n  - {name: DIRECT}\n"
        out = ProxyManager._ensure_dns_block(text)
        assert "dns:" in out
        assert "nameserver-policy" in out
        assert "polymarket.com" in out
        assert "cloudflare-dns.com" in out

    def test_ensure_dns_block_keeps_existing(self) -> None:
        """已有 dns: 段时不重复注入."""
        text = "dns:\n    enable: true\nproxies:\n  - {name: DIRECT}\n"
        out = ProxyManager._ensure_dns_block(text)
        assert out == text


class TestFailFast:
    def test_get_proxied_client_start_tried_once(self) -> None:
        """代理启动失败后 get_proxied_client 只尝试一次 (fail-fast), 后续调用直接直连.

        背景 (2026-08-22): 旧逻辑每次调用都 start() + _wait_for_proxy(30s),
        8 路采集在代理故障时反复 Popen mihomo 并各白等 30s.
        """
        mgr = ProxyManager()
        mgr.binary = "/fake/mihomo"
        with mock.patch(
            "gold_miner.proxy.manager.get_proxy_manager", return_value=mgr
        ), mock.patch("gold_miner.proxy.manager.settings") as m_settings, mock.patch.object(
            mgr, "_detect_available_port", return_value=None
        ), mock.patch.object(mgr, "start", return_value=False) as m_start, mock.patch.object(
            mgr, "_wait_for_proxy", return_value=False
        ) as m_wait:
            m_settings.mihomo_sub_url = "https://example.com/sub"
            get_proxied_client()
            get_proxied_client()
            get_proxied_client()
            m_start.assert_called_once()  # 整个会话只尝试启动一次
            m_wait.assert_called_once()

    def test_wait_for_proxy_default_timeout_is_short(self) -> None:
        """_wait_for_proxy 默认超时已从 30s 收敛为 2s (fail-fast)."""
        import inspect

        sig = inspect.signature(ProxyManager._wait_for_proxy)
        assert sig.parameters["timeout"].default == 2.0

    def test_wait_for_proxy_fast_fails_without_proxy(self) -> None:
        """无代理监听时 _wait_for_proxy 在 ~2s 内失败返回, 不是 30s 白等."""
        mgr = ProxyManager()
        mgr.port = 59999  # 无监听端口
        start = time.monotonic()
        assert mgr._wait_for_proxy() is False
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"fail-fast 等待 {elapsed:.1f}s, 应 <3s"
