---
name: gold-analysis-pipeline
description: 黄金价格走势分析完整 pipeline - 启动批协议+输出铁律；API 签名/枚举/降级模板在 references/ 按需读取
---

# Gold Analysis Pipeline - 主流程协议

## 触发

用户说「分析金价」「黄金走势」「gold」时触发。按「1.0 启动批协议」执行，走完第一步到第九步，最后输出完整报告。

## 📚 按需参考文件（不预载，节省上下文）

| 文件 | 内容 | 何时必读 |
|---|---|---|
| `references/api-reference.md` | API 签名/EventType/EventImpact 枚举/EventCalendar/EarlyWarningEngine 方法/金价获取/持仓计算/CLI 降级手动模板/条件单字段/突发新闻卡片格式/常见错误速查 | 调任何 `gold_miner` API 前、CLI 降级时、手动读写条件单前 |
| `references/event-sync.md` | 事件同步 8 步流程、日历写入铁律(1.5-1.10)、gold_bias 写入铁律、深度新闻搜索铁律、时效性权重表、Monitor 添加模板 | 手动同步事件结果/更新日历/添加 Monitor、日历复用失败需定向搜索时 |

🔴 **调 API 前必读对应参考文件，禁止凭记忆猜参数、签名、枚举值、文件路径。**

## 输出铁律（优先级最高）

1. **逐步骤输出** - 每一步完成后立即在控制台输出完整结果，报告是逐步构建的，不是事后打开文件。
2. **禁止"写文件再概述"** - `Write` 文件只是持久化，不能替代控制台逐步骤展示。控制台必须展示完整内容。
3. **命令代码行不入报告** - `PYTHONPATH=src python3 ...` 这类命令只执行、不展示。报告中只出现命令的**结果**（表格、数据、校验结论）。
4. **错误模式**：静默执行 -> Write 到文件 -> 控制台给摘要 ❌
5. **正确模式**：执行命令 -> 控制台输出该步结果 -> 下一步 -> ... -> 最后 Write 归档 ✅
6. **固定格式** - 每次报告必须遵循 `docs/report_template.md` 的板块顺序与空态规则（每块必须出标题，无数据写「（本期无触发）」），不自行增删板块。
7. **本地 HTML** - 分析输出完后运行 `python3 scripts/render_report_html.py`，在终端末尾附本地 HTML 地址 `file://{绝对路径}`，复制即可浏览器打开。
8. **目标区间预测** - 综合分析结论（决策摘要后）必须输出「金价目标区间预测」--分情景（看多/震荡/回调）给出积存金目标区间（元/g）+ 概率 + 触发条件，基于当前价推导（心理关口/支撑位/ATR）。格式见 `docs/report_template.md` §1b。
   - **传导链强制检查（r035，2026-08-14 起）**：地缘/油价驱动情景（触发条件含停火/战争/封锁/霍尔木兹/油价/美伊等）必须**同时评估直接传导与二阶传导**--单写「避险冲高」不写「油价->通胀->联储鹰派->实际利率↑->压制金价（或美元走强）」= 单层传导 = 决策失真。
   - **时间尺度分化**：标注短期/中期方向（如「先冲后落」= 短期脉冲 + 中期回落），防止把短期脉冲当可持续目标。
9. **主驱动因素板块（2026-08-21 起）**：每次分析必须在决策摘要前输出「🔍 主驱动因素」--一句话第一性主驱动 + 驱动排序表（性质/传导链/可预见性）。判定方法：与价格拐点同日发生+传导链最短+有量价佐证的事件。当一阶与二阶传导方向相反时，**短期(1-2周)方向判定以一阶为准**，二阶只作催化剂日期前的风险标注（2026-08-20 清仓教训：用二阶加息风险否决一阶利率下行）。详见 docs/report_template.md §1a/§1a-2。
   - **披露 transmission_warnings**：若 pipeline `scenario_plan["transmission_warnings"]` 非空，报告必须逐条披露，不得省略。
