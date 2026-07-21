---
name: gold-analysis-pipeline
description: 黄金价格走势分析完整 pipeline — 第一步到第九步，含 API 签名、枚举值、命令模板、常见错误
---

# Gold Analysis Pipeline — 完整分析流程与 API 参考

## 触发

用户说「分析金价」「黄金走势」「gold」时触发。先执行第一步，再走第一步到第九步，最后输出完整报告。

## 输出铁律（优先级最高）

1. **逐步骤输出** — 每一步完成后立即在控制台输出完整结果，报告是逐步构建的，不是事后打开文件。
2. **禁止"写文件再概述"** — `Write` 文件只是持久化，不能替代控制台逐步骤展示。控制台必须展示完整内容。
3. **命令代码行不入报告** — `PYTHONPATH=src python3 ...` 这类命令只执行、不展示。报告中只出现命令的**结果**（表格、数据、校验结论）。
4. **错误模式**：静默执行 → Write 到文件 → 控制台给摘要 ❌
5. **正确模式**：执行命令 → 控制台输出该步结果 → 下一步 → ... → 最后 Write 归档 ✅

## 铁律

1. **调 API 前不猜参数** — 本 Skill 已提供所有签名，直接照抄
2. **用 `python3` 不是 `python`** — macOS 默认只有 python3
3. **日历对象必须用枚举** — `EventType.GEO_POLITICAL` 不是 `"geo"`，`EventImpact.HIGH` 不是 `"high"`
4. **scheduled_at 必须传 `datetime` 带时区** — `datetime(2026, 7, 21, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))`
5. **`calendar_events.jsonl` 已从 `check_recent_results()` 排除的事件** — 若 `actual` 字段非空则不会出现在 check_recent_results 结果中；若需更新已设 actual 的事件，直接用 `update_event_result()`
6. **深度新闻搜索 P0 主题必须全覆盖** — 见「1.3 深度新闻搜索」的命令模板

---

## API 签名速查

### CalendarEvent 构造

```python
from gold_miner.data.calendar import CalendarEvent, EventType, EventImpact
from datetime import datetime, timezone, timedelta

tz = timezone(timedelta(hours=-4))  # EDT

CalendarEvent(
    name="事件名称",                          # str — 必填
    event_type=EventType.GEO_POLITICAL,       # EventType 枚举 — 必填, 不是字符串
    scheduled_at=datetime(2026,7,21,8,0,0, tzinfo=tz),  # datetime 带时区 — 必填
    impact=EventImpact.HIGH,                  # EventImpact 枚举 — 必填, 不是字符串
    actual="实际结果",                         # str | None
    forecast="预期值",                         # str | None
    previous="前值",                           # str | None
    source="Reuters/Al Jazeera",              # str
    description="描述",                        # str
    # monitor 专用
    status="active",                          # "active" | "triggered" | "expired"
    trigger_condition="XAUUSD<4000 且 ...",   # 自然语言触发条件
    check_frequency="on_analysis",            # "on_analysis" | "daily" | "weekly"
    action_on_trigger="加仓5-10g",            # 触发后的建议动作
    expires_at="2026-08-04T00:00:00-04:00",  # ISO 格式字符串
)
```

### EventType 枚举 (`from gold_miner.data.calendar import EventType`)

| 值 | 用途 |
|----|------|
| `EventType.FED_RATE` | FOMC 利率决议 |
| `EventType.CPI` | 美国 CPI |
| `EventType.PPI` | 美国 PPI |
| `EventType.PCE` | 核心 PCE 物价指数 |
| `EventType.NFP` | 非农就业 |
| `EventType.PMI` | PMI / 消费者信心 / ZEW 等 |
| `EventType.FOMC_MINUTES` | FOMC 会议纪要 |
| `EventType.PMI_MARKIT` | Flash PMI (Markit) |
| `EventType.ECB` | ECB 利率决议 |
| `EventType.BOE` | BOE 利率决议 |
| `EventType.GEO_POLITICAL` | 地缘冲突 / 政策突变 / 贸易战 |
| `EventType.GOLD_RESERVE` | 央行黄金储备 |
| `EventType.FED_SPEECH` | 美联储官员讲话 |
| `EventType.MONITOR` | 持续性观测事件 |

### EventImpact 枚举 (`from gold_miner.data.calendar import EventImpact`)

