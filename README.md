# AI Gold Miner

[![CI](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml/badge.svg)](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml)

**给黄金投资小白的 AI 投资副驾驶。**

买金前不知道怎么看？怕追高、怕抄底、怕消息骗炮？AI Gold Miner 把金价拆成多个维度，让 AI 帮你**看数据、做辩论、查纪律、给建议**，最后输出一份你能看懂的投资报告。

它不做替你下单的"黑箱"，而是做你的**投资副驾驶**：告诉你现在能不能买、该买多少、哪里设止损、接下来要盯哪些大事。支持**现货黄金 + JD 积存金**双标的投资分析。

---

## 它能帮你什么？

| 你遇到的难题 | AI Gold Miner 怎么做 |
|---|---|
| 金价涨跌看不懂 | 自动采集金价、美元、实际利率、央行购金等数据，16 个信号生成器并行扫描 |
| 消息太多不知道信谁 | 每条信息打"可信度标签"（T0 官方 / T1 终端 / T2 媒体 / T3 自媒体），FactChecker 多源交叉验证 |
| 一涨就想追、一跌就想割 | 30 条投资军规自动审查（r001–r030），情绪上头时拦住你 |
| 不知道买多少 | Agent 多空三方辩论（🐮Bull / 🐻Bear / 💼PM）+ Kelly 公式仓位计算 |
| 不知道什么时候操作 | 自动列出未来高影响事件（非农、CPI、FOMC、PCE 等），DOW 硬校验杜绝日期错误 |
| 买了之后忘了复盘 | 自动记录预测 + 结算准确率，journal/record 追踪每一次决策 |
| 机构资金流向看不懂 | 独立评估 CFTC COT + ETF 资金流 + COMEX 大户 + 13F，生成聪明钱评分 |
| 中长期趋势看不清 | 1/6/12/24/36 个月多维度趋势判断，情景矩阵 + 概率加权预期 |
| 突发新闻不会判断 | Sentinel 突发新闻监控，NLP 否定句过滤 + 语义去重，FactChecker 核查 |
| 怕环境配不好 | `doctor` / `setup` 一键诊断，`proxy-install` 自动配置代理 |

---

## 核心功能

### 1. 9 步统一分析管线 (`gold-miner scan`)

```
prepare → signals → source_truth → doctrine_check → munger_models
  → profile_match → agent_debate → decide → plan
```

| 步骤 | 名称 | 核心输出 |
|------|------|---------|
| 1 | **prepare** | 日历 DOW 校验 + 事件同步 + 深度新闻 + 7 路数据采集（4 路并行） |
| 2 | **generate_signals** | 16 个信号生成器并行扫描（全部含 Monitor + 新闻原文 + LLM） |
| 3 | **source_truth** | 来源验证（T0-T3）+ 事实 vs 解释分类 + 置信度标注 |
| 4 | **doctrine_check** | 军规审查（r001-r030）+ 风控审查（风险预算/波动率/集中度） |
| 5 | **munger_models** | 自动选择 3 个最相关 Munger 思维模型 + 仓位约束 |
| 6 | **profile_match** | 投资者画像约束检查（持仓上限/周期/风险偏好/硬止损） |
| 7 | **agent_debate** | 🐮Bull / 🐻Bear / 💼PM 三方辩论，综合前 6 步输入 |
| 8 | **decide** | 交易建议 + Kelly 仓位计算 + 条件单审查 |
| 9 | **plan** | 未来 14 天事件关注 + 情景预案 + Monitor 创建 |

### 2. 多维度信号系统（16 个信号生成器）

#### 核心维度
| 维度 | 生成器 | 数据来源 | 核心指标 |
|------|--------|---------|---------|
| **technical** | `TechnicalSignal` | Yahoo Finance | RSI、MACD、布林带、20 日区间、200/60 日均线、ATR |
| **fundamental** | `FundamentalSignal` | FRED / Yahoo | ICE 美元指数(~100)、10Y TIPS 实际利率、盈亏平衡通胀、金银比、央行购金、印度 GDP/INR |
| **news** | `NewsSignal` + `FactChecker` | NewsAPI / Tavily / WebSearch | 24h 新闻情感 + 事实核查 + 地缘风险溢价 + 可信度警告 |
| **sentiment** | `SentimentSignal` | AKShare 上期所 | AU 期货持仓量、量价关系、日内偏向 |

