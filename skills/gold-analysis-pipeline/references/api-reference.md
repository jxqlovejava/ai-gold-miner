# API 参考手册（按需加载）

> 本文件是 `SKILL.md` 的参考附件，**不随 skill 预载**。调任何 `gold_miner` API 前、CLI 降级时、
> 手动更新条件单/日历前必读本文件。**禁止凭记忆猜参数、签名、枚举值、文件路径。**

## API 签名速查

### CalendarEvent 构造

```python
from gold_miner.data.calendar import CalendarEvent, EventType, EventImpact
from datetime import datetime, timezone, timedelta

tz = timezone(timedelta(hours=-4))  # EDT

CalendarEvent(
    name="事件名称",                          # str - 必填
    event_type=EventType.GEO_POLITICAL,       # EventType 枚举 - 必填, 不是字符串
    scheduled_at=datetime(2026,7,21,8,0,0, tzinfo=tz),  # datetime 带时区 - 必填
    impact=EventImpact.HIGH,                  # EventImpact 枚举 - 必填, 不是字符串
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

# 添事件 - 参数是 CalendarEvent 对象, 不是 dict
cal.add_event(CalendarEvent(name="...", event_type=EventType.GEO_POLITICAL, ...))

# 更新事件结果 - scheduled_at 必须是 datetime, 不是 str
# ⚠️ gold_bias 必填且判定规则见 references/event-sync.md 的「gold_bias 写入铁律」
cal.update_event_result(
    name="事件名称",                              # str
    scheduled_at=datetime(2026,7,20,20,0,0, tzinfo=tz),  # datetime - NOT str
    actual="最新结果",                             # str
    forecast=None,                                # str | None
    previous=None,                                # str | None
    source_verified=None,                         # str | None
    gold_bias="bullish",                          # "bullish"|"bearish"|"neutral" - 必填
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

# 关闭 monitor - 仅对 status="active" 有效
cal.close_monitor(name="观测: ...", result="关闭原因", new_status="triggered")
# ⚠️ 前置确认：先 get_active_monitors() 核对 status=="active"；
#    已 triggered/expired 的事件 close_monitor 返回 False，此时用 update_event_result() 补记 actual 即可
```

> **`calendar_events.jsonl` 排除规则**：已从 `check_recent_results()` 排除的事件 -- 若 `actual` 字段非空则不会出现在结果中；若需更新已设 actual 的事件，直接用 `update_event_result()`。

### EarlyWarningEngine 方法

```python
from gold_miner.advisor.early_warning import EarlyWarningEngine

ewe = EarlyWarningEngine()

# 检查近期未记录结果的事件
ewe.check_recent_results(lookback_days=7)
# -> list[CalendarEvent]

# 检查活跃 monitor
ewe.get_active_monitors()
# -> list[CalendarEvent]

# 检查过时的 fast-evolving 事件
ewe.check_stale_events(lookback_days=7)
# -> list[CalendarEvent]

# 未来事件扫描
ewe.scan(days_ahead=14)
# -> AdvisorReport
```

> ⚠️ 铁律 9/10：走 `gold-miner scan` 主路径时**禁止**单独实例化这些类重跑网络工作（scan 已内置）。仅 CLI 降级场景使用。

### 金价获取 (不用 akshare - 缺依赖)

```python
# 积存金 - 已验证可用
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
f = JdAccumulationGoldFetcher(bank="MS")  # MS=民生, 默认
df = f.fetch(days=90)
price_info = f.fetch_price()  # -> JdGoldPrice | None - 单次最新价
# price_info.price, price_info.change_pct, price_info.timestamp

# XAUUSD 参考 - 用 anysearch 搜索实时价格
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

## CLI 总览

| 命令 | 功能 |
|------|------|
| `gold-miner prepare` | 仅步骤1：日历校验+事件同步+深度新闻+数据采集 |
| `gold-miner scan` | 完整9步管线（prepare->signals->truth->doctrine->munger->profile->debate->decide->plan）|
| `gold-miner advisor` | 投资顾问问答 |
| `gold-miner report` | 生成分析报告 |
| `gold-miner doctrine --check` | 独立军规审查 |
| `gold-miner scenario` | 情景推演 |

## CLI 降级：手动各维度命令模板

> 仅当 `gold-miner scan` 不可用时手动执行各维度。`python3` 不是 `python`（macOS）。

```bash
# 完整准备（等价 prepare 的手动降级）
PYTHONPATH=src python3 scripts/validate_calendar_dates.py --ref-table 30
PYTHONPATH=src python3 -m src.gold_miner.sentinel --mode deep-news-queries
# 然后对每个 P0 主题用 anysearch + last30days-cn（P0 列表见 references/event-sync.md）