| 值 | 用途 |
|----|------|
| `EventImpact.LOW` | 低影响 |
| `EventImpact.MEDIUM` | 中等影响 |
| `EventImpact.HIGH` | 高影响 |
| `EventImpact.EXTREME` | 极端影响 (战争/停火/重大政策) |

### EventCalendar 方法

```python
from gold_miner.data.calendar import EventCalendar

cal = EventCalendar()

# 添事件 — 参数是 CalendarEvent 对象, 不是 dict
cal.add_event(CalendarEvent(name="...", event_type=EventType.GEO_POLITICAL, ...))

# 更新事件结果 — scheduled_at 必须是 datetime, 不是 str
cal.update_event_result(
    name="事件名称",                              # str
    scheduled_at=datetime(2026,7,20,20,0,0, tzinfo=tz),  # datetime — NOT str
    actual="最新结果",                             # str
    forecast=None,                                # str | None
    previous=None,                                # str | None
    source_verified=None,                         # str | None
)

# 获取未来事件
cal.get_upcoming(days=14, min_impact=EventImpact.MEDIUM)
# 返回 list[CalendarEvent]

# 获取近期有结果的事件
cal.get_recent_events_with_results(lookback_days=7)
# 返回 list[CalendarEvent]

# 获取已发布但未记录结果的事件
cal.get_recently_published_without_result(lookback_days=7)
# 返回 list[CalendarEvent]

# 获取活跃 monitor
cal.get_active_monitors()
# 返回 list[CalendarEvent]

# 获取需要重新验证的 fast-evolving 事件
cal.get_events_needing_reverify(lookback_days=7)
# 返回 list[CalendarEvent]
```

### EarlyWarningEngine 方法

```python
from gold_miner.advisor.early_warning import EarlyWarningEngine

ewe = EarlyWarningEngine()

# 检查近期未记录结果的事件
ewe.check_recent_results(lookback_days=7)
# → list[CalendarEvent]

# 检查活跃 monitor
ewe.get_active_monitors()
# → list[CalendarEvent]

# 检查过时的 fast-evolving 事件
ewe.check_stale_events(lookback_days=7)
# → list[CalendarEvent]

# 未来事件扫描
ewe.scan(days_ahead=14)
# → AdvisorReport
```

### 金价获取 (不用 akshare — 缺依赖)

```python
# 积存金 — 已验证可用
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
f = JdAccumulationGoldFetcher(bank="MS")  # MS=民生, 默认
df = f.fetch(days=90)
price_info = f.fetch_price()  # → JdGoldPrice | None — 单次最新价
# price_info.price, price_info.change_pct, price_info.timestamp

# XAUUSD 参考 — 用 anysearch 搜索实时价格
# $4,056 as of 2026-07-21 (TradingView/LiteFinance)
```

### 持仓计算

```python
import yaml
with open("data/private/portfolio.yaml") as f:
    portfolio = yaml.safe_load(f)
pos = portfolio["positions"]["gold_jd"]
grams = pos["grams"]
avg_cost = pos["avg_cost"]
# 当前市价 = grams × current_price
# 浮亏 = (current_price - avg_cost) / avg_cost × 100
```

---

## 完整 Pipeline — CLI 调用

> 🆕 2026-07-22：主路径改为 `gold-miner` CLI。Skill 负责编排（步骤顺序、判断逻辑、图标规则），CLI 负责执行。

### 步骤 1：信息准备

```bash
# 完整准备（日历校验 + 事件同步 + 深度新闻 + 数据采集）
gold-miner prepare

# 等价的手动逐步骤（CLI 不可用时降级）
PYTHONPATH=src python3 scripts/validate_calendar_dates.py --ref-table 30
PYTHONPATH=src python3 -m src.gold_miner.sentinel --mode deep-news-queries
# 然后对每个 P0 主题用 anysearch + last30days-cn
```

### 步骤 2-9：完整扫描

```bash
# 一键运行全部 9 步
gold-miner scan --days 30 --news --sentiment
```

### 更新日历 / 添加 Monitor（手动）

> CLI 不可用时的降级路径：

