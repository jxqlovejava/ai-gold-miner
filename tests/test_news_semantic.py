"""news_semantic 语义推理模块测试.

覆盖: prompt 构建 / 枚举白名单校验 / 批量映射 (index) / 非法输出回退 / 禁用状态 / 类目路由 / 上限截断.
"""

from __future__ import annotations

from gold_miner.sentinel import news_semantic as ns


class _FakeClient:
    """LLMClient 桩 — 预置 chat_json 返回, 记录调用 prompt."""

    enabled = True

    def __init__(self, responses=None):
        self.responses = responses
        self.calls: list[str] = []

    def chat_json(self, prompt, **kw):
        self.calls.append(prompt)
        if isinstance(self.responses, list) and self.responses:
            return self.responses.pop(0)
        return self.responses


def _headlines(*titles, category="energy") -> list[dict]:
    return [{"title": t, "category": category} for t in titles]


# ── prompt 构建 ──


def test_build_prompt_numbers_titles():
    p = ns._build_prompt(_headlines("标题甲", "标题乙"))
    assert "1. 标题甲" in p
    assert "2. 标题乙" in p
    assert "传导框架" in p and "语义判别规则" in p
    assert "不得通过" in p  # 事故反例进 few-shot, 约束'协议≠缓和'


# ── 枚举白名单校验 ──


def test_validate_item_strips_invalid_fields():
    v = ns._validate_item(
        {
            "direction": "super-bullish",  # 非法 → 省略
            "severity": "major",
            "priority": "P0",
            "category": "nonsense",  # 非法 → 省略
            "transmission_chain": "  链  ",
            "confidence": 0.7,
        }
    )
    assert v is not None
    assert "direction" not in v
    assert "category" not in v
    assert v["severity"] == "major"
    assert v["priority"] == "P0"
    assert v["transmission_chain"] == "链"


def test_validate_item_returns_none_when_empty():
    assert ns._validate_item({"direction": "bogus"}) is None
    assert ns._validate_item(None) is None


def test_validate_item_clamps_confidence():
    v = ns._validate_item({"transmission_chain": "x", "confidence": 2.0})
    assert v is not None
    assert v["confidence"] == 1.0


# ── classify_many ──


def test_classify_many_maps_by_index():
    client = _FakeClient(
        {
            "results": [
                {
                    "index": 2,
                    "direction": "bullish",
                    "severity": "major",
                    "priority": "P0",
                    "category": "energy",
                    "transmission_chain": "链B",
                    "confidence": 0.9,
                }
            ]
        }
    )
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    out = a.classify_many(_headlines("标题甲", "标题乙"))
    assert out["标题乙"]["direction"] == "bullish"
    assert "标题甲" not in out


def test_classify_many_returns_empty_on_bad_results_shape():
    client = _FakeClient({"results": "not-a-list"})
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    assert a.classify_many(_headlines("甲")) == {}


def test_classify_many_returns_empty_when_chat_fails():
    client = _FakeClient(None)  # chat_json → None
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    assert a.classify_many(_headlines("甲")) == {}


def test_classify_many_skips_when_disabled():
    client = _FakeClient()
    client.enabled = False
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    assert a.classify_many(_headlines("甲")) == {}


def test_classify_many_filters_non_routed_categories():
    client = _FakeClient()
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    a.classify_many([{"title": "美联储宣布降息", "category": "fed"}])
    assert client.calls == []  # 确定性类目不路由 LLM


def test_classify_many_includes_broad_mentions():
    """候选B (category=None) 须送 AI; 仅过滤带类目的非路由候选."""
    client = _FakeClient({"results": []})
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=10)
    hs = [
        {"title": "伊朗外长访俄", "category": None},  # 候选B → 保留
        {"title": "美联储降息", "category": "fed"},   # 非路由 → 过滤
        {"title": "霍尔木兹封锁", "category": "energy"},  # 路由 → 保留
    ]
    a.classify_many(hs)
    assert len(client.calls) == 1
    p = client.calls[0]
    assert "1. 伊朗外长访俄" in p
    assert "2. 霍尔木兹封锁" in p
    assert "美联储降息" not in p


def test_classify_many_caps_headlines():
    client = _FakeClient({"results": []})
    a = ns.SemanticNewsAnalyzer(client=client, max_headlines=2)
    a.classify_many(_headlines("甲", "乙", "丙", "丁", "戊"))
    assert len(client.calls) == 1  # 单次批量
    assert "1. 甲" in client.calls[0]
    assert "2. 乙" in client.calls[0]
    assert "5. 戊" not in client.calls[0]
