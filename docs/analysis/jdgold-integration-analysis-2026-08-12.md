# jdgold Skill × ai-gold-miner 接管/增强深度分析（2026-08-12）

> 基于 jdgold v1.0.0 实测（免登录/需登录分界线以实测为准，非 SKILL.md 宣称）+ 全系统数据获取层盘点。

## 一、能力实测清单

| 能力 | 脚本 | 登录 | 实测 | 证据 |
|---|---|---|---|---|
| 积存金实时价（7行） | query_gold_analysis.py (overview) | 免登录 | ✅ | 民生 958.30 / 浙商 958.23 元/克 |
| 资金炸弹/机会评分/挂单簿 | query_gold_analysis.py | 免登录 | ✅ | 1分钟1154手/5.18亿美元/多空49.2:50.8 |
| SGE Au99.99 实时 OHLC | jdjr_query_gold.py | 免登录 | ✅ | 955.00 元/克 |
| 贵金属日/周/月K（约1年） | jdjr_query_stock.py | 免登录 | ✅ | 2025-08-12 起完整日K |
| 黄金资讯 | jdjr_query_news.py --no-flash | 免登录 | ✅ | 当日 CPI 快讯带链接 |
| 大V排行 | query_blogger_trend.py | 免登录 | ⬜ 未测 | 同网关预计可用 |
| WG-JDAU 24h金价 | query_price_jhub.py | 需登录 | ❌ 实测未登录 | SKILL.md 宣称免登录与实测不符 |
| 快讯合并流 | jdjr_query_news.py (带flash) | 需登录 | ❌ 实测未登录 | |
| 持仓/浮盈/诊断 | holdings_entry.py / query_income_calendar.py | 需登录 | ⬜ | |
| 交易记录 | query_trade_records.py | 需登录 | ⬜ | 只计 COMPLETE/REDEEM_SUCC |
| 条件单(民生/兴业/中信/浙商) | query_conditional_orders.py | 需登录 | ⬜ | 状态 1生效/2触发/3失效/4取消/5完成 |
| 早报 | query_morning_report.py | 需登录 | ⬜ | |
| 模拟盘+托管 | sim_autotrade.py + launchd | 需登录 | ⬜ | 仅模拟金叶子 |

## 架构约束

1. Token 约 8h 且不可自动续期 → 需登录能力只能交互式/每日登录窗口对账，不能进服务器 cron。
2. 免登录能力（积存金价/SGE/K线/资讯/资金流）→ 可直接进 cron，最大价值区。
3. 脚本 cwd 须在 skill 的 scripts/ 目录（jdjr_config.py 同目录）；封装用 subprocess(cwd=...)。
4. 所有脚本支持 --claw 上报客户端类型。

## 二、接管清单（Takeover）

- **T1 积存金实时价收口**：新建 src/gold_miner/data/jdgold_client.py 封装 query_gold_analysis（免登录 CMBC-JCJ），替换 9+ 脚本复制粘贴的两套 H5 端点（getFirstRelatedProductInfo / latestPrice），H5 降 fallback。P0。
- **T2 SGE 实时+历史**：spot_gold.py 实时换 jdjr_query_gold.py；历史换 jdjr_query_stock.py kline（1年日K），替代 jinjia HTML 爬虫 + CSV 手动累积 + akshare 回填。P0-P1。
- **T3 条件单账本**：query_conditional_orders.py --status all 登录窗口对账覆写 conditional_orders.jsonl，军规检查读真实状态。P2。
- **T4 持仓真相源**：holdings_entry.py 对账生成 portfolio.yaml 持仓段（保留止损等本地字段）。估值按各银行分别取价。P2。
- **T5 交易记录**：query_trade_records.py 全量流水生成 trade_log 数据段。P2。

## 三、增强清单（Enhancement）

- **E1 资金炸弹/大单资金流** → 资金流维度（补 COMEX 纯模拟的窟窿，分钟级 vs COT 周频）。P3。
- **E2 机会评分/挂单簿/共振** → 第七步 Agent 博弈输入证据。P3。
- **E3 黄金资讯流** → overnight_news_scanner 官方兜底 + news_monitor 加源。P1。
- **E4 大V排行（加仓/持仓榜）** → 情绪面（黄金垂直散户情绪代理）。P3。
- **E5 早报（需登录）** → 第一步信息准备。P2。
- **E6 持仓诊断四维框架** → 第六步画像匹配参考。P3。
- **E7 模拟盘沙盒** → V9/L1 策略零风险验证（本机）。P3。
- **E8 白银行情** → 金银比联动观察。P3。

## 四、不可替代（保留）

CFTC COT / GLD+ETF / 央行购金 / FRED / 经济日历 / 预测市场 / 八步分析 pipeline（军规+Munger+画像+Agent博弈）/ 微信推送链路 / Hermes 部署。jdgold = 数据+账户层；项目 pipeline = 分析决策层。jdgold 的"机会评分"只作输入证据，不替代军规审查。

## 五、落地路线

- P0：jdgold_client.py 封装免登录接口；JdAccumulationGoldFetcher 主源切换（接口不变，9脚本透明受益）
- P1：adaptive_gold_monitor 主备双源；ATR 历史切官方日K；news_scanner 加 jdgold 兜底
- P2：gold_cmd.py sync 子命令（登录态对账三账本，幂等+失败保留旧账本）；分析第一步前置同步
- P3：资金流/情绪面/模拟盘接入

集成注意：取数据层不继承 C 端话术约束；报告引用按 [verified: T1] 标注。