#### 👔 聪明钱维度（资金流独立评估，不与常规论据混排）
| 维度 | 生成器 | 数据来源 | 核心指标 |
|------|--------|---------|---------|
| **cot** | `COTSignal` | CFTC.gov | 非商业净多/空仓、投机持仓变化、商业套保头寸 |
| **etf_flow** | `ETFGoldFlowSignal` | AKShare | 国内黄金 ETF 申赎资金流 |
| **institutional** | `InstitutionalSignal` | CFTC / AKShare / 13F | COMEX 大户集中度、机构多空比、综合评分 |

#### 事件与预测维度
| 维度 | 生成器 | 数据来源 | 核心指标 |
|------|--------|---------|---------|
| **event** | `EventDrivenSignal` + `EconomicCalendarSignal` | 内置日历引擎 | 未来高影响事件检测 + 历史冲击量化 |
| **recent_events** | `RecentEventsSignal` | 日历事件 | 已发布事件时效性加权（<24h=1.0 → >7d=0），stale 事件自动降权 |
| **polymarket** | `PolymarketSignal` | Polymarket API | 预测市场隐含概率 |
| **hype_bias** | `HypeBiasSignal` | 新闻 + 搜索量 | 过度炒作检测，散户情绪噪音过滤 |

#### 中长期 & 异常维度
| 维度 | 生成器 | 数据来源 | 核心指标 |
|------|--------|---------|---------|
| **long_term** | `LongTermTrendSignal` + `LongTermFundamentalSignal` + `LongTermScenarioSignal` | 历史数据 + 宏观 | 趋势方向 + 基本面评分 + 情景矩阵 |
| **scenario** | `ScenarioSignal` | 价格 / 用户输入 | 情景分析（如"美伊冲突升级"） |
| **anomaly** | `AnomalySignal` | 价格数据 | 背离检测、放量异常 |
| **monitor** | `MonitorSignal` | 日历 Monitor | 持续监控条件触发检测 |

### 3. Agent 多空三方博弈
不是由一个 AI 拍脑袋，而是让三个角色"吵一架"：

- **🐮 多头分析师 (BullAgent)** — 找看涨理由，**资金流论据独立小节展示**
- **🐻 空头分析师 (BearAgent)** — 找看跌理由，**资金流论据独立小节展示**
- **💼 投资经理 (PortfolioManager)** — 综合双方 + 军规审查 + Kelly 公式，给出最终仓位建议

**关键规则**：聪明钱资金流论据（CFTC/ETF/COMEX/13F）不可被常规论据淹没，必须独立展示。

### 4. 投资军规审查（r001–r030）

内置 30 条纪律，每条自动判定 ✅/⚠️/❌，分 block/warn/info 三级严重度：

| 类别 | 规则数 | 示例 | 严重度 |
|------|--------|------|--------|
| 仓位管理 | 6 | 单笔≤20%、总敞口≤80%、黄金>50%提示集中风险 | block |
| 情绪纪律 | 5 | 单日波动>3%不追涨杀跌、浮盈>20%上移止损、连续止损后休整 | block |
| 操作纪律 | 5 | 重大数据前 2h 不重仓、用条件单代替盯盘、减仓趁反弹 | warn |
| 信号纪律 | 4 | 多维度确认、聪明钱与散户分歧警示、禁止单一维度决策 | warn |
| 趋势与止盈 | 4 | ATR 移动止盈（14×ATR×2.5）、均线趋势过滤 | block |
| 建仓与估值 | 4 | 分批建仓（≥2 批，间隔≥5 交易日）、安全边际加仓、再平衡 | warn |
| 原则纪律 | 2 | 永远留安全边际、不借钱投资 | warn |

完整 30 条见 [`docs/doctrine.md`](docs/doctrine.md)。

### 5. 投资者画像匹配

每次分析自动读取你的**定性画像**（风险偏好、交易风格）和**定量持仓**（克数、成本、止损），确保建议不超出你的承受能力。支持自定义个人规则（`data/private/personal_rules.md`）覆盖默认参数。

### 6. Munger 思维模型

内置 25 个思维模型（投资/心理/方法论/系统思维/经济学 5 大类），每次决策自动选择 3 个最相关模型解释逻辑和认知陷阱。

### 7. 中长期趋势分析 (`gold-miner longterm`)

输出情景矩阵（bull/base/bear）、概率加权预期价格范围、触发条件与再平衡规则，覆盖 1/6/12/24/36 个月周期。

---

## 5 分钟上手

### 方式一：Docker（最简单，无需配环境）

