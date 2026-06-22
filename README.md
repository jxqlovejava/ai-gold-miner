# Gold Miner

[![CI](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml/badge.svg)](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml)

**AI 驱动的现货黄金 + 积存金投资决策辅助系统。**

多维度信号采集 → Agent 多空博弈 → 军规审查 → 仓位建议 → 预测追踪。

---

## 5 分钟上手

### 方式一：Docker（推荐）

```bash
docker compose up --build
```

默认以 Demo 模式运行一次 `scan`，无需任何 API key。

### 方式二：本地 Demo

```bash
git clone https://github.com/jxqlovejava/ai-gold-miner.git
cd ai-gold-miner
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.demo .env
gold-miner --demo scan
```

### 方式三：Web 仪表盘

```bash
pip install -e ".[web]"
gold-miner web
```

打开 http://localhost:8501 查看最新报价与报告。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| 8 维信号 | 技术、基本面、消息面、情绪面、事件驱动、Polymarket、异常检测、极端情景 |
| 中长期分析 | 6-36 个月视角：央行/ETF/COT 趋势 + 财政信用 + 三情景矩阵 |
| Agent 博弈 | Bull / Bear / PortfolioManager 三方辩论 |
| 军规审查 | 15 条硬约束自动判定 |
| Munger 模型 | 决策时引用 2-3 个思维模型 |
| 预测追踪 | 自动记录并结算预测准确率 |
| 情景分析 | 14 种系统性风险压力测试 + 牛/基/熊三情景矩阵 |

---

## 环境配置

`.env.example` 已按三层组织：

```bash
# [demo] 零配置体验（必填）
INITIAL_CAPITAL_USD=100000
MAX_POSITION_PCT=0.8
STOP_LOSS_PCT=0.03
TAKE_PROFIT_PCT=0.06
RISK_PROFILE=moderate

# [api_keys] 进阶功能（可选）
LLM_API_KEY=your_key_here        # AI 分析
FRED_API_KEY=your_key_here       # 宏观数据
NEWS_API_KEY=your_key_here       # 新闻情绪
TAVILY_API_KEY=your_key_here     # 深度研究

# [advanced] 通知、代理、定时任务（可选）
WECHAT_WEBHOOK_URL=
MIHOMO_SUB_URL=
AGENT_ENABLED=false
```

---

## 常用命令

```bash
# 实时报价
gold-miner quote

# 完整扫描（Demo 模式）
gold-miner --demo scan

# 完整扫描（启用新闻与情绪）
gold-miner scan --news --sentiment --deep

# 生成投资报告
gold-miner report

# 投资顾问
gold-miner advisor --position 0.45 --cost 1014.42

# 军规审查
gold-miner doctrine --check

# 工作流列表
gold-miner workflow --workflow-list

# 中长期金价分析（12/24/36 个月）
gold-miner longterm --horizon 24

# 中长期分析 dry-run
gold-miner longterm --horizon 24 --dry-run
```

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
├── cli/              # 命令行入口（已拆分）
├── data/             # 数据采集
├── signals/          # 8 维信号处理
├── decision/         # Agent 博弈与风控
├── strategy/         # 多目标策略
├── execution/        # 报告、仪表盘、预警
├── doctrine/         # 军规与 Munger 模型
├── events/           # 事件溯源
├── scenarios/        # 极端情景
├── pipeline/         # 统一分析管线
└── web/              # Streamlit 仪表盘
```

---

## 输出规范

每次金价分析必须包含：

1. **Agent 博弈**：Bull / Bear / PortfolioManager 完整论据
2. **8 维信号**：逐项说明，0 信号解释原因
3. **API 失败披露**：不隐藏数据源异常
4. **Source Truth**：外部信息标注 T0/T1/T2/T3
5. **军规 + Munger + 画像匹配**

详见 [AGENTS.md](AGENTS.md) 与 [CLAUDE.md](CLAUDE.md)。

---

## 开发与贡献

```bash
# 运行测试
pytest tests/test_cli/ -v

# 代码检查
ruff check src/
mypy src/  # 允许已存在的类型警告
```

---

## License

MIT
