# AI Gold Miner — 项目上下文与决策约束

> **中文代号：青蚨** 🐛 — 典出《淮南子》「青蚨还钱」：母子青蚨分离必自动飞回，古人以母血涂钱、钱花出后复还。寓意这个系统长期持有的每一分投入，终会带着收益回流。
>
> **触发快速通道（2026-08-24 起）**：金价分析 / 黄金走势 / "分析"/"gold" → **直接前台跑 `bash scripts/quick_scan.sh`**，不 invoke skill（省 1 轮大上下文推理 ~40-60s + 6k 注入；运行时协议已内化到「强制分析流程」节）。
> `gold-analysis-pipeline` SKILL.md 仍是完整协议唯一真相源（输出铁律 1-10 / r032 摩擦成本 / 各步骤塑形）；API 签名、枚举值、命令模板在 `references/api-reference.md`，事件同步铁律在 `references/event-sync.md`。**仅排错 / 协议不清 / 手动降级 / 调 API 前才读取**，禁止凭记忆猜参数。

## 投资者画像

> 私密画像数据分两份文件维护，避免数字重复导致不一致：
> - **定性画像**（风险偏好、交易风格、信源偏好、笔记）存于 `data/private/investor_profile.md`。
> - **定量持仓**（持仓量、成本均价、止损价、额度）存于 `data/private/portfolio.yaml`，作为唯一数字真相源。
> - **个人敏感参数**（具体止损价位、ATR 参数、持仓拆分）存于 `data/personal_rules.md`。
>
> 每次涉及金价分析、交易建议、持仓决策时，先读取 `data/private/investor_profile.md`，再读取 `data/private/portfolio.yaml` 获取当前持仓数字。
> 若文件不存在，分别使用 `data/investor_profile.example.md` 和 `data/portfolio.example.yaml` 作为占位，并提示用户填充真实数据。

### 公开约束（不依赖私密文件）

| 约束项 | 公开规则 |
|--------|---------|
| 单品种仓位上限 | 20 万元 |
| 总敞口上限 | 所有黄金头寸合计不超过 80%，保留 20% 现金 |
| 杠杆 | 不借钱，仅使用自有资金 |
| 方向 | 仅做多 |
| 硬止损 | 成本价的 -30%（具体价位见投资者画像） |
| 风险偏好 | 平衡型（最大回撤容忍 10-20%） |
| 持仓周期 | 1-3 年 |
| 信源要求 | 多源交叉验证，重点识别机构「带节奏」 |

## 工具使用规则

- **默认搜索工具**：本项目所有网络搜索默认使用 `anysearch` skill，除非用户明确指定其他工具或该工具不可用。
- 当 `anysearch` 不可用时，依次 fallback 到 `web-access` skill 或 `WebSearch`。
- **自动提交**：完整实现一个功能或 fix 解决问题后，自动调用 `/gcp` 命令提交并推送，无需等待用户确认，直接执行。
- **自动部署**：每次 `/gcp` 提交推送之后，立即通过 `scripts/deploy_gold_miner_to_hermes.sh` 部署到 Hermes 服务器，同步代码+数据+条件单+日历，并更新服务器 crontab 和 Hermes cron job。无需等待用户确认，直接执行。
  - 如果是新增监控脚本（如 `adaptive_gold_monitor.py`、`overnight_news_scanner.py`、`evening_event_preview.py` 等），部署后需用 `hermes cron create` 在服务器上注册对应的 Hermes cron job。
  - 部署完成后输出部署清单：同步了哪些文件、新增/更新了哪些 cron job。
- **自主修Bug**：运行过程中发现系统 Bug 或错误（如导入报错、脚本异常、数据格式不匹配等），应尝试自行修复，而非仅报告错误等待用户指示。修复后继续原任务流程。
- **配置不入库**：服务器配置（Hermes 配置 `data/private/hermes_config.sh`、证书等）一律保存到 `data/private/` 目录，GitHub 提交时不得提交（`.gitignore` 已覆盖 `data/private/`）。

### 日期查询硬规则

涉及日期/时间的查询必须校验（事故史见 git history 与 `.learnings/`，下列规则已程序化强制）：