```bash
docker compose up --build
```

默认以 Demo 模式跑一次 `scan`，无需 API key。

### 方式二：本地安装

> 需要 Python **3.11**（项目已锁定 3.11，避免 OpenSSL 3.x 兼容问题）

```bash
# 1. 克隆代码
git clone https://github.com/jxqlovejava/ai-gold-miner.git
cd ai-gold-miner

# 2. 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 复制 demo 环境变量
cp .env.demo .env

# 5. 运行一次体验
gold-miner --demo scan
```

如果看到控制台逐步骤输出结果，就说明跑通了。

### 方式三：Web 仪表盘

```bash
pip install -e ".[web]"
gold-miner web
```

打开 http://localhost:8501 查看可视化报告。

---

## 第一次用，推荐这样做

### Step 1：填你的投资画像

复制示例文件到私密目录：

```bash
cp data/investor_profile.example.md data/private/investor_profile.md
cp data/portfolio.example.yaml data/private/portfolio.yaml
```

编辑这两个文件，填上你的真实情况：

- `data/private/investor_profile.md`：风险偏好、交易风格、信源偏好
- `data/private/portfolio.yaml`：持有克数、成本价、止损价、总资金

> 这两个文件已加入 `.gitignore`，不会提交到 Git。

### Step 2：跑信息准备

```bash
gold-miner prepare
```

执行管线 Step 1：日历校验 + 事件同步 + 深度新闻搜索 + 数据采集。

### Step 3：跑完整分析

```bash
# Demo 模式（跳过需要 API key 的新闻/情绪/LLM）
gold-miner --demo scan

# 完整模式（需配置 API key）
gold-miner scan --news --sentiment --deep
```

### Step 4：看报告做决策

报告会逐步骤输出到控制台：

1. 当前金价和关键数据
2. 多维度信号逐项说明（含聪明钱资金流子项）
3. Source Truth 验证标签（T0-T3）
4. 军规审查结果（r001-r030，✅/⚠️/❌）
5. Munger 模型解释
6. 画像约束检查
7. Agent 三方博弈（含资金流论据独立展示）
8. 交易建议 + 条件单审查
9. 后续事件关注 + 情景预案 + Monitor

**不要只看"买入/持有"结论**，重点看军规警告和事件提醒。

---

## 常用命令速查

### 核心分析

```bash
# 看实时金价（现货 + JD 积存金）
gold-miner quote

# 信息准备（日历校验 + 事件同步 + 数据采集）
gold-miner prepare

# 日度完整扫描（9 步管线）
gold-miner scan --news --sentiment --deep

# Demo 模式（跳过需要 API key 的功能）
gold-miner --demo scan
```

### 中长期 & 情景

```bash
# 中长期分析（12 个月视角，支持 1/6/12/24/36）
gold-miner longterm --horizon 12

# 情景分析
gold-miner scenario --text "美伊冲突升级"
```

### 军规 & 模型

```bash
# 军规检查
gold-miner doctrine --check

# 搜索 Munger 模型
gold-miner doctrine --search "安全边际"
```

### 文章 & 研究

```bash
# 文章深度分析（LLM 解读 + 预判生成）
gold-miner analyze --url <article_url> --deep

# 查看文章列表
gold-miner analyze --list
```

### 预测追踪

```bash
# 列出所有预测
gold-miner track --list

# 创建价格预测
gold-miner track --price 3200 --direction bullish --confidence 0.7

# 结算预测
gold-miner track --resolve-id <id>

# 审查预测准确率
gold-miner review
```

### 日志 & 记录

```bash
# 记录交易
gold-miner record --action buy --price 886.74 --amount 10

# 查看交易日志
gold-miner journal --list

# 查看报告
gold-miner report --latest
```

### 回测 & 验证

```bash
# 策略回测
gold-miner backtest --days 365 --capital 100000

# 行为回测（对比 AI 建议 vs 实际交易）
gold-miner backtest --behavior

# 环境诊断
gold-miner doctor

# 配置向导
gold-miner setup
```

### 后台运行

```bash
# Web 仪表盘
gold-miner web --port 8501

# 定时扫描守护进程
gold-miner daemon --interval 60

# 代理安装
gold-miner proxy-install
```

---

## 进阶配置

### 环境变量

复制 `.env.example` 为 `.env`，按需填写：

