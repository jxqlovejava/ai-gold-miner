# Learning: 金价分析必须走完整项目 pipeline

Date: 2026-06-15
Trigger: 用户反馈之前的分析缺少多维度信号、Agent 博弈、Source Truth Verification、Munger 模型和画像匹配。

## 学到的规则

1. **必须跑完整项目 pipeline**
   - 多维度信号采集（technical/fundamental/news/sentiment/etf_flow）
   - Agent 博弈（BullAgent / BearAgent / PortfolioManager）
   - Source Truth Verification（T0-T3 标注）
   - DoctrineChecker 军规审查
   - Munger 模型应用
   - 用户画像匹配

2. **即使讨论流程/规则本身，也要应用 Munger 和画像**
   - 不能把"机制讨论"当作例外。

3. **区分事实与解释，并标注置信度**
   - 价格数据是事实，因果链是解释。

## 如何应用

- 每次金价/交易分析前，先检查是否已跑 pipeline
- 如果没有，先跑再回答
- 所有外部信息标注来源等级
- 最终输出必须包含军规、Munger、画像三板块

## 相关文件

- CLAUDE.md
- src/gold_miner/advisor/action_guide.py
- src/gold_miner/decision/agents.py
- src/gold_miner/signals/engine.py
- src/gold_miner/doctrine/checker.py
