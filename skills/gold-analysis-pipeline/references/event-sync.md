# 事件同步与深度新闻铁律（按需加载）

> 本文件是 `SKILL.md` 的参考附件，**不随 skill 预载**。手动同步事件结果、写入/更新日历、
> 添加 Monitor、日历复用失败需定向搜索时**必读**。走 `gold-miner scan` 主路径时事件同步已内置
> （铁律 9/10），不需要本文件。

## 第一步三张表（手动同步场景的输出格式）

(1) 近期事件时效性加权 (2) Monitor 检查 (3) Staleness 重新验证

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
- `geopolitical` - 美伊+中东全域
- `israel_houthi` - 以色列-胡塞-也门
- `ceasefire_diplomacy` - 停火谈判与外交
- `fed_policy` - 美联储政策

## gold_bias 写入铁律（2026-08-10 起，update_event_result 必守）

同步 actual 时必须显式判断并写入 `gold_bias`（对**金价**的方向，非经济好坏）。引擎优先读此字段，关键词匹配仅作 fallback；两者冲突会生成「⚠️ 方向冲突待复核」告警信号。
- **组合语义**由写入者判定：失业率↓+参与率↓=疲弱（分母幻觉），不是强劲
- **反向指标**由写入者判定：初请/续请"低于预期"=劳动力强=偏鹰=`bearish`（申请人数方向与经济强弱相反）
- 事故案例见 `.learnings/2026-08-10-unemployment-denominator-illusion.md`

## 1.5 日历写入铁律（日期 + 钟点，缺一不可）

1. **存储只认美东墙上钟点** `scheduled_at`（如 `2026-07-14T10:00:00-04:00`），禁止把北京小时数直接写入。
2. **写入前必须打印双列** `ET | 北京`（`dual_clock_str` / 事件 `.dual_clock_str`），两列都合理才落盘。
3. **三步校验**：DOW（美东星期）-> 官网/notice 原文钟点与时区 -> 交叉确认（禁止只信搜索摘要）。
4. **国会/听证**：美东几乎总是上午（常见 10:00 ET）；若写成 ET 18:00+ 校验脚本会**直接报错**（典型双重换算形态）。
5. **BLS 数据（CPI/PPI/非农等）**：惯例 08:30 ET；写成晚间 ET 报错。
6. **代码写入**：优先 `make_et_iso(y,m,d,h,mi)`；`EventCalendar.add_event` 默认拒绝硬错误（`force=True` 仅历史回填）。

| 错误写法 | 正确写法 |
|----------|----------|
| 听说「北京晚上10点开」-> 存 `22:00-04:00` | 官网 10:00 ET -> 存 `10:00-04:00` -> 北京自动成 22:00 |
| 只展示北京时间做决策 | 决策引用必须 **ET + 北京** 同时出现 |

## 1.6 重大事件判定 + 回写 + 演变追踪

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

## 1.7 事件同步操作流程（8 步）

1. 调用 `EarlyWarningEngine().check_recent_results(lookback_days=7)` 查已发布但 `actual` 为空的事件
2. 对每个待查事件，按优先级搜索权威来源：
   - **T0 优先**：BLS.gov、FRED、CME FedWatch、FederalReserve.gov、BEA.gov
   - **T2 备用**：Reuters、Bloomberg、Kitco - 禁止仅依赖搜索摘要
3. 将实际结果写入 `calendar_events.jsonl`（`calendar.update_event_result()`，**必须同时写入 `gold_bias`**，判定规则见上方铁律）
4. 调用 `EarlyWarningEngine().get_active_monitors()` 查活跃 monitor
5. 逐一评估每个 active monitor 的 `trigger_condition` 是否满足
6. 对已触发 monitor：`calendar.close_monitor()` -> 记录结果到日历
7. **检查 staleness**：`EarlyWarningEngine().check_stale_events(lookback_days=7)`
   对每个返回事件：
   a. 确认类型为持续演变型（geo/policy_shift/trade_war/fed_emergency）
   b. 时间约束搜索获取最新状态
   c. 主动搜索逆转/修正报道（reversal/backtrack/withdraw/cancel）
   d. ≥2 独立来源交叉确认
   e. 若最新状态与 `actual` 不同 -> `update_event_result()` 更新（标注 `📝已更新`）
8. 输出三张表：事件日历同步表 + Staleness 重新验证表 + Monitor 检查表

## 1.8 时效性衰减权重铁律

> 权重表见上方。加权综合信号 = Σ(方向得分 × 权重) / Σ权重。方向得分：看多=+1，看空=-1，中性=0。

