"""LLM 客户端 — DeepSeek API (Anthropic Messages API 兼容)."""

from typing import Any

import httpx
from loguru import logger

from gold_miner.config import settings


class LLMClient:
    """DeepSeek LLM 客户端.

    用法:
        client = LLMClient()
        result = client.chat("分析这篇文章...")
    """

    def __init__(self) -> None:
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_api_base.rstrip("/")
        self.model = settings.llm_model
        self.enabled = bool(self.api_key)

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str | None:
        """调用 DeepSeek Anthropic-compatible Messages API."""
        if not self.enabled:
            logger.warning("LLM API key 未配置")
            return None

        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                logger.warning(f"LLM API 错误 ({resp.status_code}): {resp.text[:200]}")
                return None

            data = resp.json()
            # Anthropic Messages format: content is a list of blocks
            content = data.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        return block.get("text", "")
                # fallback: try first block
                if len(content) > 0:
                    return content[0].get("text", "")
            elif isinstance(content, str):
                return content
            return None
        except httpx.HTTPError as e:
            logger.warning(f"LLM API 请求失败: {e}")
            return None

    def analyze_article(
        self,
        text: str,
        rule_sentiment: str = "neutral",
        rule_score: float = 0.0,
        rule_claims: list[dict] | None = None,
        manipulation_flags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """分析文章并返回结构化结果."""
        if not self.enabled:
            return None

        claims_text = ""
        if rule_claims:
            claims_text = "\n".join(
                f"- [{c.get('category', '')}] {c.get('claim', '')}"
                for c in rule_claims
            )

        flags_text = ""
        if manipulation_flags:
            flags_text = "\n".join(f"- {f}" for f in manipulation_flags)

        prompt = f"""你是一个黄金投资分析助手。请分析以下关于黄金市场的文章，从专业投资角度给出判断。

## 规则引擎预分析结果
- 情感方向: {rule_sentiment}
- 情感得分: {rule_score:+.2f} (-1看跌, +1看涨)
- 检测到的操纵话术:
{flags_text if flags_text else '  无'}
- 提取的关键主张:
{claims_text if claims_text else '  无'}

## 文章内容
{text[:6000]}

## 分析要求
请以JSON格式返回分析结果（不要包含其他文字）：
```json
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "confidence": 0.0-1.0,
  "credibility": 0.0-1.0,
  "horizon_days": 整数,
  "key_drivers": ["因素1", "因素2"],
  "target_price_high": 数字或null,
  "target_price_low": 数字或null,
  "reasoning": "推理链，控制在200字以内",
  "is_pumping": true/false,
  "is_institutional_manipulation": true/false
}}
```

其中:
- credibility: 文章可信度 (0=完全不可信, 1=非常权威)
- is_pumping: 是否在刻意唱多/唱空带节奏
- is_institutional_manipulation: 是否有机构操纵痕迹
- key_drivers: 影响金价的核心驱动因素列表
- horizon_days: 如果文章观点成立，预判有效时间窗口（天）"""

        messages = [{"role": "user", "content": prompt}]
        result = self.chat(messages, max_tokens=2048, temperature=0.3)

        if not result:
            return None

        # 解析 JSON 响应
        import json
        import re

        # 尝试提取 JSON 块
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", result)
        if json_match:
            result = json_match.group(1).strip()

        try:
            return json.loads(result)
        except json.JSONDecodeError:
            # 尝试直接解析整个响应
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                logger.warning(f"LLM 返回无法解析的JSON: {result[:200]}")
                return {"raw_response": result, "parse_error": True}