10. **报告骨架程序化组装 + 落盘校验再输出（2026-08-22 P2/P3，内化到程序，不靠记忆）**：
    - **组装已内嵌 `quick_scan.sh`**（P3/P5，2026-08-22）：scan/复用完成后自动跑 `assemble_report.py` 生成骨架到 `data/output/金价分析_YYYY-MM-DD.md`（提取 scan 的决策/维度表/军规/Munger/画像/博弈/后续关注/经验提醒 + portfolio 持仓 + active 条件单），LLM **只增量填充 3 个推理板块**：主驱动因素(§1.1)、目标区间(§1.2)、条件单审查(§7)。**脚本返回时骨架已就绪（bundle 内），禁止再手动跑 assemble_report.py**（当日已填充报告会输出 `ASSEMBLE_SKIP` 不覆盖；强制重建用 `ASSEMBLE_FORCE=1 bash scripts/quick_scan.sh`）。
    - 🛡️ **Edit 回填防 dash 陷阱**：骨架占位符已全 ASCII/「无」（`止盈: 无`，无 em-dash「-」）。Edit old_string 必须**从 Read 输出逐字复制**，禁止手打 dash 类近形字（-/–/-/- 视觉相同但编码不同，匹配必失败）；连续 2 次 Edit 失败即改用 python 行号定位替换，不要反复试错（2026-08-22 事故：`Error editing file` 排障 1 轮）。
    - **补最新价**：REUSE 场景 quick_scan 返回 LATEST_PRICE，骨架行情引文用其覆盖 scan 价格（assemble_report.py 用 scan 报告内价格）。
    - **落盘**：骨架填充后用 `Write` 写入 `data/output/金价分析_YYYY-MM-DD.md`--PostToolUse hook（`.claude/settings.json`，matcher=Write）自动运行 `scripts/validate_report_format.py` 校验「板块间禁止独立 `---` 分隔线」（表格 `|---|` / frontmatter 除外）。校验失败（exit 2）hook 拦截并给出违规行号，删除 `---` 后重新 Write 直到通过，再输出终端。
    - **禁止绕过**：不手写整份报告（assemble_report.py 已生成 90% 程序化板块）；不绕过落盘校验直接手写终端文本--绕过=失去程序校验=靠记忆约束，即 2026-08-22 复发根因。手动复跑：`python3 scripts/validate_report_format.py --file data/output/金价分析_YYYY-MM-DD.md`。

## 铁律

1. **调 API 前不猜参数** - 先读 `references/api-reference.md`，照抄签名
2. **用 `python3` 不是 `python`** - macOS 默认只有 python3
3. **日历对象必须用枚举** - `EventType.GEO_POLITICAL` 不是 `"geo"`，`EventImpact.HIGH` 不是 `"high"`
4. **scheduled_at 必须传 `datetime` 带时区** - `datetime(2026, 7, 21, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))`
5. **手动事件同步必读 `references/event-sync.md`** - 含日历写入铁律(1.5-1.10)、gold_bias 判定规则、8 步同步流程、`calendar_events.jsonl` 排除规则
6. **深度新闻搜索 P0 主题必须全覆盖** - P0 列表与执行铁律见 `references/event-sync.md` §1.9
7. **单轮取数协议（2026-08-22 P5，⏱ 核心）** - `bash scripts/quick_scan.sh` **前台**一条 Bash 调用拿齐全部数据：复用判断->补最新价->重 scan->组装骨架+摘要->**末尾 bundle 输出**（`=====BUNDLE_START=====` 内：骨架/摘要/portfolio/active 条件单；**P6：ASSEMBLE_SKIP 时轻量 bundle 仅骨架**，已填充报告已含其余结论，省 ~170 行 ≈4-5k token）。**全程 2 轮模型调用**：①前台跑 quick_scan.sh（P6 后 REUSE+SKIP ~1s / RERUN ~15-30s，纯工具时间）-> ②推理填充 3 板块 + Write 落盘（hook 校验通过）+ 终端完整输出。仅 Write 校验失败才有轮③修复。
   - ✅ **quick_scan.sh 自动分流**：当日报告 <3h -> 秒级返回 `REUSE_MODE|路径|AGE|LATEST_PRICE`；缺失/≥3h -> 前台重 scan。两种模式末尾都输出 bundle，骨架/摘要组装自动完成（`ASSEMBLE_OK`/`ASSEMBLE_SKIP`）。
   - 🕐 **强制重 scan**：价格剧变（bundle 价格 vs LATEST_PRICE 跳变 >1%）或用户明确要最新 -> `FORCE_SCAN=1 bash scripts/quick_scan.sh`（仍单轮拿数）。
   - 🚫 **禁止后台跑 + 通知驱动**（P3/P4 旧模式，3 轮推理各 20-25s；前台单轮省 1 轮推理 ~25s > RERUN 前台工具时间，REUSE 场景更优）。
   - 🚫 **禁止 Read scan_report 全文**（420 行 ≈12k token/轮；bundle 内骨架+摘要已覆盖全部所需）。
   - 🧠 **思考深度分配**：轮①发命令用最短思考；仅轮②填充主驱动/目标区间/条件单审查时正常推理。每轮推理时间 ∝ 上下文 token 量（实测：旧 3 轮推理占全程 ~92%）。
   - 事故史：多轮串行预读（多耗 ~2min）/ tail 后台输出 / 显式 TaskOutput 阻塞 / 4 轮排障探测文件进度 -- 均已随单轮协议消除。
