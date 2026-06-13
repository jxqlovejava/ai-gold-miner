# Gold Miner - 黄金投资决策辅助系统

覆盖现货黄金 + 积存金的双标的、多维度、日内级别智能投资决策辅助系统。

支持 **8 维信号分析**、**异常检测**、**多目标策略**、**极端情景推演**、**BTC 联动评估**、**ETF 资金流追踪**、**投资军规审查**，以及 **Munger 多元思维模型库**。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **8 维信号分析** | 技术面、基本面、消息面、情绪面、事件驱动、Polymarket、异常检测、极端情景 |
| **ETF 资金流追踪** | 黄金 ETF + 比特币 ETF 流入流出，跨资产背离信号 |
| **投资军规审查** | 15 条硬约束规则（仓位/时机/情绪/流程），阻断违规决策 |
| **Munger 思维模型** | 232 个芒格多元思维模型，114 个黄金投资相关 |
| **情景分析** | 用户自定义极端事件推演，LLM 驱动影响评估与策略建议 |
| **统一信号管线** | 9 步拓扑排序执行，自动处理依赖关系 |
| **多 Agent 辩论** | 多头 vs 空头 vs 投资经理三方博弈 |
| **多目标策略** | 盈利最大化 / 回本优先 / 落袋为安 / 均衡，自动切换 |
| **异常检测** | 机构带节奏识别、信息源可信度评分、人工审核 |
| **极端情景** | 14 种系统性风险情景（7 看涨 + 7 看跌），含 BTC 联动 |
| **事件溯源** | 不可变事件存储，预测准确率自动追踪 |
| **回测验证** | 策略历史表现验证 |

---

## 架构

```
数据采集层 → 信号处理层 → 决策层 → 执行层
                ↓
          回测层 / 事件溯源层 / 自我改进层
```

### 模块结构

```
src/gold_miner/
├── data/               # 数据采集
│   ├── spot_gold.py       # 现货黄金 (Yahoo Finance)
│   ├── accumulation_gold.py  # 积存金 (AKShare / SGE)
│   ├── macro.py           # 宏观数据 (DXY, 利率, 通胀)
│   ├── news.py            # 新闻抓取 + NLP 情感
│   ├── polymarket.py      # 预测市场数据
│   ├── sentiment.py       # 情绪数据 (上期所 AU 期货)
│   ├── calendar.py        # 经济日历 (FOMC/CPI/PCE/NFP)
│   └── central_bank.py    # 央行购金数据 (WGC)
│
├── signals/            # 信号处理层
│   ├── base.py            # Signal / SignalBundle 基类
│   ├── engine.py          # 多因子打分引擎 (8 维权重)
│   ├── technical.py       # 技术面: RSI, MACD, 布林带, 支撑阻力
│   ├── fundamental.py     # 基本面: DXY, 实际利率, 金银比, 通胀, 央行购金
│   ├── news_signal.py     # 消息面: 新闻情感, 重大事件检测
│   ├── sentiment_signal.py   # 情绪面: 期货持仓, 量价关系
│   ├── polymarket_signal.py  # Polymarket: 真金白银的市场预期
│   ├── event_driven.py    # 事件驱动: 经济日历 → 结构化信号
│   ├── anomaly.py         # 异常检测: 新闻操控, 量价背离, 多空冲突
│   ├── trust_score.py     # 信息源可信度: 准确率追踪 + 时间衰减
│   ├── human_judgment.py  # 人工审核: 确认/驳回/存疑
│   ├── scenario.py        # 极端情景: 14 种系统性风险 + BTC 联动
│   ├── etf_flow_signal.py # ETF 资金流: 黄金 ETF + BTC ETF 流入流出
│   └── pipeline.py        # 统一信号管线 (9 步拓扑执行)
│
├── decision/           # 决策层
│   ├── agents.py          # 多 Agent 辩论 (BullAgent / BearAgent / PortfolioManager)
│   └── risk.py            # 风控审查 (仓位上限, 多空冲突, 策略风控)
│
├── strategy/           # 多目标策略体系
│   ├── objectives.py      # 四策略: 盈利/回本/落袋/均衡
│   ├── engine.py          # 多目标引擎 (评估 + 推荐 + 冲突解决)
│   └── safety.py          # 安全边际 (波动率/流动性/相关性/市场状态)
│
├── execution/          # 执行层
│   ├── dashboard.py       # CLI 决策仪表盘
│   ├── alert.py           # 价格预警
│   ├── journal.py         # 交易日记
│   ├── notifier.py        # 通知推送
│   ├── dimensions.py      # 四维度详细输出
│   └── report.py          # 报告生成
│
├── events/             # 事件溯源
│   ├── models.py          # 不可变事件 / 证据快照 / 预测状态
│   ├── store.py           # JSONL 只追加存储
│   └── resolver.py        # 自动结算到期预测
│
├── backtest/           # 回测层
│   └── engine.py          # 策略历史验证
│
├── improvement/        # 自我改进
│   ├── analyzer.py        # 失误原因分析
│   └── findings.py        # 改进发现
│
├── doctrine/           # 投资军规与思维模型
│   ├── models.py          # 数据模型: 规则 / 策略 / 思维模型
│   ├── rules.py           # 15 条硬约束军规
│   ├── strategies.py      # 8 个投资策略模板
│   ├── mental_models.py   # 10 个核心投资思维模型
│   ├── checker.py         # DoctrineChecker 审查引擎
│   ├── munger_models.py   # 232 个芒格多元思维模型库
│   └── store.py           # 规则持久化
│
├── scenarios/          # 情景分析
│   ├── models.py          # 情景报告数据模型
│   ├── analyzer.py        # LLM 驱动情景分析器
│   └── store.py           # 情景报告持久化
│
├── verification/       # 验证层
│   ├── cli.py             # 验证 CLI
│   └── reporter.py        # 准确率报告
│
├── config.py           # 全局配置 (Pydantic Settings)
└── cli.py              # 命令行入口
```

