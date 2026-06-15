# Learning: 新闻面 Source Truth 与 anysearch fallback pipeline 改造

Date: 2026-06-15
Trigger: 用户指出 anysearch 额度耗尽时应按 CLAUDE.md fallback，并要求把 Source Truth 验证、冲突检测、来源分级等反馈加强到 pipeline。

## 学到的规则

1. **搜索只是发现入口，不是证据**
   - anysearch / WebSearch / DuckDuckGo 结果必须再走 Source Truth 验证
   - 单条搜索信息不能直接写入交易建议

2. **Pipeline 里的 fallback 必须可编程、可追踪**
   - anysearch 配额耗尽时不能返回垃圾条目
   - 应自动切到 DDG/Bing News 多查询，并给每条新闻打 `source_tier`

3. **来源分级域名匹配要用精确/后缀匹配**
   - 子串匹配会把 `anysearch` 误认成 `nyse`，把 `fakebloomberg.com` 误认成 `bloomberg.com`

4. **聚合信号不能拿单条新闻 tier 代表整体**
   - 情感/活跃度信号应使用 `[mixed: Tx]` 或 `[mixed]`，避免误导

5. **冲突检测要限定在明确事件性矛盾**
   - 不能用通用多空词（rise/fall/bullish/bearish）做冲突对，否则同一篇文章都会触发 disputed

6. **预测验证是自我改进的前提**
   - 没有 `actual_price` / 到期结算，`PerformanceAnalyzer` 和 `FindingGenerator` 无法产出改进建议
   - 自动结算只在 prediction 的 `horizon_days` 到期后发生

## 如何应用

- 每次跑 `gold-miner scan` 后检查新闻信号是否带 `[verified: Tx]` / `[disputed]` / `[mixed]`
- 定期用 `gold-miner verify --report` 或 daemon 结算到期预测，积累样本
- 样本足够后运行 `gold-miner review` 和 `gold-miner findings`，按建议调整权重或数据源

## 相关文件

- src/gold_miner/data/source_tiers.py
- src/gold_miner/data/news.py
- src/gold_miner/data/fact_checker.py
- src/gold_miner/signals/news_signal.py
- src/gold_miner/execution/dimensions.py
- tests/test_news_source_truth.py
