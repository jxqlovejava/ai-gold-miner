# 派生风控线手写化 → 换成本后未重算 → 制造假「减仓」信号

**日期**: 2026-09-17
**场景**: 9/17 金价分析，scan 输出 `📌 决策: 减仓`，PM 原因为「价格 930.50 ≤ 次级止损 934.11」。用户随后确认「新增了买入」——portfolio.yaml 的 `avg_cost` 已由 983.27 变为 959.49（反推 9/16 买入 21.5288g @¥929.0 / ¥20,000），但 `secondary_stop` 仍是旧成本派生的 934.11。

**根因**: `hard_stop` / `warn_line` / `secondary_stop` 是 `avg_cost` 的**派生值**，却被当作独立真相源手写在 portfolio.yaml 里。全仓检索确认**没有任何重算方**——两个读取点（`storage/local.py:load_portfolio()` 与 `sentinel/engine.py:_load_portfolio()`，后者直接 `read_text` 绕过前者）都只把 YAML 数字取出来用。于是「改成本」和「改风控线」成为两件必须同时记得做、却无任何机制保证同步的事。

改一次 `avg_cost` 就静默产生一个错误的减仓信号：`position_state.py:195` 的判据 `current_price <= secondary_stop` 变成 `930.50 <= 934.11` 成立；按正确成本派生的 `911.52` 下 `930.50 > 911.52`，**根本不该触发**。

**影响**: 这是**决策级**失真，不是展示级。派生线同时喂给 `position_state`（PM 决策）、`sentinel/engine.py`（盘中告警）、`agent/portfolio.py`（r014 预警线）、`analysis.py`（止损位覆盖）——一条陈旧数字会同时污染分析报告和 Hermes 服务器上的每分钟监控推送。本次触发面积大是因为用户刚加过仓（成本变动），而加仓/减仓恰恰是最需要风控线跟着动的时刻。

**发现路径**（可复用）: 用户否定了我上一轮的一个**错误假设**——我据 `entry_date` 仍是 8/26 就臆断「ATR 位 931.46 是陈旧锚点」。读 `trailing_stop.py:203-213` 后发现：浮亏时走的是 `cost_basis - 3.0×ATR` 的**浮亏轨**，`entry_date`/`highest_high` 只在浮盈轨生效，与当前止损无关。被迫去追 `cost_basis` 的来源，才走到 `avg_cost` 与派生线的失配。**教训：读数异常时不要凭字段语义猜，先把计算式读出来**——排除错误假设的过程本身暴露了真根因。

**处理**（已实施，commit `f34a52c`）:
1. 新增 `src/gold_miner/decision/risk_lines.py` — 把派生关系内化为程序：
   - `derive_risk_lines(avg_cost)` 从成本单一真相源派生三条线
   - `apply_derived_risk_lines()` 在加载点覆盖，不信任 YAML 手写值（不可变，不改入参）
   - `check_risk_line_drift()` 检出漂移并 `logger.warning`
   - 比率可在 `portfolio.yaml: limits.risk_ratios` 覆盖，缺省 -30%/-10%/-5%
2. 接入**两个**读取点（`storage/local.py` + `sentinel/engine.py`）——只接一个会留下另一条绕过路径
3. portfolio.yaml 重算三条线 + 更新拆池（core 21.5288 / tactical 27.6119）
4. 新增 `tests/test_risk_lines.py`（28 项，含事故场景锁定与边界：空/None/非 dict/零成本/容差）
5. 验证：同一时点 scan 复跑，PM 决策由**「减仓」翻转为「持有」**；全量 1279 passed

**改进方向（未实施，待评估）**:
- 同类问题可能存在于其他「手写的派生字段」。已识别候选：`low_buy_bands` 档位与 `conditional_orders.jsonl` 条件单的**手工 diff**（r037 已注明「手动维护，与条件单账本 diff」）——同样是「两处必须同步但无机制保证」的结构。建议后续加一个一致性校验脚本。
- 买入/卖出成交后，除风控线外还有 `entry_date`（r025 锚点）、`last_reduce_at/price`（r036 护栏）需要联动更新，目前全靠人工。可考虑在 `append_trade` 之后加一个「持仓变更后置钩子」统一刷新所有派生字段。