---

## 8 维信号分析

| 维度 | 来源 | 信号示例 |
|------|------|---------|
| **技术面** | Yahoo Finance XAUUSD | RSI 超买/超卖, MACD 金叉/死叉, 布林带触及 |
| **基本面** | FRED, Yahoo | DXY 强弱, 实际利率趋势, 金银比, 央行购金 |
| **消息面** | NewsAPI, Tavily | 新闻情感倾向, 重大事件检测, 新闻活跃度 |
| **情绪面** | 上期所 AU 期货 | 持仓量变化, 量价关系, 日内偏差 |
| **事件驱动** | 经济日历 | FOMC 利率决议, CPI/PCE/NFP 预警 |
| **Polymarket** | Polymarket API | 预测市场共识 (真金白银的预期) |
| **异常检测** | 跨维度交叉验证 | 新闻操控, 量价背离, 多空冲突 |
| **极端情景** | 14 种系统性风险 | 美债违约, 滞胀, 暴力加息, BTC 替代 |

### 权重配置 (可自定义)

```python
DimensionWeights(
    technical=0.18,    # 技术面
    fundamental=0.22,  # 基本面
    news=0.18,         # 消息面
    sentiment=0.12,    # 情绪面
    event=0.10,        # 事件驱动
    polymarket=0.05,   # Polymarket
    anomaly=0.05,      # 异常检测
    scenario=0.10,     # 极端情景
)
```

---

## 多目标策略体系

| 策略 | 仓位计算 | 止损 | 止盈 | 激活条件 |
|------|---------|------|------|---------|
| **盈利最大化** | Kelly 公式 | 2× ATR (宽) | 3%/6%/10% 三档 | 始终可用 |
| **回本优先** | 半仓 (×0.5) | 1× ATR (紧) | 回本即止盈 | 组合收益 < -5% |
| **落袋为安** | 仓位递减 | 移动止损 | 分批锁定利润 | 组合收益 > +8% |
| **均衡** | 标准仓位 | 1× ATR | 2× ATR | 兜底 |

优先级: **回本 > 落袋 > 盈利 > 均衡**

---

## 极端情景分析 (14 种)

### 看涨情景 (7 种)

| 情景 | 金价影响 | BTC 影响 | 触发条件 |
|------|---------|---------|---------|
| 美债违约—有序 | **+95%** | **+100%** | 技术性违约，快速恢复 |
| 美债违约—无序 | **+90%** | **+150%** (两阶段) | 实质违约，回购冻结 |
| 美元体系崩溃 | **+90%** | **+80%** | DXY < 95，去美元加速 |
| 重大地缘危机 | **+80%** | +10% | 军事冲突，能源跳涨 |
| 滞胀 | **+75%** | -30% | CPI > 4% + GDP < 1% |
| 全球衰退 | **+70%** | -40% (先跌后涨) | PMI 同步收缩 |
| 联储转鸽 | **+65%** | **+80%** | 意外降息 50bp+ |

### 看跌情景 (7 种)

| 情景 | 金价影响 | BTC 影响 | 触发条件 |
|------|---------|---------|---------|
| 沃尔克式暴力加息 | **-85%** | **-60%** | 利率 > 6%，实际利率 > +2.5% |
| 新布雷顿森林 | **-70%** | +20% | G20 新储备货币协议 |
| 央行联合抛售 | **-60%** | +30% | WGC 净购金转净售金 |
| 比特币替代黄金 | **-50%** | **+100%** | BTC 市值超黄金 50% |
| 通缩螺旋 | **-40%** | **-70%** | CPI 连续 3 月为负 |
| 全球永久和平 | **-25%** | 0% | 军费连续 3 年下降 |
| 黄金工业替代 | **-15%** | 0% | 常温超导等突破 |

### 美债违约：有序 vs 无序

```
有序违约 (CDS 30-100bp, VIX < 35)
  → 黄金 +95%, BTC +100%

无序违约 (CDS > 100bp, VIX > 35, 回购冻结)
  第一阶段 (1-3周): 流动性危机
    → 黄金 -5~15%, BTC -40~70%
  第二阶段 (1-6月): 无限 QE 救市
    → 黄金 +50~100%, BTC +100~200%
```

---

## 异常检测

### 检测类型