#### 2.1 近期事件（时效性加权）
PYTHONPATH=src python3 -c "
from gold_miner.signals.recent_events import RecentEventSignalGenerator
for s in RecentEventSignalGenerator().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

#### 2.2 技术面 - 必做三项: TechnicalAnalyzer + K线形态 + 缠论结构
# 1) 常规技术指标
PYTHONPATH=src python3 -c "
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
from gold_miner.signals.technical import TechnicalAnalyzer
f = JdAccumulationGoldFetcher(bank='MS')
for s in TechnicalAnalyzer(f.fetch(days=90)).generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

# 2) 缠论结构（独立 ChanlunSignalGenerator，需 600 天长历史窗口）
#    ⚠️ 数据源坑: 缠论须 ≥600 自然日(≈400 根日线)才能形成笔/中枢。
#    积存金短窗口(如 120 天 ~32 根)只有 1 笔、0 中枢、无买卖点, 会误判「无结构」。
#    必须用 SpotGoldFetcher().fetch(days=600), 不能用 JdAccumulationGoldFetcher 短窗口。
PYTHONPATH=src python3 -c "
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.signals.chanlun_signal import ChanlunSignalGenerator
import json
hist = SpotGoldFetcher().fetch(days=600)
gen = ChanlunSignalGenerator(hist, symbol='Au99.99', name='黄金')
for s in gen.generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
print(json.dumps(gen.summary_dict(), ensure_ascii=False, indent=1))
"
# 缠论结果必须在报告技术面维度输出「🀄 缠论结构」子板块，无结构时写「（本期无触发）」。

#### 2.3 基本面
PYTHONPATH=src python3 -c "
from gold_miner.signals.fundamental import FundamentalAnalyzer
for s in FundamentalAnalyzer().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

#### 2.4 消息面 + 资金流 + 情绪面
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

