# Learning: FRED API SSL 失败的长效兜底方案

Date: 2026-07-04
Trigger: 用户要求「彻底解决 FRED API 再次 SSL 失败问题」，并补充「消息面要输出下具体资讯」。

## 学到的规则

1. **venv Python 3.11/3.13 + OpenSSL 3.x 在 macOS 上容易触发 `SSL: UNEXPECTED_EOF_WHILE_READING`**
   - 这是已知 bug，不是代码逻辑错误
   - 仅把瞬时传输错误（EOF、handshake failure、broken pipe、connection reset）视为可降级；证书域名不匹配/过期等安全错误**不降级**

2. **兜底顺序应把 curl 放在第一位**
   - 旧实现：httpx → system python → node
   - 新实现：httpx 强制 HTTP/1.1 → curl → system python → node
   - curl 在 macOS 上通常最稳，且自带成熟 TLS 栈

3. **强制 HTTP/1.1 能减少 OpenSSL 3.x EOF 触发概率**
   - httpx 默认可能走 HTTP/2，握手阶段更容易触发 EOF
   - `httpx.Client(http1=True)` 是一条低成本防线

4. **retries + 指数退避覆盖偶发失败**
   - `fallback_get(..., retries=2)` 让四层降级最多跑 3 轮
   - 对 FRED 这种间歇性失败非常有效

5. **消息面信号要输出「具体资讯」而非只有抽象分数**
   - 重大事件信号保留完整标题（80 字符内）、摘要、来源、URL
   - 新增「最近新闻资讯」信号，把 top 5 新闻以 `[情感] [来源] 标题` 形式聚合
   - 报告新闻版块展示原文链接

## 如何应用

- 所有外网 GET 调用继续走 `fallback_get()`，不要绕过它直接新建 httpx client
- 若后续仍见某类 SSL 错误没被抓到，优先扩展 `_is_ssl_or_transport_error` 的关键词，而不是再加新 fallback
- 新增/修改信号时，思考下游是否需要「具体资讯」字段，而不是只有 score/direction

## 相关文件

- src/gold_miner/utils/http_fallback.py
- src/gold_miner/signals/news_signal.py
- src/gold_miner/execution/report.py
