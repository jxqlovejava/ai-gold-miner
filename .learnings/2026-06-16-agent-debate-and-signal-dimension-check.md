# Learning: 金价分析必须完整展示 Agent 博弈并检查各信号维度有效性

Date: 2026-06-16
Trigger: 用户指出之前的分析缺少多 Agent 博弈细节，且未说明 scan 中多个信号维度失效。

## 学到的规则

1. **Agent 博弈不能只看结论**
   - 必须分别列出 BullAgent、BearAgent、PortfolioManager 的：立场、建议仓位、信心、核心论据（逐条评分）
   - 必须说明 PortfolioManager 最终决策与军规调整后的差异

2. **必须检查 scan 各维度是否实际生效**
   - 技术面 / 基本面 / 消息面 / 情绪面 / ETF 资金流 / 事件驱动 / Polymarket / 异常检测 / 极端情景
   - 对于 0 信号或 API 失败的维度，必须主动说明原因

3. **不能盲目信任系统输出**
   - NewsAPI 超时、anysearch 配额耗尽、DuckDuckGo 抓取失败会导致消息面/事件面失效
   - Yahoo Finance 403 会导致国际金价数据缺失
   - 技术面横盘时 0 信号是合理的，但要解释清楚

4. **金价分析需要系统输出 + 外部 Source Truth 双验证**
   - 系统信号缺失时，必须用自己的搜索/工具补充事实核查
   - 三大事件等关键外部信息必须标注 `[verified: T0/T1/T2/T3]` 或 `[unverified]`

## 如何应用

- 每次运行 `gold-miner scan` 后，先读取完整日志，提取 Agent 博弈三段论
- 检查多因子评分详情中每个维度的数值，对 0 信号维度做原因说明
- 遇到 API 失败，直接写明"该维度因 X 失效"，不能假装不存在
- 最终输出必须包含：多维度信号表、完整 Agent 博弈、Source Truth、军规、Munger、画像
- **开源场景**：规范已写入 `CLAUDE.md`、`AGENTS.md`、`README.md`，所有克隆项目的代理/用户都会自动加载

## 相关文件

- CLAUDE.md
- AGENTS.md（新增）
- README.md
- src/gold_miner/decision/agents.py
- src/gold_miner/signals/engine.py
- src/gold_miner/signals/pipeline.py
- /tmp/gold_scan_*.log
