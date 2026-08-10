# Learning: 利率预期反转构式词表缺失 → 「加息概率走低」误判「强烈利空」

Date: 2026-08-10
Trigger: 用户收到微信推送「加息概率走低」被标「强烈利空黄金」，方向与经济学完全相反

## 学到的规则

1. **裸词子串匹配无法解析「X概率走低」反转构式**
   - 「加息概率走低」= 收紧预期↓ = 实际利率预期↓ = **利多**，但两套关键词引擎都因子串含「加息」直接命中 bearish
   - 根源是**先裸词、后修饰词**的判定顺序：`'加息' in text` 在解析「走低/下降」之前就返回了
   - 同一 bug 已三次同构爆发：8/8「削弱/降温加息预期」、8/10 非农缺「下修」、8/10「概率走低」

2. **多引擎各自维护下降词表 = 必然漂移**
   - sentinel/news_monitor.py（regex 规则 + ambiguity 升级信号）与 signals/recent_events.py（关键词 fallback）各维护一份下降词表，各自缺失不同同义词
   - 修复：收敛到 `src/gold_miner/direction_lexicon.py` 单一真相源，两侧 import 共用
   - 词表需覆盖：走低/下降/回落/下滑/降温/放缓/减弱/降低/消退/下调/下修/下探/下移/走弱/骤降/滑落/降至

3. **反转构式必须双向对称**
   - 「加息概率走低」→ 利多；「降息概率走低」→ 利空（宽松预期消退）
   - 只修加息侧、漏降息侧，会留下对称误判（「降息概率走低」被标「强烈利多」）

4. **确定性类目的反转标题要升级 LLM 二次裁决**
   - fed/macro 类目默认不路由 LLM（防幻觉），但反转构式破坏确定性假设
   - `_SEMANTIC_AMBIGUITY_PATTERNS` 词表与 direction_lexicon 共用，命中则 `escalate` 交 LLM 复核
   - 修复前「加息概率走低」连升级机会都没有——词表同样漏「走低/下滑」

## 修复落点

- 新增 `src/gold_miner/direction_lexicon.py`（词表 + `infer_rate_expectation_direction` + 反转 pattern）
- `recent_events._infer_direction_by_keywords`：反转预检先于裸词检查
- `news_monitor._HIGH_IMPACT_RULES` 反转规则 + `_SEMANTIC_AMBIGUITY_PATTERNS` 改用共享词表
- 回归测试：6 例新增（news_monitor 3 + recent_events 3），覆盖走低/下滑/降至/降息侧/升温不误伤

## 与当日报告的关联

报告内「消息面 +0.28 多」方向本身正确（CME 加息概率崩），本 bug 影响的是**推送链路**（news_monitor 关键词规则）在 LLM 不可用时对「加息概率走低」类标题的方向标注——一旦 LLM 401/SSL 降级即暴露。
