# Learning: Sourcing Truth Verification

## Date
2026-06-12

## Topic
信息源验证 / 事实核查 / 宏观经济数据引用

## What Happened
在分析2026年6月12日金价拉升原因时，用户指出美国5月CPI已于北京时间6月10日20:30公布，但分析中错误地写成"下周一（6月15日）美国5月CPI数据公布"。

## Root Cause
1. 过度依赖搜索引擎返回的二手摘要
2. 搜索结果本身存在自相矛盾：部分来源正确指出CPI已于6月10日公布，部分来源错误地写成"6月15日CPI"
3. 未访问一手来源（BLS.gov 官方发布日历）做交叉验证
4. 未优先采信用户这个一手知情方的提示

## Impact
- 交易分析中的时间线错误
- 用户对分析可信度产生质疑
- 暴露出"搜索→采信→输出"链条缺少事实校验闸门

## Fix Applied
1. 立即承认错误并更正事件日历
2. 在 `CLAUDE.md` 中新增「信息验证协议（Sourcing Truth Verification）」章节
3. 明确来源可信度层级（T0-T3）和输出标注规范

## Prevention Protocol
- 任何用于交易决策的数据必须至少1个T0来源确认
- 事件日期类信息必须一手来源 + 与用户已知事实交叉
- 搜索摘要仅作为发现入口，不能直接作为事实写入分析
- 输出中标注 `[verified: T0]` / `[verified: T2]` / `[unverified]`

## Related Files
- `/Users/jiangxiaoqiang/Documents/workspace/ai-gold-miner/CLAUDE.md`
- `/Users/jiangxiaoqiang/Documents/workspace/ai-gold-miner/data/trade_log.md`

## Confidence
High — 已写入项目级配置，后续所有分析默认遵循