```bash
PYTHONPATH=src python3 -c "
from gold_miner.data.calendar import EventCalendar, CalendarEvent, EventType, EventImpact
from datetime import datetime, timezone, timedelta

cal = EventCalendar()
tz = timezone(timedelta(hours=-4))

# 更新已有事件
cal.update_event_result(
    name='事件名称',
    scheduled_at=datetime(2026, 7, 20, 20, 0, 0, tzinfo=tz),
    actual='最新实际结果 [verified:T2 Reuters/source]'
)

# 添加新 monitor
cal.add_event(CalendarEvent(
    name='观测: 触发条件描述→预期结果',
    event_type=EventType.MONITOR, status='active',
    scheduled_at=datetime(2026, 7, 21, 10, 30, 0, tzinfo=tz),
    impact=EventImpact.HIGH,
    source='2026-07-22-analysis',
    trigger_condition='触发条件自然语言描述',
    check_frequency='on_analysis',
    action_on_trigger='触发后的建议动作',
    expires_at='2026-08-04T00:00:00-04:00'
))
"
```

### CLI 总览

| 命令 | 功能 |
|------|------|
| `gold-miner prepare` | 仅步骤1：日历校验+事件同步+深度新闻+数据采集 |
| `gold-miner scan` | 完整9步管线（prepare→signals→truth→doctrine→munger→profile→debate→decide→plan）|
| `gold-miner advisor` | 投资顾问问答 |
| `gold-miner report` | 生成分析报告 |
| `gold-miner doctrine --check` | 独立军规审查 |
| `gold-miner scenario` | 情景推演 |

---

## 条件单字段

读取 `data/private/conditional_orders.jsonl`，每行一个 JSON 对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一 ID，如 `co_20260716_002` |
| `status` | str | `"active"` / `"triggered"` / `"cancelled"` |
| `type` | str | `"limit_buy"` / `"oco"` |
| `trigger_price` | float | 限价单触发价 |
| `oco.take_profit.price` | float | OCO 止盈价 |
| `oco.stop_loss.price` | float | OCO 止损价 |
| `quantity_g` | float | 触发后交易克数 |
| `expires_at` | str | ISO 格式过期时间 |
| `note` | str | 备注 |

审查规则：只审查 `status="active"` 的条件单。逐条判断保留/撤销/修改。

---

## 分析输出模板

### Pipeline 步骤总览（新顺序）

| 步骤 | 内容 | 核心输出 |
|------|------|---------|
| 一 | 信息准备（日历同步+深度新闻） | 时效性加权表 + Monitor检查 + Staleness验证 + P0主题扫描 |
| 二 | 多维度信号采集 | 8维信号（技术面/基本面/消息面/资金流/情绪面等）|
| **三** | **Source Truth + 事实vs解释** | 来源验证表 + 事实/解释区分（置信度标注）|
| 四 | 军规自查 | r001-r030 逐条判定 |
| 五 | Munger 模型 | 2-3个思维模型 |
| 六 | 画像匹配 | 约束检查表 |
| **七** | **🐮Bull/🐻Bear/💼PM Agent 博弈** (综合前六步) | 三方辩论 + 资金流论据 + 军规阻断说明 |
| **八** | **交易建议 + 条件单调整** | 买/卖/观望结论 + 条件单审查（结论先行）|
| 九 | 后续事件关注 + 情景预案 + Monitor 创建 | 未来14天事件 + CPI/FOMC 情景推演 + 新增 Monitor 表 |

> **关键变更（2026-07-22）**：
> - Agent 博弈从第四步移至第七步 — 综合军规约束、Munger 思维模型、画像匹配作为输入，形成信息充分的对立辩论。
> - Agent 图标固定：🐮 BullAgent / 🐻 BearAgent / 💼 PortfolioManager，不可混用。
> - 第八步结论先行（买/卖/观望+条件单），第九步前瞻附录（什么会改变结论）。

### 第一步：信息准备 — 事件日历同步 + 深度新闻扫描

三张表：(1) 近期事件时效性加权 (2) Monitor 检查 (3) Staleness 重新验证

加权公式：Σ(方向得分 × 权重) / Σ权重。方向得分：看多=+1，看空=-1，中性=0。

| 事件距今 | 权重 |
|----------|------|
| <24h | 1.0 |
| 24-48h | 0.7 |
| 48-72h | 0.5 |
| 3-7d | 0.3 |
| >7d | 0 (不纳入) |

深度新闻扫描表：

| 主题 ID | 优先级 | anysearch | last30days-cn | 发现数 |
|---------|--------|-----------|---------------|--------|

P0 主题列表（必须全覆盖）：
- `geopolitical` — 美伊+中东全域
- `israel_houthi` — 以色列-胡塞-也门
- `ceasefire_diplomacy` — 停火谈判与外交
- `fed_policy` — 美联储政策