8. **重大事件先查日历复用（2026-08-21 起）** - 消息面捕获重大事件标 `[unverified]` 时，**先本地查 `data/calendar_events.jsonl`**（grep 事件名/主题词），若已有带多源验证的 actual 记录（如 `[verified: T2 多源]`），直接复用、跳过外部搜索；仅当日历缺失/过时才走定向搜索（模板见 `references/event-sync.md` §1.10）。
9. **scan 完成后零深挖（2026-08-22 起，⏱ 提速核心）** - `gold-miner scan` 报告（`data/output/scan_report_YYYYMMDD.md`）是**唯一数据源**（LLM 通过骨架+摘要双文件消费其内容，见铁律 7，不读原文），已包含：全部 9 步结果、8 维信号表、军规自查（r001-r035）、Agent 博弈、Munger、画像匹配、三情景骨架（含 r035 传导链校验 ✅）。**scan 完成后禁止任何深挖**：
   - ❌ 不单独跑 `EarlyWarningEngine().check_recent_results()` / `get_active_monitors()` / `check_stale_events()` - scan 日志已输出「未记录:N | 活跃Monitor:N | Stale:N」，细节直接用 scan 报告或本地 grep `data/calendar_events.jsonl` 即可
   - ❌ 不追源码找 `scenario_plan` / `transmission_warnings` / `build_price_target_matrix` 等内部实现 - scan 报告的三情景骨架已足够填充报告 §1b 与 r035 披露，深挖 = 自我驱动的完美主义
   - ❌ 不猜 CLI 参数重跑子命令（`scenario --text` 等）- 报告缺什么就用手头数据补，不重启网络任务
   - 事故A（深挖）：2026-08-22 ~2.5min 浪费在深挖；事故B（编排）：~2min 浪费在 6 轮串行调用 + 废 tail + 显式阻塞（见铁律 7）
10. **信任 scan 内置事件同步（2026-08-22 起）** - scan 已执行事件同步 + 深度新闻 + 日历校验。除铁律 8 的日历复用（本地 grep）外，**不再单独实例化数据采集类**（`EarlyWarningEngine`、`JdAccumulationGoldFetcher`、`NewsSignalGenerator` 等）去重跑 scan 已做过的网络工作。需要补充的信号细节，从 scan 报告与本地 JSONL/CSV 读取。

## 摩擦成本铁律（r032，卖出决策必查）

民生银行积存金**卖出收 0.4% 手续费**（唯一数字真相源：`data/private/portfolio.yaml` 的 `sell_fee_pct`）。卖到成本价 ≠ 保本，是实亏一笔手续费。所有卖出/止盈/条件单建议必须按**净口径**计算：

