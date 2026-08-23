# DuckDuckGo 串行重试白耗 3 分钟 → 进程级熔断

**日期**: 2026-08-23
**修复提交**: `perf: DuckDuckGo抓取加进程级熔断+超时10s→5s`

## 症状
用户感知「第一步耗时 53s+」，实际 `quick_scan.sh` RERUN 总耗时 ~193s：scan 管线本体仅 6.9s，剩余 ~186s 全是深度新闻 8 个主题 × DuckDuckGo 2 次重试 = 16 次超时（每次 ~12s）串行白耗，最终零产出。

## 根因
`SearchEngineFetcher.fetch_from_duckduckgo`（news.py）每次调用独立重试，无"本轮网络不可达"记忆——第 1 个主题已证明 DDG 不可达，后 7 个主题仍各自重试。`fetch_multi` 串行循环放大浪费。

## 修复
1. 类属性 `_ddg_circuit_open`：单主题 2 次网络失败（`_is_retryable_error` 覆盖的 httpx 异常族）即置位，后续主题直接返回 []。
2. 超时 10s→5s（注释本就写明"本环境频繁超时"）。
3. 验证过 `fallback_get` 所有失败终点统一抛 `httpx.ConnectError`（http_fallback.py:469），熔断在真实环境必触发。
4. 不可重试错误（如解析 ValueError）不熔断，避免误伤。

## 可复用模式
- **串行循环 + 外部网络调用 + 重试 = 熔断器必配**。第一个失败已含"网络不可达"全部信息，后续重试是零信息消耗的等待。
- 排查"为什么慢"时先看日志时间戳分段：本次 scan 6.9s vs DDG 186s 的对比一眼定位。
- `_is_retryable_error` 只认 httpx 异常族——测试模拟时必须用 `httpx.TimeoutException` 而非内置 `TimeoutError`，否则走 break 分支误判。

## 后续追因（同日）
16 次超时的根因不是 DDG 故障——当日 09:59 起经 Clash Verge(7897) 实测 302/0.9s、fallback_get 端到端 202/1.6s。是 scan 时段本机代理链路中断：项目 mihomo(17890) 未运行 + 7897 当时无响应。
- 两套子系统端口认知错位：`ProxyManager._detect_available_port` 认 [17890, 7890, 7891, 7897, 9090]；`fallback_get._try_mihomo` 只用其 `get_client`（同套探测，OK），但 Phase 2 直连 `trust_env=False` 刻意绕过 7897（注释称 TLS issues，当日实测已恢复，注释可能过时）。
- `_try_mihomo` 路径不触发项目 mihomo 自启（`get_proxied_client` 才自启）——刻意 fail-fast，保留。
- 结论：代理恢复后抓取自愈；熔断把"代理全挂"场景成本钉死在 ~14s。
