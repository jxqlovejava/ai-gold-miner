# AI Gold Miner

[![CI](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml/badge.svg)](https://github.com/jxqlovejava/ai-gold-miner/actions/workflows/ci.yml)

**给黄金投资小白的 AI 投资助手。**

买金前不知道怎么看？怕追高、怕抄底、怕消息骗炮？AI Gold Miner 把金价拆成多个维度，让 AI 帮你**看数据、做辩论、查纪律、给建议**，最后输出一份你能看懂的投资报告。

它不做替你下单的“黑箱”，而是做你的**投资副驾驶**：告诉你现在能不能买、该买多少、哪里设止损、接下来要盯哪些大事。

---

## 它能帮你什么？

| 你遇到的难题 | AI Gold Miner 怎么做 |
|---|---|
| 金价涨跌看不懂 | 自动采集金价、美元、实际利率、央行购金等数据，算成信号 |
| 消息太多不知道信谁 | 给每条信息打“可信度标签”（T0 官方 / T1 终端 / T2 媒体 / T3 自媒体） |
| 一涨就想追、一跌就想割 | 15 条投资军规自动审查，情绪上头时拦住你 |
| 不知道买多少 | Agent 多空辩论 + 你的风险偏好，给出具体仓位建议 |
| 不知道什么时候操作 | 自动列出未来高影响事件（非农、CPI、FOMC 等），提前预警 |
| 买了之后忘了复盘 | 自动记录每次预测，后续结算准确率，帮你慢慢变强 |

---

## 核心功能

### 1. 多维金价扫描
把金价从 5 个角度拆开看：

- **技术面**：RSI、MACD、布林带、20 日区间
- **基本面**：美元指数、实际利率、通胀预期、金银比、央行购金
- **消息面**：24h 新闻 + 情感打分
- **情绪面**：期货持仓、CFTC COT、ETF 资金流向
- **事件面**：未来高影响经济数据日历

### 2. Agent 多空博弈
不是由一个 AI 拍脑袋，而是让三个角色“吵一架”：

- **多头分析师**：找看涨理由
- **空头分析师**：找看跌理由
- **投资经理**：综合双方，给出仓位建议

最后输出谁更有道理、建议仓位是多少。

### 3. 投资军规审查
内置 15 条纪律（r001–r015），每条自动判定 ✅/⚠️/❌：

- 单笔不超过总资产 20%
- 总黄金敞口不超过 80%
- 重大数据前 2 小时不重仓
- 单日波动 >3% 不追涨杀跌
- 浮盈 >20% 必须把止损移到成本价以上
- ……

### 4. 投资者画像匹配
先读你的**定性画像**（风险偏好、交易风格）和**定量持仓**（克数、成本、止损），确保建议不超出你的承受能力。

### 5. 多个场景工作流
| 工作流 | 什么时候用 |
|---|---|
| `pre-market` 盘前 | 开盘前快速看报价、新闻、日历、预警 |
| `intra-day` 盘中 | 盘中盯关键价位和异常波动 |
| `post-market` 盘后 | 收盘后完整复盘 + 交易建议 |
| `daily` 日度 | 每天跑一次完整简化扫描 |
| `long-term` 中长期 | 1/6/12/24/36 个月趋势判断 |

### 6. Munger 思维模型
每次决策引用 2–3 个查理·芒格思维模型，比如：

- **市场先生**：不被短期情绪左右
- **安全边际**：为判断错误留缓冲
- **检查清单方法**：用纪律对抗人性漏洞

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

如果看到最后输出「黄金投资决策仪表盘」，就说明跑通了。

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

### Step 2：跑一个完整盘后分析

```bash
gold-miner workflow post-market
```

或Demo模式（跳过需要 API key 的新闻/情绪）：

```bash
gold-miner --demo workflow post-market
```

### Step 3：看报告做决策

报告会给出：

1. 当前金价和关键数据
2. 多空双方理由
3. 建议仓位（0% = 观望，10% = 轻仓，等等）
4. 军规审查结果
5. 未来要盯的大事
6. 是否符合你的画像约束

**不要只看“买入/持有”结论**，重点看军规警告和事件提醒。

---

## 常用命令速查

```bash
# 看实时金价
gold-miner quote

# 日度简化扫描（Demo 模式）
gold-miner --demo scan

# 日度完整扫描（需 API key）
gold-miner scan --news --sentiment

# 列出所有工作流
 gold-miner workflow --workflow-list

# 盘前简报
 gold-miner workflow pre-market

# 盘后完整复盘
 gold-miner workflow post-market

# 中长期分析（12 个月视角）
 gold-miner longterm --horizon 12

# 投资顾问：针对你的持仓给建议
 gold-miner advisor --position 0.1 --cost 915.88

# 军规检查
 gold-miner doctrine --check
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
LLM_API_KEY=your_key_here        # AI 深度分析
FRED_API_KEY=your_key_here       # 美国宏观数据
NEWS_API_KEY=your_key_here       # 新闻情绪
TAVILY_API_KEY=your_key_here     # 深度研究

# [高级] 通知与代理（可选）
WECHAT_WEBHOOK_URL=
MIHOMO_SUB_URL=
AGENT_ENABLED=false
```

没有 API key 也能用，`--demo` 模式会自动跳过需要 key 的功能。

---

## 项目架构（小白版）

```
数据采集层 → 信号处理层 → 决策层 → 输出层
     ↓            ↓            ↓
  央行/ETF    技术/基本面   Agent 辩论
  新闻/COT    情绪/事件     军规审查
```

主要代码目录：

```
src/gold_miner/
├── cli/          # 命令行入口
├── data/         # 数据采集（金价、宏观、新闻、COT）
├── signals/      # 信号处理
├── decision/     # Agent 博弈与风控
├── doctrine/     # 军规 + Munger 模型
├── execution/    # 报告、仪表盘
├── pipeline/     # 统一分析管线
├── workflows/    # 盘前/盘中/盘后/日度/中长期工作流
└── web/          # Streamlit 可视化
```

---

## 输出报告长什么样？

每次完整分析会输出：

1. **当前金价与关键数据**（价格、DXY、实际利率、央行购金等）
2. **多维度信号**（技术 / 基本面 / 消息 / 情绪 / ETF 资金流）
3. **Agent 博弈**：Bull vs Bear vs 投资经理
4. **军规审查**：r001–r015 每条判定
5. **Munger 模型**：2–3 个思维模型解释
6. **画像匹配**：建议是否在你的约束范围内
7. **未来关注事件**：接下来要盯的数据/会议
8. **投资决策仪表盘**：最终信号、仓位、止损、操作清单

示例：

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

  生成时间: 2026-06-29 17:54:01

--------------------------------------------------
  未来关注事件:
    ISM制造业PMI: 2天后 07-02 10:00 | 来源: S&P Global / ISM
    非农就业: 3天后 07-03 08:30 | 来源: BLS
    ISM服务业PMI: 3天后 07-03 10:00 | 来源: S&P Global / ISM
    美国PPI: 12天后 07-12 08:30 | 来源: BLS

==================================================
```

---

## 重要提醒

1. **这不是投资建议**，是辅助你决策的工具。最终下单前请再确认一次自己的纪律。
2. **市场有风险**，任何模型都可能错。仓位控制和止损比预测更重要。
3. **数据会有延迟或失败**，报告会明确标注 API 失败和数据来源等级，不要盲信。
4. **私密信息不上传**：`data/private/` 已加入 `.gitignore`，但不要在公开场合分享。

---

## 了解更多

- 投资决策流程与输出规范：[CLAUDE.md](CLAUDE.md)
- Agent 博弈与披露格式：[AGENTS.md](AGENTS.md)
- 个人补充规则与 ATR 止盈：[data/personal_rules.md](data/personal_rules.md)

---

## 开发与贡献

```bash
# 运行测试
pytest tests/ -m "not slow and not integration" -v

# 代码检查
ruff check src/
mypy src/  # 允许已存在的类型警告
```

---

## License

MIT