⚠️ **铁律**：
- 不执行事件日历同步 = 基于过时信息做决策
- 不执行 monitor 检查 = 丢失上次分析设定的跟进条件
- 消息面捕获的**重大**地缘/政策事件 -> 回写日历 + 无效化相关事件 + 创建 monitor
- **最新事件（<24h）权重是 7 天前事件的 3.3 倍**

## 1.9 深度新闻搜索执行铁律

**搜索主题覆盖原则**：
- 地缘冲突多极点 - 不能只搜美伊，必须覆盖以色列/胡塞/沙特/红海/曼德海峡
- 外交与军事对称 - 有冲突升级查询就必须有停火/调停查询
- 新参与方出现时同步更新搜索主题配置

**执行铁律**：
1. **禁止压缩查询** - 不允许将多条 anysearch query 合并，每条 P0 query 单独搜索
2. **P0 主题全覆盖** - `israel_houthi`、`ceasefire_diplomacy` 等不得跳过
3. **执行后输出完成清单**：

| 主题 ID | 优先级 | anysearch | last30days-cn | 发现数 |
|---------|--------|-----------|---------------|--------|
| geopolitical | P0 | ✅/❌ | ✅/❌ | N |

4. 清单中任何 ❌ 必须在进入第二步前补齐

## 1.10 事件定向验证标准模板（2026-08-21 起）

**触发前提**：仅在「日历复用失败」（SKILL.md 铁律 8）时才走此步骤。

**anysearch batch_search 完整模板**--参数必须放 JSON query 对象内，**禁止** `--max_results`/`--freshness` 放顶层（会报 `unrecognized arguments`）：

```bash
python3 ~/.claude/skills/anysearch/scripts/anysearch_cli.py batch_search --queries '[
  {"query":"<事件关键词> 2026-08","max_results":6,"freshness":"week"},
  {"query":"<中文关键词> 最新 2026年8月","max_results":6,"freshness":"week"},
  {"query":"<事件名> reversal OR backtrack OR withdraw OR update","max_results":5,"freshness":"week"}
]'
```

规则：
- `max_results`/`freshness` 必须写在 JSON query 对象内；`--query` 重复写字符串只放查询文本
- 查询须带时间约束（`2026-08` 或具体日期），不搜无日期查询（快速演变事件铁律）
- 逆转/修正查询必须与正向查询同时发（reversal/backtrack/update），防止被 SEO 大新闻压制
- 接口稳定时无需重复 `doc`；报错再查 `batch_search --help`

## 添加 Monitor 的命令模板

```bash
# CLI 不可用时的降级路径：
PYTHONPATH=src python3 -c "
from gold_miner.data.calendar import EventCalendar, CalendarEvent, EventType, EventImpact
from datetime import datetime, timezone, timedelta

cal = EventCalendar()
tz = timezone(timedelta(hours=-4))

# 添加新 monitor
cal.add_event(CalendarEvent(
    name='观测: 触发条件描述->预期结果',
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

## 快速演变事件搜索铁律（2026-08-24 从 CLAUDE.md 移入）

**geo/policy_shift/trade_war/fed_emergency 等持续演变事件**，不能按"搜索一次→写入 actual→永久有效"处理。标准三步校验（DOW/官网/交叉确认）不足以应对状态变化。

1. **时间约束搜索** — 搜索必须限定时间范围，不搜不带日期约束的查询：
   - 正确: `"美伊谈判" after:2026-07-14 site:reuters.com`
   - 错误: `美伊谈判最新进展`（按 SEO 排序，"大新闻"压制逆转报道）
2. **逆转/修正优先搜索** — 主动搜索事件被逆转/撤销/修正的信号：
   - `"Hormuz fee" reversal OR backtrack OR withdraw OR cancel OR 撤销`
   - `"Iran policy" update OR latest OR "as of"`
3. **多时点交叉验证** — 对比三个时间点的报道：初始公告报道 → 中间状态 → 最新报道。若三者在 72h 内不一致，以最新 T0/T1 来源为准
4. **搜索排序意识** — 搜索引擎偏向"大新闻"（初始公告），压制"小更新"（逆转）。不要仅依赖搜索结果第一页或摘要位置判断重要性。**主动向下翻页**或使用不同查询找更新
5. **每次第一步都重新验证** — 对于 event_type 在 `FAST_EVOLVING_TYPES` 中且 `actual_updated_at` 超过阈值的事件，调用 `EarlyWarningEngine().check_stale_events(lookback_days=7)` 自动发现，然后对每个 stale 事件用上述规则重新搜索验证

> 背景：2026-07-16 分析中美伊冲突使用 7/13 的过时信息（Trump 征收 20% Hormuz 费），未发现 7/14 已撤销收费改为海湾投资协议。根因：搜索引擎返回初始大新闻压制逆转报道 + 无 staleness 检测机制。