| 口径 | 公式 | 示例（成本 894.25） |
|------|------|----------------------|
| 净保本价 | `avg_cost ÷ (1 − sell_fee_pct)` | 894.25 ÷ 0.996 ≈ **897.84** |
| 净盈亏（元） | `(price × (1 − fee) − avg_cost) × grams` | 价格 900 时 ≈ +102 元（毛 +274 元） |
| 净收益率 | `price × (1 − fee) ÷ avg_cost − 1` | 价格 900 时 +0.24%（毛 +0.64%） |

**输出要求**：
1. 第八步交易建议与条件单审查表中，所有 TP/SL/目标价必须同时给出**扣费后净收益**，不得只给毛收益；
2. 「浮盈转正」「回本」的判定线是**净保本价**，不是成本价；
3. 情景推演/收益矩阵的浮盈列需注明毛口径，并给出扣费后净值；
4. 代码侧已费率感知：`Position.breakeven_price` / `Position.net_pnl()`（`src/gold_miner/agent/portfolio.py`）、`ATRTrailingStop(sell_fee_pct=0.004)` 的浮盈轨保本地板、监控卡片净盈亏行--分析引用这些值即可，不要手算另搞一套。**当前成本以 portfolio.yaml 实时计算为准。**

## 1.0 启动批协议（唯一执行路径）

**启动批 = 一条前台 Bash**：`bash scripts/quick_scan.sh`（五合一：复用判断->补最新价->重 scan->组装骨架+摘要->bundle 输出全部数据）。stdout 即全部输入：模式行（`REUSE_MODE|RERUN_MODE` + `LATEST_PRICE` + `ASSEMBLE_*`）+ bundle（`=====BUNDLE_START=====` 内：骨架/摘要/portfolio/active 条件单；SKIP 时仅骨架）。**不要后台运行**（后台 = 通知驱动 = 多一轮推理，见铁律 7）。

**轮②（唯一推理轮）**：bundle 数据已在上下文 -> 推理填充 3 个板块（主驱动 §1.1 / 目标区间 §1.2 / 条件单审查 §7）-> `Write` 落盘（hook 校验通过）-> 终端完整输出报告。REUSE 场景用 LATEST_PRICE 覆盖骨架内 scan 价格。

- 🚫 禁止 Read `scan_report_YYYYMMDD.md` 全文（420 行 ≈12k token；bundle 内骨架+摘要已覆盖全部所需）。
- 画像 / personal_rules / V9 / doctrine / report_template **不读**（骨架已含结论，定性内容在 memory 缓存）。
- 需要事件细节时本地 grep `data/calendar_events.jsonl`（加 `| tail -8` 限行，绝不与条件单 grep 合并）。
- 强制重 scan：`FORCE_SCAN=1 bash scripts/quick_scan.sh`（价格剧变/用户要最新）。**目标全程 ≤60s（REUSE ≤40s）**。

## Pipeline 步骤总览

| 步骤 | 内容 | 核心输出 |
|------|------|---------|
| 一 | 信息准备（日历同步+深度新闻） | 时效性加权表 + Monitor检查 + Staleness验证 + P0扫描 |
| 二 | 多维度信号采集（8维） | 技术面/基本面/消息面/资金流/情绪面信号 |
| **三** | **Source Truth + 事实vs解释** | 来源验证表 + 事实/解释区分（置信度标注）|
| 四 | 军规自查 | r001-r035 逐条判定 |
| 五 | Munger 模型 | 2-3个思维模型 |
| 六 | 画像匹配 | 约束检查表 |
| **七** | **🐮Bull/🐻Bear/💼PM Agent博弈**（综合前六步） | 三方辩论 + 资金流论据 + 军规阻断说明 |
| **八** | **交易建议 + 条件单调整** | 买/卖/观望结论 + 条件单审查（结论先行）|
| 九 | 后续事件 + 情景预案 + Monitor | 未来14天事件 + 情景推演 + Monitor创建 |

## 各步骤要求（输出塑形）