#### 1.5 日历写入铁律（日期 + 钟点，缺一不可）

1. **存储只认美东墙上钟点** `scheduled_at`（如 `2026-07-14T10:00:00-04:00`），禁止把北京小时数直接写入。
2. **写入前必须打印双列** `ET | 北京`（`dual_clock_str` / 事件 `.dual_clock_str`），两列都合理才落盘。
3. **三步校验**：DOW（美东星期）→ 官网/notice 原文钟点与时区 → 交叉确认（禁止只信搜索摘要）。
4. **国会/听证**：美东几乎总是上午（常见 10:00 ET）；若写成 ET 18:00+ 校验脚本会**直接报错**（典型双重换算形态）。
5. **BLS 数据（CPI/PPI/非农等）**：惯例 08:30 ET；写成晚间 ET 报错。
6. **代码写入**：优先 `make_et_iso(y,m,d,h,mi)`；`EventCalendar.add_event` 默认拒绝硬错误（`force=True` 仅历史回填）。

| 错误写法 | 正确写法 |
|----------|----------|
| 听说「北京晚上10点开」→ 存 `22:00-04:00` | 官网 10:00 ET → 存 `10:00-04:00` → 北京自动成 22:00 |
| 只展示北京时间做决策 | 决策引用必须 **ET + 北京** 同时出现 |

#### 1.6 重大事件判定 + 回写 + 演变追踪

**重大事件判定标准**（满足任一即为重大）：
- 对金价日内波动 > 2%
- 涉及主要产油国/霍尔木兹海峡/大国军事冲突
- 央行紧急政策声明/关税突变/资本管制
- 消息面 `SignalStrength == STRONG` 且 `_is_geopolitical() == True`

**重大事件回写 + 演变追踪**：
1. 追加到 `calendar_events.jsonl`（`event_type: "geo"`，`actual` 记录事件经过）
2. 检查是否需要无效化相关日历事件
3. **创建对应的 monitor 事件**跟踪后续演变：
   - `name`: `"观测: [事件名]后续演变"`
   - `trigger_condition`: 描述需要跟踪的演变方向（升级/缓和/外溢）
   - `check_frequency`: `"on_analysis"`
   - `action_on_trigger`: 演变对金价的影响评估框架
   - `expires_at`: 建议 30 天后，届时重新评估

#### 1.7 事件同步操作流程（8 步）

1. 调用 `EarlyWarningEngine().check_recent_results(lookback_days=7)` 查已发布但 `actual` 为空的事件
2. 对每个待查事件，按优先级搜索权威来源：
   - **T0 优先**：BLS.gov、FRED、CME FedWatch、FederalReserve.gov、BEA.gov
   - **T2 备用**：Reuters、Bloomberg、Kitco — 禁止仅依赖搜索摘要
3. 将实际结果写入 `calendar_events.jsonl`（`calendar.update_event_result()`）
4. 调用 `EarlyWarningEngine().get_active_monitors()` 查活跃 monitor
5. 逐一评估每个 active monitor 的 `trigger_condition` 是否满足
6. 对已触发 monitor：`calendar.close_monitor()` → 记录结果到日历
7. **检查 staleness**：`EarlyWarningEngine().check_stale_events(lookback_days=7)`
   对每个返回事件：
   a. 确认类型为持续演变型（geo/policy_shift/trade_war/fed_emergency）
   b. 时间约束搜索获取最新状态
   c. 主动搜索逆转/修正报道（reversal/backtrack/withdraw/cancel）
   d. ≥2 独立来源交叉确认
   e. 若最新状态与 `actual` 不同 → `update_event_result()` 更新（标注 `📝已更新`）
8. 输出三张表：事件日历同步表 + Staleness 重新验证表 + Monitor 检查表

#### 1.8 时效性衰减权重铁律

> 权重表见上方「事件距今 | 权重」。加权综合信号 = Σ(方向得分 × 权重) / Σ权重。方向得分：看多=+1，看空=-1，中性=0。

⚠️ **铁律**：
- 不执行事件日历同步 = 基于过时信息做决策
- 不执行 monitor 检查 = 丢失上次分析设定的跟进条件
- 消息面捕获的**重大**地缘/政策事件 → 回写日历 + 无效化相关事件 + 创建 monitor
- **最新事件（<24h）权重是 7 天前事件的 3.3 倍**

