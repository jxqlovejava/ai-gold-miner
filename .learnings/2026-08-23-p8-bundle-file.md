# P8: bundle 落盘省一轮推理（2026-08-23）

**现象**：金价分析一轮 4m47s，其中 pipeline 仅 7.0s。scan stdout 44KB+ 超工具输出持久化阈值，bundle 被收进 tool-results 文件，模型被迫 grep 定位 + awk 抽取 = 多花 1 轮推理 ≈1min（每轮推理全量重算 ~60k 上下文，kimi-k3 缓存弱，轮次数是唯一可控杠杆）。

**修复**：`quick_scan.sh` 的 `emit_bundle`/`emit_bundle_light` 输出块加 `| tee data/private/.last_bundle.txt`；stdout 超限场景直接 Read 该文件，禁对持久化输出 grep/awk。SKILL.md 铁律 7 已同步。

**规则**：bundle 含 portfolio.yaml / conditional_orders.jsonl 私密数据 → 落盘必须 gitignored 路径（`data/output/` 被 git 跟踪，`/gcp` 自动提交会泄密；`data/private/` 已被 .gitignore 覆盖）。

**配套（上下文瘦身第二轮）**：agents 94→44（gsd-\*/gan-\*/semantica/java/react/typescript/opensource/sales 域归档至 `~/.claude/agents-backup/`）；rules/common 裁到 coding-style/git-workflow/security 三个（余归档 `~/.claude/rules-backup/`，rules 为目录扫描加载，移出即不进上下文）。恢复命令见各自 backup README。下次会话生效。