#### 2.5b oil 信号
# 🔴 oil 维度必须用 OilSignalGenerator，禁止手工臆造（油价方向由代码判双通道）：
# - 油价单日/5日上行 -> bearish（加息预期渠道，油价上涨利空金）
# - 油价下跌 -> bullish（加息压力缓和）
# - 20日大涨 -> 滞胀观察 bullish（中期）
PYTHONPATH=src python3 -c "
from gold_miner.signals.oil_signal import OilSignalGenerator
for s in OilSignalGenerator().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f} | channel={s.metadata.get(\"channel\")}')
"

#### 2.5c jd 大V情绪 + 资金炸弹（免登录接口，pipeline scan 已接入）
PYTHONPATH=src python3 -c "
from gold_miner.signals.jd_blogger_sentiment_signal import JdBloggerSentimentSignalGenerator
from gold_miner.signals.jd_fund_bomb_signal import JdFundBombSignalGenerator
for g in [JdBloggerSentimentSignalGenerator(), JdFundBombSignalGenerator()]:
    for s in g.generate_signals():
        print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

#### 2.5d 反带节奏 HypeBias
PYTHONPATH=src python3 -c "
from gold_miner.signals.hype_bias_signal import HypeBiasSignalGenerator
for s in HypeBiasSignalGenerator().generate_signals():
    print(f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}')
"

#### 2.6 维度覆盖校验（每次手动构建信号后必须运行）
# 校验清单: technical / fundamental / news / sentiment / event / oil / smart_money /
#           hype_bias / jd_blogger / jd_fund_bomb。缺任何维度 -> 必须补采后再输出。
# oil 信号无标准 metadata / 预测数据标为事实 -> 报错。
PYTHONPATH=src python3 scripts/validate_signal_coverage.py
```

🔴 `Signal` 是 dataclass，字段：`s.name`、`s.direction` (枚举 `.value` 取 `"bullish"/"bearish"/"neutral"`)、`s.strength` (枚举 `.value` 取 `"strong"/"moderate"/"weak"`)、`s.score` (float)、`s.description` (str)。
🔴 打印模板：`f'[{s.direction.value}] {s.name} | strength={s.strength.value} | score={s.score:.2f}'`

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

## 突发新闻预警卡片格式（`news_monitor`）

`format_news_alerts()` 生成的微信推送卡片。**时间戳必带**：单条新闻显示发布时间、页脚显示生成时间（均北京时间）。

```
📰 突发新闻预警

当前 XAUUSD:  (🟢 +0.5%)

🚨 重大突发:
  • [能源危机 14:32] 就业数据疲软 + 霍尔木兹协议预期升温，金价创6月末以来新高
    💡 🟢[利多·重大] 霍尔木兹协议/缓和->油价↓->通胀↓->降息预期↑->利多金价

⚠️ 关注:
  • [地缘冲突 14:30] 开盘：美股周四开盘涨跌不一 ...
    💡 🟢[利多·重大] 美伊缓和->战争溢价回吐->油价↓->利多金价

📡 来源: 新浪黄金, 7×24快讯 | 🕐 08-06 22:06
```

格式规则（2026-08-06 起生效）：

1. **单条时间**：`[{类别} {HH:MM}]` - 优先用头条 `ts` 转北京时间；无 `ts` 时从原始 `time` 字段正则提取 `HH:MM`；两者皆无（东财等）则保持 `[类别]` 原样，不硬凑。
2. **页脚**：`📡 来源: {', '.join(sorted(sources))} | 🕐 {生成时间 %m-%d %H:%M}` - 来源是数据源标注（新浪黄金 / 7×24快讯 / 东方财富 / 金十数据），多源排序稳定。**禁止**输出旧的 `🕐 自动监控`（无时间）。
3. 来源可能为空时仍保留 `来源:` 前缀，属正常。

## 常见错误速查

| 错误 | 原因 | 修复 |
|------|------|------|
| `command not found: python` | macOS 只有 python3 | 用 `python3` |
| `ModuleNotFoundError: No module named 'akshare'` | akshare 未安装 | 用 `JdAccumulationGoldFetcher` 代替 `spot_gold` |
| `'str' object has no attribute 'value'` | 传了字符串而非枚举 | `EventType.GEO_POLITICAL` 不是 `"geo"` |
| `'dict' object has no attribute 'scheduled_at'` | 传了 dict 而非 CalendarEvent | 必须用 `CalendarEvent(...)` 构造 |
| `missing 1 required positional argument: 'actual'` | `update_event_result` 参数名不对 | 签名: `(name, scheduled_at, actual, forecast, previous, source_verified)` |
| `got an unexpected keyword argument: 'days_ahead'` | 参数名猜错 | `get_upcoming(days=14)` 不是 `days_ahead=` |
| `'str' object has no attribute 'tzinfo'` | scheduled_at 传了字符串 | 必须传 `datetime(2026,7,21,8,0,0, tzinfo=tz)` |
| `'RecentEventSignalGenerator' has no attribute 'generate'` | 方法名猜错 | `generate_signals()` 不是 `generate()` |
| `TechnicalAnalyzer.__init__() missing 1 required positional argument: 'df'` | 无参构造 | `TechnicalAnalyzer(df)` 必须传 DataFrame |
| `'FundamentalAnalyzer' has no attribute 'analyze'` | 方法名猜错 | `generate_signals()` 不是 `analyze()` |
| `'NewsSignalGenerator' has no attribute 'generate'` | 方法名猜错 | `fetch_and_analyze(hours=48)` 不是 `generate()` |
| `'SentimentAnalyzer' has no attribute 'analyze'` | 方法名猜错 | `generate_signals()` 不是 `analyze()` |
| `'Signal' object has no attribute 'label'` | 字段名猜错 | `s.name` 不是 `s.label`；`s.direction.value` 不是 `s.direction` |
| search 结果被 SEO 大新闻压制 | 搜索引擎偏差 | 对 fast-evolving 类型做逆转/修正专项搜索 |
| 分析报告中"周三初请失业金" | DOW 未校验 | 先跑 `validate_calendar_dates.py --ref-table 30` |
| `anysearch batch_search: unrecognized arguments` | 把 search 的顶层参数传给 batch_search | `max_results/freshness` 放 JSON query 对象内，见 event-sync.md 1.10 模板 |
| `close_monitor 返回 False` | 事件已非 active（已 triggered/expired） | 先 `get_active_monitors()` 确认 status；已 triggered 用 `update_event_result()` 补记 actual |
| `FileNotFoundError: data/personal_rules.md` | 猜错路径 | 静态文件统一在 `data/private/`，见 SKILL.md 1.0 路径表 |