```bash
# [基础] 必填
INITIAL_CAPITAL_USD=100000
MAX_POSITION_PCT=0.8
STOP_LOSS_PCT=0.03
TAKE_PROFIT_PCT=0.06
RISK_PROFILE=moderate

# [API] 进阶功能（可选）
LLM_API_KEY=your_key_here        # AI 深度分析（DeepSeek）
FRED_API_KEY=your_key_here       # 美国宏观数据（实际利率、CPI 等）
NEWS_API_KEY=your_key_here       # 新闻情绪
TAVILY_API_KEY=your_key_here     # 深度研究（web search fallback）

# [高级] 通知与代理（可选）
WECHAT_WEBHOOK_URL=              # 微信通知 webhook
MIHOMO_SUB_URL=                  # mihomo/clash 订阅链接
AGENT_ENABLED=false              # Agent 定时调度
```

没有 API key 也能用，`--demo` 模式会自动跳过需要 key 的功能。

### 私密数据文件

| 文件 | 用途 |
|------|------|
| `data/private/investor_profile.md` | 定性画像：风险偏好、交易风格、信源偏好 |
| `data/private/portfolio.yaml` | 定量持仓：克数、成本价、止损价、总资金（数字唯一真相源） |
| `data/private/personal_rules.md` | 个人补充规则（ATR 参数、止损价位、持仓拆分等） |
| `data/private/trade_log.md` | 交易日志 |
| `data/private/prediction_journal.jsonl` | 预测记录 |
| `data/private/event_store.jsonl` | 事件存储 |
| `data/private/economic_data.jsonl` | 经济数据历史 |
| `data/private/conditional_orders.jsonl` | 活跃条件单 |
| `data/private/doctrine_state.json` | 军规状态持久化 |
| `data/private/scenarios.jsonl` | 情景分析存档 |
| `data/private/conversations/` | 按天记录的投资决策对话 |

---

## 项目架构

```
数据采集层 ──► 信号处理层 ──► 分析管线 (9步) ──► 决策输出
     ↓               ↓               ↓               ↓
   FRED/FX      16 个信号生成器    prepare         Agent 三方辩论
   News/COT       技术/基本面      signals         军规审查 (30条)
   AKShare/JD     聪明钱/事件      source_truth    交易建议 + Kelly
   CFTC/13F       情绪/异常        doctrine        情景预案 + Monitor
   Polymarket      中长期/监控      munger          条件单审查
                                    profile_match
                                    agent_debate
                                    decide
                                    plan
```

主要代码目录：

```
src/gold_miner/
├── cli/          # 命令行入口（20+ 命令）
├── pipeline/     # 统一分析管线（9 步 AnalysisPipeline + LongTermAnalyzer）
├── signals/      # 16 个信号生成器（技术/基本面/聪明钱/新闻/情绪/事件/异常/中长期）
├── decision/     # Agent 博弈（Bull/Bear/PM）+ 机构资金流独立评估
├── agent/        # 多 Agent 协调调度
├── intelligence/ # 情报分析（多源融合 + 交叉验证）
├── data/         # 数据采集（FRED、Yahoo、AKShare、CFTC、News、JD、Polymarket）
├── doctrine/     # 30 条军规规则 + 25 个 Munger 思维模型库
├── advisor/      # 投资顾问、极端情景预警、EarlyWarningEngine
├── strategy/     # Kelly 公式、ATR 止损、仓位风控、策略目标
├── execution/    # 警报、仪表盘格式化、通知
├── llm/          # DeepSeek LLM 客户端
├── events/       # 事件日历模型 + 存储 + DOW 校验
├── sentinel/     # 突发新闻监控（NLP 否定句过滤、语义去重）
├── storage/      # 本地文件持久化（data/private/）
├── proxy/        # 代理管理器（mihomo/clash 隔离进程，httpx 连接池复用）
├── backtest/     # 策略回测引擎 + 行为回测
├── scenarios/    # 情景分析引擎
├── verification/ # 预测结算与准确率追踪
├── experience/   # 经验加载与自改进
├── improvement/  # 自改进循环
├── web/          # Streamlit 可视化仪表盘
├── utils/        # 工具函数
└── config.py     # pydantic-settings 全局配置
```

---

## 数据源