#### 1.9 深度新闻搜索执行铁律

**搜索主题覆盖原则**：
- 地缘冲突多极点 — 不能只搜美伊，必须覆盖以色列/胡塞/沙特/红海/曼德海峡
- 外交与军事对称 — 有冲突升级查询就必须有停火/调停查询
- 新参与方出现时同步更新搜索主题配置

**执行铁律**：
1. **禁止压缩查询** — 不允许将多条 anysearch query 合并，每条 P0 query 单独搜索
2. **P0 主题全覆盖** — `israel_houthi`、`ceasefire_diplomacy` 等不得跳过
3. **执行后输出完成清单**：

| 主题 ID | 优先级 | anysearch | last30days-cn | 发现数 |
|---------|--------|-----------|---------------|--------|
| geopolitical | P0 | ✅/❌ | ✅/❌ | N |

4. 清单中任何 ❌ 必须在进入第二步前补齐

### 第二步：8 维信号采集

> **主路径**：`gold-miner scan` 自动运行全部 8 维信号 + ScoringEngine 评分。
> 🔴 `Signal` 是 dataclass，字段：`s.name`、`s.direction` (枚举 `.value` 取 `"bullish"/"bearish"/"neutral"`)、`s.strength` (枚举 `.value` 取 `"strong"/"moderate"/"weak"`)、`s.score` (float)、`s.description` (str)。
> 🔴 打印模板：`f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}'`

```bash
# 一键信号采集（包含全部维度）
gold-miner scan --days 30 --news --sentiment
```

> 💡 以下是 CLI 降级路径（仅当 `gold-miner scan` 不可用时手动执行各维度）。API 签名速查见上方「API 签名速查」。

<details>
<summary>📋 CLI 降级：手动各维度命令模板</summary>

#### 2.1 近期事件（时效性加权）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.recent_events import RecentEventSignalGenerator
for s in RecentEventSignalGenerator().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"
```

#### 2.2 技术面

```bash
PYTHONPATH=src python3 -c "
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
from gold_miner.signals.technical import TechnicalAnalyzer
f = JdAccumulationGoldFetcher(bank='MS')
for s in TechnicalAnalyzer(f.fetch(days=90)).generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"
```

#### 2.3 基本面

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.fundamental import FundamentalAnalyzer
for s in FundamentalAnalyzer().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"
```

#### 2.4 消息面 + 资金流 + 情绪面

```bash
# 消息面
PYTHONPATH=src python3 -c "
from gold_miner.signals.news_signal import NewsSignalGenerator
for s in NewsSignalGenerator().fetch_and_analyze(hours=48):
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

# 资金流 (COT+ETF+机构)
PYTHONPATH=src python3 -c "
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator
for g in [CotSignalGenerator(), EtfFlowSignalGenerator(), InstitutionalSignalGenerator(current_spot=4065)]:
    try:
        for s in g.generate_signals():
            print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    except Exception as e:
        print(f'  {type(g).__name__} 失败: {e}')
"

# 情绪面
PYTHONPATH=src python3 -c "
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
f = JdAccumulationGoldFetcher(bank='MS')
for s in SentimentAnalyzer(au_df=f.fetch(days=90)).generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"
```

</details>

#### 2.7 资金流维度分析要点

- **CFTC COT**：管理基金净多仓趋势 + 商业套保净空变化 = 两股力量同向时信号最强。背离时（管理基金加仓但矿商加速套保）需警惕。
- **ETF 资金流**：GLD 日度持仓变化 > WGC 月度汇总。CPI/FOMC 等数据日脉冲流入但次日即刻逆转 = 假突破。
- **COMEX 大户集中度**：前4大多头/空头集中度 > 40% = 拥挤警告。逼空风险 = 强烈看多信号。
- **矿商套保 (Commercial Hedgers)**：矿商在上涨中加速做空 = 认为当前价格值得锁定。最重要反向指标之一。
- **三流一致警告**：当 ETF流出 + 管理基金减仓 + 矿商加速套保同时出现，即使消息面利多也应警惕顶部。

### 第三步：Source Truth Verification + 事实vs解释

输出「来源验证表」：

| 数据点 | 来源层级 | 验证状态 |
|--------|---------|---------|
| XAUUSD $4,065 | [T2] TradingView | verified 多源交叉 |

