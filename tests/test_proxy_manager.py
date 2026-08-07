"""ProxyManager 代理探测与 DNS 注入测试.

覆盖: 端口探测复用本机已运行代理 / 无代理时优雅降级 / DNS nameserver-policy 注入.
"""

from __future__ import annotations

import socket

from gold_miner.proxy.manager import ProxyManager


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