| 数据 | 来源 | 频率 | 需要 API Key |
|------|------|------|-------------|
| 国际金价 (XAU/USD) | Yahoo Finance | 实时 | 否 |
| JD 积存金价格 | JD Finance API | 实时 | 否 |
| 美元指数 (ICE DXY) | Yahoo Finance | 实时 | 否 |
| 10Y TIPS 实际利率 | FRED (DFII10) | 日频 | FRED_API_KEY |
| CFTC COT 持仓 | CFTC.gov | 周频（周五） | 否 |
| COMEX 大户持仓 | CFTC.gov | 周频 | 否 |
| 机构 13F 持仓 | SEC.gov | 季频 | 否 |
| 国内黄金 ETF 流向 | AKShare | 日频 | 否 |
| 上期所 AU 期货 | AKShare | 日频 | 否 |
| 央行购金 (PBOC) | AKShare / PBOC | 月频 | 否 |
| 金价新闻 | NewsAPI / Tavily | 实时 | NEWS_API_KEY / TAVILY_API_KEY |
| Polymarket 预测 | Polymarket API | 实时 | 否 |
| 经济日历 | 内置引擎 | 预加载 | 否 |
| LLM 深度分析 | DeepSeek API | 按需 | LLM_API_KEY |

---

## 输出报告长什么样？

每次完整分析会逐步输出：

1. **当前金价与关键数据**（价格、DXY、实际利率、央行购金、JD 积存金）
2. **多维度信号逐项说明**（技术/基本面/👔聪明钱/新闻/事件/情绪/预测/异常/中长期）
3. **Source Truth + 事实 vs 解释**（T0-T3 标签 + 验证置信度）
4. **军规自查**（r001-r030 每条判定 ✅/⚠️/❌）
5. **Munger 模型**（3 个思维模型解释）
6. **画像匹配**（约束检查表）
7. **Agent 三方博弈**（🐮Bull / 🐻Bear / 💼PM 含资金流论据）
8. **交易决策仪表盘**（信号、仓位、止损、条件单、操作清单）
9. **后续事件关注 + 情景预案 + Monitor**

示例仪表盘：

```
==================================================
           黄金投资决策仪表盘
==================================================
  信号: 持有
  标的: 积存金 Au99.99 (元/克) | 国际 $4,062/oz
  仓位: 0%

  入场价: 886.74
  建议区间: 882.31 ~ 891.17
--------------------------------------------------
  操作清单:
    1. 维持当前仓位，等待更明确信号
--------------------------------------------------
  未来关注事件:
    ISM制造业PMI: 2天后 07-02 10:00 | 来源: S&P Global / ISM
    非农就业: 3天后 07-03 08:30 | 来源: BLS
==================================================
```

---

## 重要提醒

1. **这不是投资建议**，是辅助你决策的工具。最终下单前请再确认一次自己的纪律。
2. **市场有风险**，任何模型都可能错。仓位控制和止损比预测更重要。
3. **数据会有延迟或失败**，报告会明确标注 API 失败和数据来源等级，不要盲信。
4. **私密信息不上传**：`data/private/` 已加入 `.gitignore`，但不要在公开场合分享。

---

## 开发

### 运行测试

```bash
pytest tests/ -v
```

### 代码检查

```bash
ruff check src/
mypy src/
```

### 项目配置

详见 [pyproject.toml](pyproject.toml)。使用 hatchling 构建系统，核心依赖包括 pydantic、httpx、yfinance、akshare、pandas、ta-lib。

---

## 了解更多

- 项目上下文与决策约束：[CLAUDE.md](CLAUDE.md)
- Agent 博弈与披露格式：[AGENTS.md](AGENTS.md)
- 完整军规体系：[docs/doctrine.md](docs/doctrine.md)
- 个人补充规则：[data/personal_rules.example.md](data/personal_rules.example.md)
- Pipeline 操作模板：`gold-analysis-pipeline` skill

## 文档

完整项目文档（面向开发者和 AI 代理）位于 [openwiki/](openwiki/quickstart.md) 目录，包含：

- [Quickstart](openwiki/quickstart.md) — 快速开始与导航
- [Architecture](openwiki/architecture/overview.md) — 模块架构与数据流
- [Analysis Pipeline](openwiki/pipeline/analysis-pipeline.md) — 9 步分析管线详解
- [Long-Term Analysis](openwiki/pipeline/long_term.md) — 中长期分析引擎
- [Signal System](openwiki/signals/overview.md) — 信号系统
- [CLI Commands](openwiki/cli/commands.md) — 命令参考
- [Data Sources](openwiki/data-sources/overview.md) — 数据源与代理管理
- [Investment Doctrines](openwiki/doctrine/overview.md) — 军规、Munger、画像

---

## License

MIT