区分事实与解释：
- **事实**：价格、成交量、官方数据 — 不可争议
- **解释**：因果链、市场情绪 — 标注置信度 `"金价因X而涨" — 置信度：高/中/低 (XX%)`
### 第四步：军规自查 (r001-r030)
### 第五步：Munger 模型 (2-3个)
### 第六步：画像匹配
### 第七步：🐮Bull / 🐻Bear / 💼PM Agent 博弈 (综合前六步输入)
### 第八步：交易建议（买/卖/观望 + 操作建议 + 条件单调整）

> ⚠️ 步骤八是最终结论，内容必须是可执行指令。分析推演已在步骤一至七完成。

#### 8A. 最终交易建议：买入 / 卖出 / 观望（含触发价位、数量、置信度）

#### 8B. 条件单调整

读取 `data/private/conditional_orders.jsonl`，筛选 `status = "active"`，逐条判断保留/撤销/修改。

输出「条件单状态表」：

| 条件单 ID | 类型 | 方向 | 触发价 | 数量 | 状态 | 建议动作 | 原因 |
|-----------|------|------|--------|------|------|----------|------|
| co_xxx | oco | 卖出 | TP932/SL852 | 9g | active | **保留/撤销/修改** | 原因 |

撤销或修改必须在 JSONL 文件中更新对应记录。新建条件单必须追加。

### 第九步：后续事件关注 + CPI/FOMC 情景预案 + Monitor 创建（前瞻附录）

#### 9.1 后续事件关注

| 时间 | 事件 | 预期波动 | 对持仓影响 |
|------|------|---------|-----------|

#### 9.2 情景预案

| 情景 | 金价预期走向 | 建议动作 |
|------|-------------|---------|

#### 9.3 Monitor 创建

情景预案中的条件性场景立即创建 monitor 事件（`event_type: "monitor"`, `status: "active"`）。

输出「新增 Monitor 事件表」：

| Monitor 名称 | 触发条件 | 检查频率 | 触发后动作 | 过期时间 |
|-------------|----------|----------|-----------|----------|

情景表中已转为 monitor 的动作标注 `✅ 已加入 Monitor:<名称>`。

---

## 常见错误速查

| 错误 | 原因 | 修复 |
|------|------|------|
| `command not found: python` | macOS 只有 python3 | 用 `python3` |
| `ModuleNotFoundError: No module named 'akshare'` | akshare 未安装 | 用 `JdAccumulationGoldFetcher` 代替 `spot_gold` |
| `'str' object has no attribute 'value'` | 传了字符串而非枚举 | `EventType.GEO_POLITICAL` 不是 `"geo"` |
| `'dict' object has no attribute 'scheduled_at'` | 传了 dict 而非 CalendarEvent | 必须用 `CalendarEvent(...)` 构造 |
| `missing 1 required positional argument: 'actual'` | `update_event_result` 参数名不对 | 签名: `(name, scheduled_at, actual, forecast, previous, source_verified)` |
| `got an unexpected keyword argument 'days_ahead'` | 参数名猜错 | `get_upcoming(days=14)` 不是 `days_ahead=` |
| `'str' object has no attribute 'tzinfo'` | scheduled_at 传了字符串 | 必须传 `datetime(2026,7,21,8,0,0, tzinfo=tz)` |
| `'RecentEventSignalGenerator' has no attribute 'generate'` | 方法名猜错 | `generate_signals()` 不是 `generate()` |
| `TechnicalAnalyzer.__init__() missing 1 required positional argument: 'df'` | 无参构造 | `TechnicalAnalyzer(df)` 必须传 DataFrame |
| `'FundamentalAnalyzer' has no attribute 'analyze'` | 方法名猜错 | `generate_signals()` 不是 `analyze()` |
| `'NewsSignalGenerator' has no attribute 'generate'` | 方法名猜错 | `fetch_and_analyze(hours=48)` 不是 `generate()` |
| `'SentimentAnalyzer' has no attribute 'analyze'` | 方法名猜错 | `generate_signals()` 不是 `analyze()` |
| `'Signal' object has no attribute 'label'` | 字段名猜错 | `s.name` 不是 `s.label`；`s.direction.value` 不是 `s.direction` |
| search 结果被 SEO 大新闻压制 | 搜索引擎偏差 | 对 fast-evolving 类型做逆转/修正专项搜索 |
| 分析报告中"周三初请失业金" | DOW 未校验 | 先跑 `validate_calendar_dates.py --ref-table 30` |
