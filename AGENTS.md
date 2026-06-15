# AGENTS.md — AI Gold Miner 项目级代理规范

> 本文件面向所有使用本项目的 AI 代理（Claude Code / Copilot / Codex / 自定义 Agent）。
> 规则与 CLAUDE.md 互补，重点约束**分析输出的完整性和可追溯性**。

## 强制输出标准

### 1. 金价分析必须跑 pipeline

每次涉及金价分析、交易建议、持仓决策时，必须先运行：

```bash
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m gold_miner.cli scan --days 30 --news --sentiment --deep
```

### 2. Agent 博弈必须完整披露（三方博弈）

不能只写"Bull 看多、Bear 看空"。必须列出：

- **BullAgent**：立场、建议仓位、信心百分比、逐条论据及对应评分
- **BearAgent**：立场、建议仓位、信心百分比、逐条论据及对应评分
- **PortfolioManager**：原始决策 + 军规阻断/调整后的最终决策

### 3. 信号维度必须逐项检查

输出中必须包含以下 8 维信号的数值和状态说明：

| 维度 | 状态要求 |
|------|----------|
| technical | 数值 + 0 信号原因 |
| fundamental | 数值 + 核心指标 |
| news | 数值 + 新闻条数/情感 + API 失败说明 |
| sentiment | 数值 + 持仓/成交量变化 |
| etf_flow | 数值 + 无数据说明 |
| event | 数值 + 是否捕获关键事件 |
| polymarket | 数值 + 无数据说明 |
| anomaly/scenario | 数值 + 触发/未触发说明 |

### 4. 数据失败必须主动披露

任何 API 失败（NewsAPI 超时、anysearch 配额耗尽、Yahoo 403、DuckDuckGo 失败）必须写明，不能隐藏。

### 5. Source Truth 强制标注

所有外部信息标注：

- `[verified: T0]` — 一手官方源
- `[verified: T1]` — 官方授权数据终端/预测市场
- `[verified: T2]` — 权威媒体原创
- `[verified: T3]` — 聚合/自媒体
- `[unverified]` — 无法验证

### 6. 军规、Munger、画像必须出现

每次交易建议输出必须包含：

1. 军规审查（r001-r015，✅/⚠️/❌）
2. Munger 模型（2-3 个）
3. 投资者画像匹配
4. 事实 vs 解释 + 置信度

## 执行与回退

### 输出无效判定

若输出缺少以下任意一项，视为**无效分析**，必须重新生成：

1. 完整 Agent 博弈（Bull/Bear/PortfolioManager）
2. 8 维信号逐项说明
3. API 失败披露（如有）
4. 军规 / Munger / 画像

### 数据获取失败回退

- 单一 API 失败：写明失败原因，用其他可用数据源/搜索补充，并标注 `[unverified]` 或对应 Source Truth 等级。
- 多个核心 API 失败（如 NewsAPI + Yahoo Finance 同时不可用）：**暂停给出方向性交易建议**，仅输出事实汇总和风险提示，明确告知用户"当前数据不足以支持决策"。
- 禁止使用缓存的过期数据冒充实时分析。

---

## 禁止行为

- 只引用 pipeline 最终结论而不展示 Agent 博弈
- 对 0 信号维度不做任何说明
- 隐藏 API 失败
- 用搜索摘要直接作为事实证据
- 脱离投资者画像给出建议

## 快速检查清单

- [ ] 是否运行了 `gold-miner scan`？
- [ ] Bull/Bear/PortfolioManager 是否完整列出？
- [ ] 8 维信号是否逐项说明？
- [ ] 是否有 API 失败未披露？
- [ ] 关键事件是否标注 Source Truth？
- [ ] 军规/Munger/画像是否完整？
