"""利率预期反转构式 — 唯一真相源词表与判定.

事故背景 (2026-08-10): 推送 "美联储9月加息概率走低" 被裸词 '加息' 误判为
「强烈利空黄金」。根因: sentinel 与 signals 多引擎各自维护下降词表, 反复缺失
『走低/下滑』等常用同义词, 反转构式 (加息概率走低 = 收紧预期↓ = 利多) 漏判,
掉入裸词规则误标反向。此前已同构爆发两次:
  2026-08-08   '削弱/降温/回落加息预期' 误标利空
  2026-08-10   非农 -2.3 万因词表缺 '下修/负增' 误判中性

本模块把利率预期反转的 主体词/预期名词/下降动词 收敛为单一词表, 供
  - sentinel/news_monitor.py   (regex 高影响规则 + LLM 升级信号)
  - signals/recent_events.py   (关键词 fallback)
共用, 防词表漂移再次引发同构误判。

反转语义 (经济学方向, 与 news_semantic._SYSTEM_FRAMEWORK 一致):
  加息 + (预期|概率|压力) + 走低/下降 → 收紧预期↓ → 实际利率预期↓ → 利多金价 (bullish)
  降息 + (预期|概率|压力) + 走低/下降 → 宽松预期↓ → 实际利率预期↑ → 利空金价 (bearish)
"""

from __future__ import annotations

import re

# ── 政策主体 (收紧 / 宽松) ──
HAWK_SUBJECTS = "加息|鹰派|hike|hawkish|tighten"
DOVE_SUBJECTS = "降息|鸽派|cut|dovish|ease|loosen"

# ── 预期名词 (反转构式的量词) ──
# 2026-09-05 事故补词: '紧迫性/急迫性/迫切性/必要性' 等价于"预期强度"名词。
# 非农 actual "就业强韧→降息紧迫性下降→利空黄金" 因缺此词未命中反转构式,
# 裸'降息'子串先短路判 bullish → 假阳性冲突 (gold_bias 已写 bearish)。同构于 2026-08-10。
EXPECTATION_NOUNS = (
    "预期|概率|压力|步伐|周期|押注|定价|odds|pricing|expectation"
    "|紧迫性|急迫性|迫切性|必要性"
)

# ── 下降动词 (预期/概率走低)
# 2026-08-10 修复: 补 '走低/下滑/下探/下移/走弱/骤降/滑落/降至', 覆盖 "加息概率走低/下滑" 等。
DECLINE_VERBS = (
    "走低|下降|回落|下滑|降温|放缓|减弱|降低|消退|下调|下修|下探|下移|走弱|骤降|大降|滑落|降至|削减"
    "|fall|drop|slide|cool|fade|trim|ease"
)

# 主体与名词之间、名词与下降动词之间允许的间隔: 非结构化字符 (不跨分句, 防跨句误配)
_STRUCT_GAP = r"[^，。；;:：、\n]"

# ── 反转构式正则: 主体 → 预期名词 → 下降动词 ──
_HAWK_REVERSAL_RE = re.compile(
    rf"(?:{HAWK_SUBJECTS}){_STRUCT_GAP}{{0,10}}?(?:{EXPECTATION_NOUNS}){_STRUCT_GAP}{{0,8}}?(?:{DECLINE_VERBS})",
    re.IGNORECASE,
)
_DOVE_REVERSAL_RE = re.compile(
    rf"(?:{DOVE_SUBJECTS}){_STRUCT_GAP}{{0,10}}?(?:{EXPECTATION_NOUNS}){_STRUCT_GAP}{{0,8}}?(?:{DECLINE_VERBS})",
    re.IGNORECASE,
)

# 供 news_monitor 直接用作 ImpactRule.pattern (反转规则须置于裸'加息/降息'规则之前)
HAWK_REVERSAL_PATTERN = (
    rf"(?:{HAWK_SUBJECTS}){_STRUCT_GAP}{{0,10}}?(?:{EXPECTATION_NOUNS}){_STRUCT_GAP}{{0,8}}?(?:{DECLINE_VERBS})"
)
DOVE_REVERSAL_PATTERN = (
    rf"(?:{DOVE_SUBJECTS}){_STRUCT_GAP}{{0,10}}?(?:{EXPECTATION_NOUNS}){_STRUCT_GAP}{{0,8}}?(?:{DECLINE_VERBS})"
)


def infer_rate_expectation_direction(text: str) -> str | None:
    """利率预期反转构式 → 方向 ("bullish"/"bearish"), 未命中返回 None.

    需在裸 '加息/降息' 子串检查之前调用, 否则子串先命中错误方向
    (事故 2026-08-10: '加息概率走低' 含 '加息' → 误判利空)。

    判定: 主体为收紧侧 (加息/鹰派) → 收紧预期消退 → bullish;
          主体为宽松侧 (降息/鸽派) → 宽松预期消退 → bearish。
    """
    if _HAWK_REVERSAL_RE.search(text):
        return "bullish"
    if _DOVE_REVERSAL_RE.search(text):
        return "bearish"
    return None
