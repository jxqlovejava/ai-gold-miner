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

## 完整 Pipeline 命令模板

### 步骤 1.1 — 日历日期校验

```bash
PYTHONPATH=src python3 scripts/validate_calendar_dates.py --ref-table 30
```

### 步骤 1.2 — 事件同步 + Monitor 检查

```python
PYTHONPATH=src python3 -c "
from gold_miner.data.calendar import EventCalendar, EventType
from gold_miner.advisor.early_warning import EarlyWarningEngine

cal = EventCalendar()
ewe = EarlyWarningEngine(calendar=cal)

# 1. 近期未记录结果的事件
recent = ewe.check_recent_results(lookback_days=7)
for e in recent:
    print(f'{e.name} | {e.beijing_time_str} | actual=N/A')

# 2. 活跃 monitor
monitors = ewe.get_active_monitors()
for m in monitors:
    print(f'MONITOR: {m.name} | trigger={m.trigger_condition}')

# 3. Stale fast-evolving 事件
stale = ewe.check_stale_events(lookback_days=7)
for s in stale:
    print(f'STALE: {s.name} | last actual={s.actual[:80] if s.actual else \"N/A\"}')
"
```

### 步骤 1.3 — 深度新闻搜索

```bash
# 生成搜索计划
PYTHONPATH=src python3 -m src.gold_miner.sentinel --mode deep-news-queries

# 然后对每个 P0 主题用 anysearch batch_search + last30days-cn
# 用本 Skill 提供的命令模板, 覆盖所有 P0 主题
```

### 步骤 1.4 — 更新日历 + 添加 Monitor

```python
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
    source='2026-07-21-analysis',
    trigger_condition='触发条件自然语言描述',
    check_frequency='on_analysis',
    action_on_trigger='触发后的建议动作',
    expires_at='2026-08-04T00:00:00-04:00'
))
"
```

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

### 第二步：8 维信号采集 — 命令模板

> 🔴 `Signal` 是 dataclass，字段：`s.name`、`s.direction` (枚举 `.value` 取 `"bullish"/"bearish"/"neutral"`)、`s.strength` (枚举 `.value` 取 `"strong"/"moderate"/"weak"`)、`s.score` (float)、`s.description` (str)。
> 🔴 打印模板：`f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}'`

#### 2.1 近期事件（时效性加权）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.recent_events import RecentEventSignalGenerator
gen = RecentEventSignalGenerator()
signals = gen.generate_signals()
for s in signals:
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    if s.description:
        print(f'  {s.description[:250]}')
"
```

#### 2.2 技术面（需要价格 DataFrame）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
from gold_miner.signals.technical import TechnicalAnalyzer

f = JdAccumulationGoldFetcher(bank='MS')
df = f.fetch(days=90)
ta = TechnicalAnalyzer(df)               # df 必传，不是无参构造
signals = ta.generate_signals()
for s in signals:
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    if s.description:
        print(f'  {str(s.description)[:300]}')
"
```

#### 2.3 基本面（无参构造，调用 `generate_signals()` 不是 `analyze()`）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.fundamental import FundamentalAnalyzer
fa = FundamentalAnalyzer()              # 无参构造
signals = fa.generate_signals()         # 不是 analyze()
for s in signals:
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    if s.description:
        print(f'  {s.description[:300]}')
"
```

#### 2.4 消息面（`fetch_and_analyze()` 不是 `generate()`）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.news_signal import NewsSignalGenerator
gen = NewsSignalGenerator()
signals = gen.fetch_and_analyze(hours=48)  # 不是 generate()
for s in signals:
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    if s.description:
        print(f'  {s.description[:250]}')
"
```

#### 2.5 👔 资金流（COT + ETF + 机构，统一模板）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator

print('--- CFTC COT ---')
try:
    for s in CotSignalGenerator().generate_signals():
        print(f'  [{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
except Exception as e:
    print(f'  COT失败: {e}')

print('--- ETF 资金流 ---')
try:
    for s in EtfFlowSignalGenerator().generate_signals():
        print(f'  [{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
except Exception as e:
    print(f'  ETF失败: {e}')

print('--- COMEX大户 + 13F 机构 ---')
try:
    inst = InstitutionalSignalGenerator(current_spot=4065)
    for s in inst.generate_signals():
        print(f'  [{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
except Exception as e:
    print(f'  机构持仓失败: {e}')
"
```

#### 2.6 情绪面（需要价格 DataFrame）

```bash
PYTHONPATH=src python3 -c "
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
from gold_miner.signals.sentiment_signal import SentimentAnalyzer

f = JdAccumulationGoldFetcher(bank='MS')
df = f.fetch(days=90)
sa = SentimentAnalyzer(au_df=df)        # df 必传
signals = sa.generate_signals()
for s in signals:
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
    if s.description:
        print(f'  {str(s.description)[:300]}')
"
```

### 第三步：Source Truth Verification + 事实vs解释
### 第四步：军规自查 (r001-r030)
### 第五步：Munger 模型 (2-3个)
### 第六步：画像匹配
### 第七步：🐮Bull / 🐻Bear / 💼PM Agent 博弈 (综合前六步输入)
### 第八步：交易建议（买/卖/观望 + 操作建议 + 条件单调整）
### 第九步：后续事件关注 + CPI/FOMC 情景预案 + Monitor 创建（前瞻附录）

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