1. **以系统 `currentDate` 为唯一真相源** — 提取年/月/日/星期，搜索关键词不硬编码年份；搜后反查星期（官方数据不在非工作日发布）
2. **关键事件日期（PCE、非农、CPI、FOMC 等）必须查官网 release schedule**，不以搜索摘要为最终依据
3. **DOW 参考表强制校验** — 分析输出前运行 `PYTHONPATH=src python scripts/validate_calendar_dates.py --ref-table 30`，报告中所有「周X+事件名」与参考表 ET 星期逐条一致。代码层面 `add_event` 已对已知确定性事件（初请=周四、FOMC=周三、非农=周五）做 DOW 硬阻断
4. **官方 schedule 日期比对** — `gold-miner prepare` 自动跑 `scripts/validate_bls_schedule.py`（TradingEconomics 日历比对未发布的 ppi/cpi/pce/非农/FOMC纪要），日期不一致阻断分析；TE 不可用降级 warning。本地兜底 `calendar_time_rules.check_relative_anchors()`（同月 CPI/PPI 相邻 ≤5 天）

### 快速演变事件搜索规则

geo/policy_shift/trade_war/fed_emergency 等持续演变事件不能「搜一次永久有效」。核心：**时间约束搜索 + 逆转/修正优先搜索 + 多时点交叉验证 + stale 事件定期重验**（`EarlyWarningEngine().check_stale_events`）。完整 5 条铁律与查询模板见 [`skills/gold-analysis-pipeline/references/event-sync.md`](skills/gold-analysis-pipeline/references/event-sync.md)「快速演变事件搜索铁律」节。

## 输出规则

每次输出交易建议或决策分析时，必须包含以下三个板块，并**使用中文方向术语（看多/看空/观望），不直接使用 long/short/neutral**：

### 1. 军规自查
引用具体规则 ID（r001-r035），标注每条规则对该决策的判定（✅/⚠️/❌）。完整军规见 [`docs/doctrine.md`](docs/doctrine.md)。

### 2. Munger 模型
引用 2-3 个与当前决策最相关的思维模型，说明如何应用。模型库（29 个）见 [`docs/munger_models.md`](docs/munger_models.md)。

### 3. 画像匹配
明确说明建议是否在该用户的约束范围内（仓位上限 / 持仓周期 / 风险偏好 / 硬止损规则）。

**禁止**：脱离画像约束的建议、没有军规审查的建议、忽略信源质量的分析。

## 输出格式规范

**🔴 每次金价分析必须输出完整报告，不可省略任何板块给摘要。** 细则全部由 skill 定义并程序强制，此处不重复：

- 逐步骤输出 / 禁「写文件再概述」/ 命令代码行不入报告正文 → SKILL.md 输出铁律 1-5
- 固定格式（板块顺序+空态规则）→ [`docs/report_template.md`](docs/report_template.md) + SKILL.md 铁律 6
- 目标区间预测（三情景+概率+r035 传导链）→ SKILL.md 铁律 8
- 主驱动因素板块（一阶/二阶）→ SKILL.md 铁律 9
- 报告骨架组装 + 落盘校验 + 模型全文直发 → SKILL.md 铁律 10（Write hook + `scripts/validate_report_format.py` 强制）
- 详细格式（Agent 博弈披露、8 维逐项、API 失败披露）→ [AGENTS.md](AGENTS.md)

## 强制分析流程

每次涉及金价分析、交易建议、持仓决策时，必须走完项目内置的完整 pipeline（`bash scripts/quick_scan.sh` 单轮拿数），禁止基于单一维度或未经交叉验证的信息直接下结论。

**快速通道运行时协议（2026-08-24 起，替代 invoke skill）**：

1. 触发词 → 直接**前台**跑 `bash scripts/quick_scan.sh`（强制重扫 `FORCE_SCAN=1`）；禁后台跑 + 通知驱动。
2. stdout = 模式行 + bundle；stdout 超限被持久化时直 Read `data/private/.last_bundle.txt`；**禁 Read scan_report 全文**。
3. `ASSEMBLE_SKIP`（REUSE/RERUN 通用）→ bundle 骨架 = 盘上已校验报告全文，**逐字转贴输出**，前置 2-3 行增量摘要（决策一句话 + 价格校验 LATEST_PRICE vs 报告价，跳变 >1% 走 FORCE_SCAN；+ 条件单变动说明）。
4. `ASSEMBLE_OK` → 只增量填充 3 板块（§1.1 主驱动 + 驱动排序表 / §1.2 三情景目标区间含概率 + r035 传导链 / §7 条件单审查表）；REUSE 时行情引文用 LATEST_PRICE 覆盖 → `Write` 落盘 `data/output/金价分析_YYYY-MM-DD.md`（Write hook 自动校验板块间禁 `---`，失败删后重写）→ 模型全文直发同内容。
5. scan 后零深挖：不单独重跑引擎子命令 / 不追源码 / 不手写整份报告绕过组装。
6. 完整协议真相源：[`skills/gold-analysis-pipeline/SKILL.md`](skills/gold-analysis-pipeline/SKILL.md)（仓库根 `skills/`，`~/.claude/skills/gold-analysis-pipeline` 为 symlink）—— 排错 / 协议不清 / 手动降级时按需读取。

