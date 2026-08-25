"""LLMClient 单元测试 — thinking disabled / 400-422 降级重试 / 无 text 块日志.

背景 (2026-08-22): pro/flash 扩展思考把 max_tokens 全吃在 thinking 块 → chat 返回空 →
语义层静默禁用 → 突发新闻退化为规则判定。修复后 client.py 统一带
thinking: {"type": "disabled"}。这些分支此前无测试覆盖，本文件锁定行为防回归。

注意: client.py 模块级 `from gold_miner.config import settings` 是值拷贝绑定,
patch("gold_miner.config.settings") 无效, 必须 patch("gold_miner.llm.client.settings")。
"""
from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock

import httpx
import pytest

from gold_miner.llm.client import LLMClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> LLMClient:
    """构造已启用的 LLMClient (mock settings, 不真实联网)."""
    settings = MagicMock()
    settings.llm_api_key = "test-key"
    settings.llm_model = "deepseek-chat"
    settings.llm_api_base = "https://api.deepseek.com"
    monkeypatch.setattr("gold_miner.llm.client.settings", settings)
    return LLMClient()


def _text_resp(body: str, status: int = 200) -> httpx.Response:
    """标准 200 文本块响应."""
    return httpx.Response(status, json={"content": [{"type": "text", "text": body}]})


def test_thinking_disabled_in_payload(client):
    """默认 payload 必须带 thinking: disabled (防扩展思考吃满 max_tokens)."""
    with mock.patch("httpx.post", return_value=_text_resp("ok")) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert m_post.call_args_list[0].kwargs["json"]["thinking"] == {"type": "disabled"}


def test_chat_returns_text_block(client):
    """200 + content 列表含 text 块 → 返回 text 内容."""
    with mock.patch("httpx.post", return_value=_text_resp("金价看多")):
        result = client.chat([{"role": "user", "content": "分析"}])
    assert result == "金价看多"


def test_chat_returns_string_content(client):
    """兼容端点直接返回字符串 content."""
    resp = httpx.Response(200, json={"content": "直接字符串"})
    with mock.patch("httpx.post", return_value=resp):
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "直接字符串"


def test_chat_no_text_block_returns_none(client):
    """200 但 content 无 text 块 (thinking 吃满 max_tokens) → 返回 None 而非空串."""
    resp = httpx.Response(200, json={
        "content": [{"type": "thinking", "thinking": "..."}],
        "stop_reason": "max_tokens",
    })
    with mock.patch("httpx.post", return_value=resp):
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result is None


def test_chat_400_thinking_downgrade_retry_once(client):
    """400 + 错误含 thinking → 去掉该字段重试一次, 降级后第二次 payload 无 thinking.

    注: payload.pop("thinking") 原地修改 dict, mock 记录的首个调用是同一对象引用,
    故只断言降级后的第二次调用无 thinking (首次带 thinking 由 test_thinking_disabled 覆盖).
    """
    resp_400 = httpx.Response(400, text='{"error": "thinking not supported"}')
    with mock.patch("httpx.post", side_effect=[resp_400, _text_resp("ok")]) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert m_post.call_count == 2
    assert "thinking" not in m_post.call_args_list[1].kwargs["json"]


def test_chat_422_thinking_retries_no_more_than_once(client):
    """422 + thinking 错误只降级一次; 第二次仍 422 (非 thinking) → 直接返回 None."""
    resp_422 = httpx.Response(422, text="invalid thinking parameter")
    with mock.patch("httpx.post", return_value=resp_422) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert m_post.call_count == 2  # 首次带 thinking, 降级重试一次, 不再第三次


def test_chat_401_auth_error_no_retry(client):
    """401/403 认证错误属非瞬态 → 直接失败不重试."""
    resp_401 = httpx.Response(401, text="invalid api key")
    with mock.patch("httpx.post", return_value=resp_401) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result is None
    m_post.assert_called_once()


@mock.patch("gold_miner.llm.client._sleep_backoff")
def test_chat_429_retries_then_success(mock_sleep, client):
    """429 限流属瞬态 → 退避重试后成功."""
    resp_429 = httpx.Response(429, text="rate limited")
    with mock.patch("httpx.post", side_effect=[resp_429, _text_resp("ok")]) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert m_post.call_count == 2
    mock_sleep.assert_called()


@mock.patch("gold_miner.llm.client._sleep_backoff")
def test_chat_timeout_retries_then_success(mock_sleep, client):
    """httpx 超时属瞬态 → 重试后成功."""
    with mock.patch(
        "httpx.post",
        side_effect=[httpx.TimeoutException("timeout"), _text_resp("ok")],
    ) as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert m_post.call_count == 2


def test_chat_disabled_without_key_returns_none(monkeypatch: pytest.MonkeyPatch):
    """未配置 API key → 直接返回 None, 不发起请求."""
    settings = MagicMock()
    settings.llm_api_key = ""
    settings.llm_model = "deepseek-chat"
    settings.llm_api_base = "https://api.deepseek.com"
    monkeypatch.setattr("gold_miner.llm.client.settings", settings)
    client = LLMClient()
    assert not client.enabled
    with mock.patch("httpx.post") as m_post:
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result is None
    m_post.assert_not_called()


def test_chat_json_parses_codeblock(client):
    """chat_json 从 ```json 代码块提取并解析."""
    resp = _text_resp('```json\n{"delta": "reinforce", "direction": "bullish"}\n```')
    with mock.patch("httpx.post", return_value=resp):
        result = client.chat_json("返回JSON")
    assert result == {"delta": "reinforce", "direction": "bullish"}


def test_chat_json_unparsable_returns_none(client):
    """chat_json 解析不出 JSON → 返回 None (调用方回退)."""
    with mock.patch("httpx.post", return_value=_text_resp("没有JSON")):
        result = client.chat_json("返回JSON")
    assert result is None