**第一/二步（信息准备+信号采集）**：`gold-miner scan` 自动完成（含日历校验、事件同步、深度新闻、8 维信号、ScoringEngine 评分）。手动降级路径见 `references/api-reference.md`。第二步必含：技术面 = TechnicalAnalyzer + K线形态 + 缠论结构 三项；🀄 缠论结构子板块（无结构写「（本期无触发）」）；oil 维度必须用 `OilSignalGenerator`（禁止手工臆造方向，油价上行->bearish 加息渠道）。

**2.7 资金流维度分析要点**（解读 scan 聪明钱信号时应用）：
- **CFTC COT**：管理基金净多仓趋势 + 商业套保净空变化 = 两股力量同向时信号最强。背离时（管理基金加仓但矿商加速套保）需警惕。
- **ETF 资金流**：GLD 日度持仓变化 > WGC 月度汇总。CPI/FOMC 等数据日脉冲流入但次日即刻逆转 = 假突破。
- **COMEX 大户集中度**：前4大多头/空头集中度 > 40% = 拥挤警告。逼空风险 = 强烈看多信号。
- **矿商套保 (Commercial Hedgers)**：矿商在上涨中加速做空 = 认为当前价格值得锁定。最重要反向指标之一。
- **三流一致警告**：当 ETF流出 + 管理基金减仓 + 矿商加速套保同时出现，即使消息面利多也应警惕顶部。

**第三步**：来源验证表（数据点/来源层级/验证状态），事实（价格/成交量/官方数据）与解释（因果链/情绪，标置信度%）区分。

**第七步 Agent 博弈**：必须在军规/Munger/画像之后，综合前三者作为输入。图标固定：🐮 BullAgent / 🐻 BearAgent / 💼 PortfolioManager，不可混用。

**第八步（结论先行，可执行指令）**：
- 8A. 最终交易建议：买入/卖出/观望（含触发价位、数量、置信度）
- 8B. 条件单调整：读 `data/private/conditional_orders.jsonl` 筛 active，逐条输出「条件单状态表」（ID/类型/方向/触发价/数量/状态/建议动作/原因）。撤销或修改必须在 JSONL 中更新，新建条件单必须追加。字段定义见 `references/api-reference.md`。
- 🆕 **r034 主动止盈评估（2026-08-14）**：PM 决策不得只在「加仓/持有」间选择。当**同时满足** 近48h重大数据温和落地 + 高位震荡(距20日高<3%) + 聪明钱流出 + 已有浮盈 -> **必须显式评估「机动池主动部分止盈 ≥1/3 / 核心池最多减 1/4」**；动作=持有须在理由中说明为何不做部分止盈。
- 🔴 **ATR 减半线必须预挂卖出条件单（r017 强执）**：凡报告给出 ATR 移动止盈位，必须在 8B 表中预挂对应卖出条件单（含触发价、数量），禁止「监测 + 手动执行」。若平台无法挂 trailing 则挂固定价 limit_sell，并在 9.3 Monitor 标注复查。**例外**：小克数机动仓（<10g）按经验提醒走 monitor 微信提醒 + 人工决定，不挂自动卖出单。

**第九步（前瞻附录）**：
- 9.1 后续事件关注表（时间/事件/预期波动/对持仓影响）
- 9.2 情景预案表（情景/金价预期走向/建议动作）
- 9.3 Monitor 创建：情景表中条件性场景立即创建 monitor 事件（`event_type: "monitor"`, `status: "active"`，添加模板见 `references/event-sync.md`），输出「新增 Monitor 事件表」（名称/触发条件/检查频率/触发后动作/过期时间）。已转 monitor 的动作标注 `✅ 已加入 Monitor:<名称>`。

## 一键命令

```bash
# 完整 9 步管线（scan 报告即最终数据源，配合铁律 9/10 零深挖）
gold-miner scan --days 30 --news --sentiment

# 仅步骤1：日历校验+事件同步+深度新闻+数据采集
gold-miner prepare
```

> 其余 CLI（advisor/report/doctrine --check/scenario）与手动降级模板见 `references/api-reference.md`。