### Pipeline 八步总览

| 步骤 | 内容 | 核心输出 |
|------|------|---------|
| 一 | 信息准备（日历同步+深度新闻） | 时效性加权表 + Monitor检查 + Staleness验证 + P0扫描 |
| 二 | 多维度信号采集（8维） | 技术面/基本面/消息面/资金流/情绪面信号 |
| 三 | Source Truth + 事实vs解释 | 来源验证表 + 置信度标注 |
| 四 | 军规自查 | r001-r035 逐条判定 (✅/⚠️/❌) |
| 五 | Munger 模型 | 2-3个思维模型 |
| 六 | 画像匹配 | 约束检查表 |
| 七 | 🐮Bull/🐻Bear/💼PM Agent博弈 + 交易建议 + 条件单 | Bull辩论→Bear辩论→PM决策→条件单审查 |
| 八 | 后续事件 + 情景预案 + Monitor | 未来14天事件 + 情景推演 + Monitor创建 |

### 关键不变规则

- **日历校验**：每次分析前必须运行 `validate_calendar_dates.py --ref-table 30`，不通过不得继续（走 scan 时已内置，不重复跑）。
- **时效性衰减**：<24h 权重 1.0 → 24-48h 0.7 → 48-72h 0.5 → 3-7d 0.3 → >7d 0。
- **资金流维度**：CFTC COT + ETF + COMEX 大户 + 13F 逐子项展示；聪明钱与消息面矛盾时标注「消息面 vs 资金面背离」。
- **Agent 博弈**：在军规/Munger/画像之后（第七步），图标固定 🐮/🐻/💼。
- **条件单**：分析后必须审查所有 active 条件单，逐条给出保留/撤销/修改建议。

## 投资军规

完整军规体系（r001-r035）维护在 [`docs/doctrine.md`](docs/doctrine.md)。核心原则：

- **仓位管理**：单笔不超过总资产 20%，总敞口不超过 80%，黄金占比超 50% 提示集中风险，不追涨杀跌。
- **情绪纪律**：连续止损后休整、盈利上移止损、多维度确认、必须设止损。
- **操作纪律**：重大数据前提前调整、用条件单代替盯盘、减仓趁反弹。
- **趋势与止盈**：ATR 移动止盈、均线趋势过滤。
- **建仓与估值**：分批建仓、安全边际加仓、黄金仓位再平衡。

## 信息验证协议（Sourcing Truth Verification）

交易决策数据 / 带日期事件 / 机构观点必须验证。层级：**T0** 一手官方（BLS/CME FedWatch/FRED/央行官网）> **T1** 授权终端（Wind/Bloomberg/路透）> **T2** 权威媒体（WSJ/FT/财新）> **T3** 聚合自媒体（需双源交叉）。搜索摘要是发现入口不是证据。

输出标注：`[verified: T0/T1/T2]` / `[anonymous claim, unverified]`（匿名独家≠多源确认，置信度≤0.2）/ `[disputed: A说X vs B说Y]` / `[unverified]`。同链多域名（i24NEWS↔JP↔Ynet 等）只算 1 个确认点；标"事实"前必须先搜矛盾报道。

**信号维度计数**：禁止手动数信号方向，报告直接引用 `SignalBundle.format_dimension_table()` 程序化表格，insufficient_data 维度不计入多空对比。

**引擎输出引用**：必须陈述机制，禁止拟人化因果（引擎只有规则没有推理）；事件方向以写入时 `gold_bias` 为准；「⚠️ 方向冲突待复核」必须披露并说明采信理由。

完整协议（验证动作/工具路径/源链清单/i24NEWS 特殊规则/事故案例）见 [`docs/sourcing_protocol.md`](docs/sourcing_protocol.md)。

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
