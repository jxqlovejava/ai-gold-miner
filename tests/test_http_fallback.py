"""http_fallback.fallback_get 的 proxy_required 短路行为测试.

背景 (2026-08-21): newsapi.org 国内必须走 mihomo 代理。此前 mihomo 失败时会继续
尝试 direct/curl/system-python/node 多层回退, 每层各吃一个 timeout, 网络抖动时
拖慢整条金价分析 pipeline (完整分析曾达 19min)。新增 proxy_required=True:
mihomo 失败即快速失败, 单层 timeout 收敛。
"""
from __future__ import annotations

import time
from unittest import mock

import httpx
import pytest

from gold_miner.utils.http_fallback import fallback_get


def test_proxy_required_short_circuits_when_mihomo_down():
    """proxy_required=True 且 mihomo 失败 → 立即抛 ConnectError, 不尝试直连."""
    with mock.patch(
        "gold_miner.utils.http_fallback._try_mihomo", return_value=None
    ) as m_mihomo, mock.patch(
        "gold_miner.utils.http_fallback._try_curl",
        side_effect=AssertionError("proxy_required 不应走到 curl"),
    ) as m_curl, mock.patch(
        "gold_miner.utils.http_fallback._try_system_python",
        side_effect=AssertionError("proxy_required 不应走到 system_python"),
    ) as m_sys:
        with pytest.raises(httpx.ConnectError):
            fallback_get(
                "https://newsapi.org/v2/everything",
                params={"q": "gold"},
                timeout=8,
                proxy_required=True,
            )
        m_mihomo.assert_called_once()
        m_curl.assert_not_called()
        m_sys.assert_not_called()


def test_proxy_required_still_works_when_mihomo_ok():
    """proxy_required=True 但 mihomo 成功 → 正常返回."""
    fake = {
        "ok": True,
        "status_code": 200,
        "text": '{"status": "ok"}',
        "headers": {"content-type": "application/json"},
    }
    with mock.patch(
        "gold_miner.utils.http_fallback._try_mihomo", return_value=fake
    ) as m_mihomo:
        resp = fallback_get(
            "https://newsapi.org/v2/everything",
            params={"q": "gold"},
            timeout=8,
            proxy_required=True,
        )
        assert resp.status_code == 200
        assert resp.text == '{"status": "ok"}'
        m_mihomo.assert_called_once()


def test_proxy_required_fast_fail_time_bounded():
    """mihomo 失败时 proxy_required 总耗时 ≤ 单层 timeout (快速失败)."""
    def slow_mihomo(*args, **kwargs):
        time.sleep(0.3)  # 模拟 timeout 前返回失败
        return None

    start = time.monotonic()
    with mock.patch(
        "gold_miner.utils.http_fallback._try_mihomo", side_effect=slow_mihomo
    ), pytest.raises(httpx.ConnectError):
        fallback_get("https://newsapi.org/v2/everything", timeout=8, proxy_required=True)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"proxy_required 快速失败耗时 {elapsed:.2f}s, 应 ≤0.5s"


def test_definitive_connect_error_short_circuits():
    """Phase 2 direct 连接遇确定性错误 (Connection refused) → 立即 raise, 不重试、不走 fallback 链.

    背景 (2026-08-22): 代理故障时 8 路采集在 Connection refused 上各白等
    30s×3 重试 + curl/node 回退, scan 总耗时 278.9s. 确定性错误重试无意义.
    """
    def refused(*args, **kwargs):
        raise httpx.ConnectError("[Errno 61] Connection refused", request=None)

    with mock.patch(
        "gold_miner.utils.http_fallback._try_mihomo", return_value=None
    ), mock.patch(
        "gold_miner.utils.http_fallback._try_curl",
        side_effect=AssertionError("确定性错误不应走到 curl"),
    ) as m_curl, mock.patch(
        "gold_miner.utils.http_fallback._try_system_python",
        side_effect=AssertionError("确定性错误不应走到 system_python"),
    ) as m_sys, mock.patch(
        "gold_miner.utils.http_fallback._try_node",
        side_effect=AssertionError("确定性错误不应走到 node"),
    ) as m_node, mock.patch("httpx.Client.get", side_effect=refused):
        with pytest.raises(httpx.ConnectError, match="fail-fast"):
            fallback_get("https://example.com/data", timeout=8)
        m_curl.assert_not_called()
        m_sys.assert_not_called()
        m_node.assert_not_called()


def test_transient_error_still_falls_back():
    """瞬时错误 (SSL EOF) → 仍走 fallback 链, 不误伤原重试/回退逻辑."""
    def ssl_eof(*args, **kwargs):
        raise httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING")

    with mock.patch(
        "gold_miner.utils.http_fallback._try_mihomo", return_value=None
    ), mock.patch("httpx.Client.get", side_effect=ssl_eof), mock.patch(
        "gold_miner.utils.http_fallback._try_curl",
        return_value={"ok": True, "status_code": 200, "text": "ok", "headers": {}},
    ) as m_curl:
        resp = fallback_get("https://example.com/data", timeout=8)
        assert resp.status_code == 200
        m_curl.assert_called_once()