1. **新闻操控检测** — 新闻情感 vs 技术面+基本面方向分歧 > 0.4
2. **新闻量突增** — 24h 内新闻量 > 2σ，且方向高度一致
3. **量价背离** — 成交量 z-score > 2.5，或量增价不动
4. **多空冲突** — 多空信号接近均衡，市场方向高度不确定

### 信息源可信度

- 追踪每个信息源的预测准确率
- 时间衰减：30 天以上无更新，可信度递减
- 自动缩放信号分数（低可信度来源的信号被削弱）

### 人工审核

- 高严重度异常自动标记为「需人工审核」
- 支持确认 / 驳回 / 存疑三种 verdict

---

## 安装

```bash
# 克隆仓库
git clone https://github.com/jxqlovejava/ai-gold-miner.git
cd ai-gold-miner

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Keys
```

---

## 配置

编辑 `.env` 文件：

```bash
# API Keys
fred_api_key=your_fred_key
news_api_key=your_newsapi_key
tavily_api_key=your_tavily_key

# 交易参数
initial_capital_usd=100000
max_position_pct=0.8
stop_loss_pct=0.03
take_profit_pct=0.06

# 风险画像: aggressive | moderate | conservative
risk_profile=moderate

# LLM (用于文章分析增强)
llm_api_key=your_deepseek_key
llm_model=deepseek-v4-pro

# Polymarket
polymarket_enabled=true
polymarket_min_volume=500
```

---

## 密钥与安全

**严禁将真实密钥提交到 Git。**

- 所有 API Key、代理密码、订阅 Token 必须保存在 `.env` 中
- 项目仅提交 `.env.example` 作为模板，`.env` 已在 `.gitignore` 中排除
- 代理/网络工具相关文件（`data/proxy/provider.yaml`、`data/proxy/config.yaml`、`data/proxy/cache.db`、`src/gold_miner/proxy/mihomo`）已在 `.gitignore` 中排除
- 若不慎将含密钥的文件推送到远程，请立即：
  1. 在服务商后台重置/轮换对应密钥
  2. 使用 `git filter-repo` 或 BFG 清理 git 历史
  3. 强制推送清理后的历史

---

## 使用

### 运行扫描

```bash
gold-miner scan
```

### 获取实时报价

```bash
gold-miner quote
```

### 运行回测

```python
from gold_miner.backtest.engine import BacktestEngine
from gold_miner.signals.pipeline import SignalPipeline, PipelineContext

engine = BacktestEngine(initial_capital=100_000)
context = PipelineContext(gold_df=price_df)
pipeline = SignalPipeline()
result = engine.run(price_df, lambda df: pipeline.execute(context))
```

### 投资军规审查

```bash
# 运行军规审查
gold-miner doctrine --check

# 列出全部规则 / 策略 / 思维模型
gold-miner doctrine --list --type rules
gold-miner doctrine --list --type strategies
gold-miner doctrine --list --type models

# 搜索 Munger 思维模型库
gold-miner doctrine --search "安全边际"
gold-miner doctrine --discipline invest
```

### 情景分析

```bash
# 分析自定义极端事件对黄金的影响
gold-miner scenario "如果2027年爆发全球美债危机，对黄金有什么影响？"

# 查看已保存的情景报告
gold-miner scenario --list
```

### 极端情景评估 (代码)

```python
from gold_miner.signals.scenario import ScenarioAnalyzer

sa = ScenarioAnalyzer()
probs = sa.assess_early_warnings(
    cds_spread=85,
    cpi_value=4.8,
    fed_rate=5.0,
    real_rate=2.0,
    bitcoin_mcap_ratio=0.35,
)
signals = sa.generate_signals()
```

---

## 测试

```bash
# 运行全部测试
pytest tests/ -q

# 排除已知故障测试
pytest tests/ -q --ignore=tests/test_polymarket.py

# 代码质量检查
ruff check src/
mypy src/
```

---

## 数据源

| 数据 | 来源 | 频率 |
|------|------|------|
| 现货黄金 | Yahoo Finance (XAUUSD=X) | 分钟级 |
| 积存金 | AKShare / 上海黄金交易所 | 小时级 |
| 美元指数 | FRED / Yahoo Finance | 日频 |
| 实际利率 | FRED (TIPS 收益率) | 日频 |
| 通胀预期 | FRED (盈亏平衡通胀率) | 日频 |
| 央行购金 | 世界黄金协会 (WGC) | 季度 |
| 新闻 | NewsAPI / Tavily | 实时 |
| 预测市场 | Polymarket | 实时 |
| 情绪 | 上期所 AU 期货持仓 | 日频 |
| 经济日历 | 硬编码 FOMC/CPI/PCE/NFP 日期 | 事件驱动 |

---

## 事件溯源

系统使用不可变的 JSONL 事件存储：

```
PREDICTION_MADE → EVIDENCE_ATTACHED → PRICE_OBSERVED → PREDICTION_SETTLED
```

每个预测自动追踪：
- 预测时的完整证据快照（价格、信号、评分）
- 到期后的实际价格与收益
- 准确率统计

---

## 许可证

MIT
