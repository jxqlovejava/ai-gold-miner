# Learning: Hermes 微信 iLink 限流根因 + 诊断纪律（先查星期再疑 job 缺失）

Date: 2026-08-22
Trigger: 用户反馈"Hermes 微信推送的金价提醒逻辑有问题"持续修复任务；排查中发现日间新闻 job 今日未运行，误以为调度缺失。

## 学到的规则

1. **Hermes 微信推送失败根因 = 适配器 iLink 限流熔断过于敏感**
   - `gateway/platforms/weixin.py` 默认 `WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD=1`：**一次** iLink 限流即在 30s 窗口内熔断、黑窗 30s，期间所有微信推送全失败
   - 黄金周报(周六10:00)/夜间突发新闻(01:52)/日历(20:08) 等整点整分并发投递 → 撞限流 → 一次失败连累全通道（gateway.log 大量 `iLink sendmessage rate limited; cooldown active for 30.0s`）
   - 修复：systemd drop-in（`~/.config/systemd/user/hermes-gateway.service.d/weixin-rate-tuning.conf`）注入 env 覆盖，**不改适配器代码、Hermes 升级后保留**：
     - `WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD=3`（3 次限流才熔断）
     - `WEIXIN_RATE_LIMIT_CIRCUIT_OPEN_SECONDS=10`（黑窗 30→10s）
     - `WEIXIN_SEND_CHUNK_DELAY_SECONDS=2.5`（长卡片分 chunk 间隔拉大，降瞬时频率）
     - `WEIXIN_SEND_CHUNK_RETRIES=2`（重试减少，避免加重限流）
   - 脚本 `scripts/apply_hermes_weixin_tuning.sh` 幂等可重跑；daemon env 注入需 systemd `Environment=`（`.env` 中 `export` 前缀行会被 systemd 忽略）

2. **诊断"job 未运行"必须先核对星期，再深挖调度器**
   - 现象：日间突发新闻 job（`1,11,.. 9-23 * * 1-5`）今日 Last run 停在昨天 → 疑似调度 bug
   - 真相：今天是**周六**，工作日 job 不跑是正确行为；周报（`0 10 * * 6`）周六 10:00 正常跑、夜间新闻（`0-1 * * 2-6`）周六 01:52 正常跑，全部自洽
   - 教训：把正常行为当异常深挖浪费了约一轮 loop；先 `date` 确认星期（项目"日期查询三步校验"铁律同样适用于运维排查）

3. **增量判断引擎（问题#2 机制缺口）**
   - 旧：新事件不增量影响判断，必须等用户主动全量分析才反应
   - 新：`src/gold_miner/incremental/` 维护持久化基准 `data/private/decision_state.json`，突发新闻/事件结果出现时 LLM 判定 delta（强化/反向/无明显变化），仅实质变化推「⚡金价增量判断」微信卡片，空 stdout 静默防刷屏
   - 每次全量 scan 后 `assemble_report.py` 自动刷新基准 → 增量引擎永远基于最新分析
   - 服务器端：Hermes cron「黄金·增量判断」`*/30 9-23 * * 1-5`（gold_incremental.py wrapper）

## 对当日结论的影响

iLink 修复后 14:21 起 0 次限流（weixin 重连正常）；真实验证待周一（突发新闻/日历/晚间预告恢复推送时观察）。持仓已更新（用户新买入 5.0214g @995.72，net 保本 999.72）。
