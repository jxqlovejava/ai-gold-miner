---
name: ai-gold-miner
description: 黄金/金价/积存金分析（青蚨）。当用户提出金价分析、黄金走势、行情快报、技术面分析、军规自查、条件单、持仓盈亏、投资顾问咨询、文章分析、情景推演、预测复盘等相关问题时，激活本 skill 并调用 gold_cmd.py 获取专业结果。
user-invocable: true
metadata:
  openclaw:
    emoji: 🪙
    skillKey: ai-gold-miner
    author: jxqlovejava
    requires:
      bins: ["python3"]
---

# AI Gold Miner — 黄金/积存金分析 Skill

当用户提出与黄金、金价、积存金相关的分析问题时，运行本项目的命令封装器 `gold_cmd.py` 获取结果，并把输出**原样、完整**地呈现给用户（不自行改写结论）。

## 触发条件

当用户的问题属于以下任一类别时，激活本 skill：

| 类别 | 触发词示例 | 命令 |
|------|-----------|------|
| 行情快报 | 金价多少 / 现在金价 / 行情快报 / 黄金现在什么价 | `quote` |
| 完整分析 | 金价分析 / 黄金走势分析 / 分析一下 / 完整报告 / 怎么看 | `scan` |
| 技术面 | 技术面 / 走势图 / RSI / MACD / 均线 / 缠论 | `watch` |
| 军规自查 | 军规 / 规则自查 / 军规审查 / r001 | `doctrine` |
| 条件单 | 条件单 / 挂单 / 限价单 / OCO | `orders` |
| 持仓盈亏 | 持仓 / 盈亏 / 浮盈 / 我的仓位 / 亏了多少 | `position` |
| 投资顾问 | 投资顾问 / 我该怎么办 / 咨询 / 要不要卖 / 要不要加仓 | `advisor` |
| 文章分析 | 分析这篇文章 + URL / 这篇文章怎么看 / 帮我分析链接 | `analyze` |
| 情景推演 | 情景 / 如果……会怎样 / 推演 / 极端情况 | `scenario` |
| 预测复盘 | 预测 / 复盘 / 之前的预测 / 准确率 | `track` |

## 使用指引

### 命令封装器

所有命令统一通过 `/home/ubuntu/.hermes/scripts/gold_cmd.py` 运行：

```bash
python3 /home/ubuntu/.hermes/scripts/gold_cmd.py <subcommand> [--url X] [--text X] [--question X] [--quick]
```

运行前无需设置 PYTHONPATH —— 脚本内部已处理。运行后**把 stdout 的 markdown 原样、完整呈现给用户**。

### 命令分类

**同步命令**（输出 markdown，直接呈现）：
- `quote` — 行情快报（现价/持仓浮盈/ATR止盈位）
- `watch` — 技术面（RSI/MACD/布林/MA200/缠论）
- `doctrine` — 军规自查表（r001-r032）
- `orders` — 条件单状态
- `position` — 持仓详情（净口径）
- `track` — 预测追踪
- `analyze` — 文章分析，需带 URL：`analyze --url "https://..."`

**异步命令**（stdout 是"已启动"提示，最终报告由脚本自动推送到用户微信，无需再等待）：
- `scan` — 完整 9 步分析，可选 `--quick`（关新闻/情绪加速）
- `advisor` — 投资顾问，可用 `--question "具体问题"`
- `scenario` — 情景推演，用 `--text "情景描述"`

### 参数提取

- `analyze`：从用户消息中提取 `http(s)://` 链接传给 `--url`；若无链接，提示用户提供文章链接
- `scenario`：把用户描述的情景传给 `--text`；用户未给具体情景时用默认值
- `advisor`：把用户的具体问题传给 `--question`；用户只问"怎么办"时可不传（走默认咨询）

## 不应触发的场景

- **A股/港股/美股/股票/大盘分析** → 路由回 `financial-analysis` skill，不要用本 skill
- 纯编程或技术开发问题
- 非黄金的金融资讯查询（黄金以外的商品、汇率、基金）
- 涉及具体交易执行指令（如"帮我买入XX克"）——本 skill 只提供分析，不代客下单
