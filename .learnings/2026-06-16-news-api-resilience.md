# Learning: 新闻 API 失效时的诊断与容错修复

Date: 2026-06-16
Trigger: 用户发现 `gold-miner scan` 消息面 API 大面积失效（NewsAPI / anysearch / DuckDuckGo / Bing News），要求诊断并修复。

## 学到的规则

1. **先区分「代码 bug」与「外部服务/网络问题」**
   - NewsAPI / DuckDuckGo：ConnectTimeout，属于网络层问题，重试可缓解但无法根治。
   - anysearch：返回 200 但 body 显示 quota exhausted，属于配额问题，不要重试。
   - Bing News：返回 200 但页面被重定向到 `cn.bing.com/` 首页，属于地理/IP 限制，不是 HTML 解析问题。
   - Yahoo Finance：403 Forbidden，属于反爬屏蔽，项目内已有 jinjia.com.cn / AKShare 回退。

2. **重试策略必须区分错误类型**
   - 可重试：timeout、connect error、remote protocol error（SSL EOF 等）、5xx、429。
   - 不可重试：401/403、quota exhausted、内容解析失败。
   - 重试要配指数退避，避免雪崩。

3. **为已知慢/不可靠的端点缩短超时**
   - DuckDuckGo 在当前环境 30s 超时仍失败，3 次重试会阻塞 pipeline 90s。
   - 缩短到 10s + 2 次重试，失败时间控制在 20s 内，给回退留出空间。

4. **回退链要覆盖实际可用的来源**
   - Bing News 在国内环境被重定向到首页，解析为空。
   - 通用 Bing 搜索 (`/search`) 在国内可用且返回 `b_algo` 结果。
   - 因此 Bing News 失败后应自动回退到通用 Bing 搜索，而不是继续重试 Bing News。

5. **避免硬编码会过期的查询字符串**
   - `news.py` 中 NFP 查询硬编码了 `"May 2026"`，6 月后查询会返回过期/空结果。
   - 应使用 `today.strftime('%B %Y')` 动态生成。

6. **长连接池可能导致失效连接复用**
   - NewsFetcher 使用持久化 httpx client，重试时可能复用已断开的连接。
   - 重试前关闭并重建 client，可排除这一因素。

## 如何应用

- 网络请求统一添加重试 helper，区分 retryable / non-retryable error。
- 对外部 API 的响应先判断业务错误（quota、auth）再判断网络错误。
- 对慢/不可用的回退源缩短超时，避免阻塞主流程。
- 设计回退链时，用真实环境测试每个来源是否实际返回可用数据，而非只看 status code。
- 定期检查是否有硬编码日期/月份/年份的查询字符串。
- 持久化 HTTP client 在重试前考虑刷新连接池。

## 相关文件

- `src/gold_miner/data/news.py`
- `src/gold_miner/data/fact_checker.py`
- `src/gold_miner/proxy/manager.py`
- `tests/test_news_source_truth.py`
