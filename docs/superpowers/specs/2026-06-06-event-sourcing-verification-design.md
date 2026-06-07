# 事件溯源预测验证系统 — 设计文档

## 概述

将现有 `PredictionTracker` + `ArticleJournal` 重构为统一事件溯源 (Event Sourcing) 架构，实现：

1. 所有分析预测结论 + 佐证证据不可变记录
2. 分级自动验证（短期自动、中长期人工确认）
3. 生成人类可读 Markdown 验证报告
4. 持续自改进闭环

## 事件模型

### EventStore (JSONL)

```
data/event_store.jsonl
```

事件只追加不修改。每条事件一行 JSON，通过 `prediction_id` 关联同一预测的所有事件。

### 7 种事件类型

| 事件 | 触发时机 | 核心字段 |
|------|----------|---------|
| `prediction_made` | scan/analyze/manual | direction, score, confidence, horizon_days, source, auto_resolve |
| `evidence_attached` | 与预测同时或后续补充 | EvidenceSnapshot（价格/DXY/利率/金银比/信号摘要/来源引用） |
| `price_observed` | daemon定时/手动 | observed_price, observed_at |
| `prediction_settled` | 自动(<7天)/人工确认后 | was_correct, actual_return, settled_by(auto/human) |
| `human_verified` | 人工确认 | verifier_notes, override |
| `prediction_invalidated` | 数据异常等 | reason |
| `report_generated` | 手动/定期 | report_path, metrics_snapshot |

### EventStore API

```python
class EventStore:
    def append(event_type, prediction_id, payload) -> Event
    def events_for(prediction_id) -> list[Event]
    def replay(prediction_id) -> PredictionState  # 重放事件计算状态
    def all_predictions() -> list[str]             # 去重的 prediction_id 列表
    def pending_settlement() -> list[str]          # 到期未结算
    def pending_verification() -> list[str]        # 待人工确认
```

## 证据模型

### EvidenceSnapshot（不可变 dataclass）

```python
@dataclass(frozen=True)
class EvidenceSnapshot:
    snapshot_id: str
    prediction_id: str
    timestamp: datetime

    # 原始价格数据
    spot_gold: float
    dxy: float | None = None
    silver: float | None = None
    real_rate: float | None = None
    breakeven: float | None = None
    gold_silver_ratio: float | None = None

    # 信号摘要
    signals_summary: tuple[SignalSnapshot, ...] = ()
    dimension_scores: dict[str, float] = field(default_factory=dict)

    # 来源引用
    source_type: str = "scan"  # scan | article | manual
    source_refs: tuple[SourceRef, ...] = ()

@dataclass(frozen=True)
class SignalSnapshot:
    name: str
    dimension: str
    direction: str
    score: float
    description: str

@dataclass(frozen=True)
class SourceRef:
    ref_type: str      # article | data_source | url
    ref_id: str = ""
    url: str = ""
    title: str = ""
    description: str = ""
```

## 自动验证闸门

| 条件 | 行为 |
|------|------|
| auto_resolve + horizon <= 7天 + 到期 | 自动结算 |
| auto_resolve + horizon > 7天 + 到期 | 仅记价，入待确认队列 |
| auto_resolve = false | 不自动结算，纯人工 |

### AutoResolver（daemon 集成）

daemon 每轮扫描后执行：
1. 加载已到期未结算的 auto_resolve 预测
2. 获取当前实际价格
3. 追加 `price_observed` 事件
4. horizon <= 7天：自动追加 `prediction_settled`
5. horizon > 7天：入待确认队列
6. 生成本轮结算 Markdown 报告 → `data/reports/`

## CLI 命令

### 新增 `verify` 命令

```
gold-miner verify              # 列出待人工确认的预测
gold-miner verify --id <ID>    # 查看预测详情 + 完整证据链
gold-miner verify --confirm <ID>           # 确认结算
gold-miner verify --reject <ID> --reason "..."  # 驳回/无效化
gold-miner verify --report               # 生成当前周期 Markdown 验证报告
```

### 扩展 `track` 命令

```
gold-miner track  # 保留现有功能，底层切换到 EventStore
```

### 扩展 `review` 命令

```
gold-miner review  # 从 EventStore 重放计算准确率
```

## Markdown 验证报告

生成路径：`data/reports/YYYY-MM-DD-verification-report.md`

模板结构：
- 本周期结算统计（自动/人工、正确/错误、分维度准确率）
- 每个已结算预测的详情卡片（结论→证据→结果→反思）
- 待人工确认列表
- 改进建议（调用现有 FindingGenerator）

## 迁移计划

1. `PredictionTracker` 和 `ArticleJournal` 保留不动
2. 新增 `EventStore` 作为统一写入层
3. `run_scan` → 同时写旧 PredictionTracker + 新 EventStore（双写过渡）
4. `run_analyze` → 同时写旧 ArticleJournal + 新 EventStore
5. daemon 集成 AutoResolver
6. 验证稳定后切换读取到 EventStore
7. 旧文件保留为历史归档

## 文件结构

```
src/gold_miner/
├── events/
│   ├── __init__.py
│   ├── store.py          # EventStore (JSONL append-only)
│   ├── models.py         # Event, EvidenceSnapshot, PredictionState
│   └── resolver.py       # AutoResolver
├── verification/
│   ├── __init__.py
│   ├── reporter.py       # Markdown 报告生成
│   └── cli.py            # verify 命令
├── improvement/
│   ├── tracker.py        # 保留，双写期间仍用
│   └── ...
└── cli.py                # 扩展 verify 命令
```
