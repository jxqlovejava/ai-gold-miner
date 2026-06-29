# Learning: 防止兜底/mock 数据泄漏具体行情数字

Date: 2026-06-29
Trigger: 用户发现报告里出现 947.50 元/克金价，质疑数据来源与正确性。

## 学到的规则

1. **兜底数据不能写入具体价格**
   - `src/gold_miner/cli/report.py` 中网络失败时的 fallback 新闻摘要写死了 "现货黄金单日大跌2.76%至每克947.50元"。
   - 该摘要被 `NewsSignalGenerator` 截取到信号 description， beginner 版报告会渲染出来，极易被误认为真实行情。
   - 真实行情（2026-06-29）为：京东民生积存金 888.96 元/克、SGE Au99.99 883.70 元/克，947.50 严重偏离。

2. **Fallback 常量应提到模块级并加明确注释**
   - 把函数内的硬编码列表提取为 `_FALLBACK_NEWS_ITEMS`，便于单测直接导入验证。
   - 在常量上方加注释说明："必须避免写入具体价格，防止被误认为真实行情"。

3. **正则测试要覆盖标题和摘要，以及多种价格写法**
   - 仅测 summary 不够，title 里也可能出现百分比/价格暗示。
   - 价格模式要覆盖 "947.50元/克"、"每克947.50元"、"¥947.50/克" 等常见写法。

4. **先定位数据真实来源再做修正**
   - 通过 grep 定位到 `cli/report.py:64` 的硬编码 summary，而不是先去猜行情接口出错。
   - 再用 `gold-miner quote` 和 `JdAccumulationGoldFetcher.fetch_price()` 交叉验证真实价格。

## 如何应用

- 所有 fallback/mock 数据在写入前检查是否包含具体价格、日期、百分比等会过期的数字。
- 把兜底数据提取为带注释的模块级常量，并写回归测试锁定不变量。
- 新闻类 fallback 同时检查 title 和 summary，避免任何字段泄漏假行情。
- 用户质疑具体数字时，先用代码定位来源，再用实时接口/官方源交叉验证，最后修复并加测试。

## 相关文件

- `src/gold_miner/cli/report.py`
- `tests/test_cli/test_report.py`
- `src/gold_miner/data/jd_accumulation_gold.py`
- `src/gold_miner/data/accumulation_gold.py`
- `src/gold_miner/signals/news_signal.py`
