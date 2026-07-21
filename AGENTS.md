# AI Gold Miner — 代理执行与输出规范

> 本文件面向所有使用本项目的 AI 代理（Claude Code / Copilot / Codex / 自定义 Agent）。
> 项目上下文、15 条军规、Munger 模型、Source Truth 层级见 [CLAUDE.md](CLAUDE.md)。

---

## 执行入口

每次涉及金价分析、交易建议、持仓决策时，必须先运行：

```bash
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m gold_miner.cli scan --days 30 --news --sentiment --deep
```

若命令不存在或参数变更，以 `gold-miner --help` 为准。

---

## 强制输出格式

每次运行项目 pipeline 后，输出不能只给结论。必须完整披露以下信息：

### 0. 用户-facing 方向术语

所有面向用户的方向描述**必须**使用中文术语，**禁止**直接使用 `long` / `short` / `neutral` 等英文交易术语：

| 英文信号 | 中文表达 | 含义 |
|---------|---------|------|
| long | **看多** / 做多 | 建议买入或持有 |
| short | **看空** / 做空 | 建议卖出或空仓 |
| neutral | **观望** / 中性 | 建议不操作 |

Pipeline 内部信号（如 `PortfolioManager` 原始输出）可保留英文，但在最终结论、操作清单、情景预测中必须翻译为中文。

### 1. Agent 博弈必须展示三方博弈 + 资金流维度

不能只写"Bull 看多、Bear 看空"。必须列出：

| Agent (固定图标) | 字段 | 说明 |
|-------|------|------|
| 🐮 BullAgent | 立场、建议仓位、信心百分比、常规论据 + **资金流论据** (`smart_money_arguments`) | 不能省略看多理由；**资金流论据不可被常规论据淹没** |
| 🐻 BearAgent | 立场、建议仓位、信心百分比、常规论据 + **资金流论据** (`smart_money_arguments`) | 不能省略看空理由；**资金流论据不可被常规论据淹没** |
| 💼 PortfolioManager | 原始决策 + 军规阻断/调整后的最终决策 | 必须说明是否被军规阻断 |

**资金流维度强制披露规则**：
- 博弈中必须显式列出「👔 资金流论据」小节（即使为空也要标注"本期无资金流信号"）
- 当聪明钱方向与消息面方向矛盾时，必须添加「消息面 vs 资金面背离」警告
- 资金流论据包含：CFTC COT（管理基金净多/商业套保）+ ETF 资金流（GLD/WGC）+ COMEX 大户集中度 + 13F 机构持仓 + 聪明钱综合评分

### 2. 多维度信号必须逐项说明

以下各维信号必须逐项给出（含新增的聪明钱资金流维度）：

| 维度 | 状态要求 |
|------|----------|
| technical | 数值 + 0 信号原因（横盘未触发 / 数据缺失） |
| fundamental | 数值 + 核心指标 |
| 👔 smart_money | **CFTC COT + ETF 资金流 + COMEX 大户 + 13F + 聪明钱综合** — 必须逐子项展示，不得仅给汇总分 |
| news | 数值 + 新闻条数/情感 + API 失败说明 |
| sentiment | 数值 + 持仓/成交量变化（仅散户情绪，不含 COT） |
| event | 数值 + 是否捕获关键事件 |
| polymarket | 数值 + 无数据说明 |
| anomaly/scenario | 数值 + 触发/未触发说明 |

### 3. API 失败必须主动披露

任何 API 失败（NewsAPI 超时、anysearch 配额耗尽、Yahoo 403、DuckDuckGo 失败）必须写明，不能隐藏。示例：

> "消息面因 NewsAPI 超时而失效，已用 WebSearch 补充。"

### 4. Source Truth 强制标注

所有外部信息标注：

- `[verified: T0]` — 一手官方源
- `[verified: T1]` — 官方授权数据终端/预测市场
- `[verified: T2]` — 权威媒体原创
- `[verified: T3]` — 聚合/自媒体
- `[unverified]` — 无法验证

详细层级定义与验证动作见 [CLAUDE.md](CLAUDE.md)「信息验证协议」。

### 5. 军规、Munger、画像必须出现

每次交易建议输出必须包含：

1. 军规审查（r001-r015，✅/⚠️/❌）
2. Munger 模型（2-3 个）
3. 投资者画像匹配
4. 事实 vs 解释 + 置信度

军规与 Munger 模型清单见 [CLAUDE.md](CLAUDE.md)；投资者画像读取自 `data/private/investor_profile.md`（定性）和 `data/private/portfolio.yaml`（持仓数字）。

---

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

### 画像读取失败回退

- 若 `data/private/investor_profile.md` 不存在，读取 `data/investor_profile.example.md` 作为占位画像。
- 输出中必须说明当前使用的是示例画像，交易建议仅作演示，不针对真实持仓。

---

## 禁止行为

- 只引用 pipeline 最终结论而不展示 Agent 博弈
- 对 0 信号维度不做任何说明
- 隐藏 API 失败
- 用搜索摘要直接作为事实证据
- 脱离投资者画像给出建议
- 在 `CLAUDE.md` 或本文件中硬编码私密持仓数据

---

## 快速检查清单

- [ ] 是否运行了 `gold-miner scan`？
- [ ] 是否已读取 `data/private/investor_profile.md` 和 `data/private/portfolio.yaml`？
- [ ] Bull/Bear/PortfolioManager 是否完整列出？
- [ ] 8 维信号是否逐项说明？
- [ ] 是否有 API 失败未披露？
- [ ] 关键事件是否标注 Source Truth？
- [ ] 军规/Munger/画像是否完整？

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
