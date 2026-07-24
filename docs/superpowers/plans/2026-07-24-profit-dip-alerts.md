# 止盈/抄底实时提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `adaptive_gold_monitor.py` 中新增止盈/抄底机会提醒：价格触发（急涨破20日新高+浮盈≥5% / 破20日低点 / 关键价位边沿触发）+ 理由引擎（信号快照+RSI+日历证据判定强/中/弱/抑制），告警走 stdout → Hermes → 微信。

**Architecture:** 两级结构。触发层复用每分钟 cron 与现有检测链；理由层仅在触发时运行，全部本地数据秒级组装。信号面证据来自 pipeline 新增的 `data/signal_snapshot.json` 落盘（`SignalBundle.dimension_direction_counts()`）。

**Tech Stack:** Python 3（无新依赖），pytest，项目现有 `gold_miner` 包。

**Spec:** `docs/superpowers/specs/2026-07-24-profit-dip-alerts-design.md`（用户已批准）

## Global Constraints

- 不新增 cron job；不改状态机（NORMAL/WATCHING/ALERT/CRITICAL）升级逻辑。
- 告警 dict 格式与现有检测一致：`{"type": str, "message": str, "severity": str}`，新类型自动进入 `_format_card` 的 `remaining` 展示段，**不改 `_format_card`**。
- 推送通道：stdout 打印卡片 → Hermes → 微信（脚本现有约定，不变）。
- 信号维度计数规则：`insufficient_data` 维度不计入多空对比（用 `dimension_direction_counts()` 的返回值，不手动数）。
- 止盈建议措辞固定针对**机动仓 15g**，核心仓不动。
- 配置：`OPP_DEFAULTS` 顶部常量 + 可选 `data/private/opportunity_config.yaml` 覆盖；**不在仓库创建该 YAML**（gitignored，用户需要时自建）。
- 中文输出；浮盈基准 = `portfolio.yaml` 的 `avg_cost`（不做 lot 级追踪）。
- 测试运行命令统一为 `PYTHONPATH=src python3 -m pytest <path> -v`。

## 与 spec 的偏差（已简化，行为等价或数据所限）

1. 关键价位不设 240 分钟冷却——纯边沿触发已保证每轮入带只提醒一次（spec §6 中该冷却冗余）。
2. 不计算 ATR——历史序列只有收盘价无 high/low；理由层用 RSI(14)+MA20/MA60。
3. spec §7 快照示例中的 `dimensions` 数组省略——理由引擎只用维度计数。
4. `main()` 历史获取从 `_get_historical(days=30)` 改为 `days=90`（MA60 需要）。

---

### Task 1: 信号快照落盘模块

**Files:**
- Create: `src/gold_miner/signals/snapshot.py`
- Modify: `src/gold_miner/pipeline/analysis.py:965-968`（在维度方向汇总日志后追加落盘调用）
- Test: `tests/test_signal_snapshot.py`

**Interfaces:**
- Produces: `save_signal_snapshot(bundle, current_price: float, path: Path = SNAPSHOT_PATH) -> None`
  - bundle 只需有 `dimension_direction_counts() -> tuple[int, int, int]`（duck-typed，测试用假对象）。
  - 写出 JSON：`{"timestamp", "current_price", "bull_dims", "bear_dims", "insufficient_dims", "direction_clarity"}`，clarity 规则：`bull-bear>=2 → "bullish"`，`bear-bull>=2 → "bearish"`，否则 `"mixed"`。
- Consumed by: Task 4 的 `_load_signal_snapshot`（监控脚本读取同一 JSON 字段名）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_signal_snapshot.py`：

```python
"""信号快照落盘测试."""
import json
from pathlib import Path

from gold_miner.signals.snapshot import save_signal_snapshot


class _FakeBundle:
    def __init__(self, counts):
        self._counts = counts

    def dimension_direction_counts(self):
        return self._counts


def _write(tmp_path: Path, counts) -> dict:
    target = tmp_path / "signal_snapshot.json"
    save_signal_snapshot(_FakeBundle(counts), 894.5, path=target)
    return json.loads(target.read_text(encoding="utf-8"))


def test_clarity_bullish(tmp_path):
    snap = _write(tmp_path, (4, 1, 1))
    assert snap["direction_clarity"] == "bullish"
    assert snap["bull_dims"] == 4
    assert snap["bear_dims"] == 1
    assert snap["insufficient_dims"] == 1
    assert snap["current_price"] == 894.5
    assert "timestamp" in snap


def test_clarity_bearish(tmp_path):
    snap = _write(tmp_path, (1, 4, 0))
    assert snap["direction_clarity"] == "bearish"


def test_clarity_mixed_when_close(tmp_path):
    # 4:4 与 4:3 都是方向不明
    assert _write(tmp_path, (4, 4, 0))["direction_clarity"] == "mixed"
    assert _write(tmp_path, (4, 3, 0))["direction_clarity"] == "mixed"


def test_creates_parent_dir(tmp_path):
    target = tmp_path / "sub" / "dir" / "signal_snapshot.json"
    save_signal_snapshot(_FakeBundle((2, 2, 0)), 900.0, path=target)
    assert target.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_signal_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: gold_miner.signals.snapshot`

- [ ] **Step 3: 实现快照模块**

创建 `src/gold_miner/signals/snapshot.py`：

```python
"""信号快照落盘 — 供 adaptive_gold_monitor 理由引擎读取最近一次 pipeline 维度方向."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SNAPSHOT_PATH = Path("data/signal_snapshot.json")

_BEIJING = timezone(timedelta(hours=8))


def save_signal_snapshot(bundle, current_price: float, path: Path = SNAPSHOT_PATH) -> None:
    """把 SignalBundle 的维度方向计数落盘为 JSON.

    bundle: 任何有 dimension_direction_counts() -> (bull, bear, insufficient) 的对象.
    """
    bull, bear, insuf = bundle.dimension_direction_counts()
    if bull - bear >= 2:
        clarity = "bullish"
    elif bear - bull >= 2:
        clarity = "bearish"
    else:
        clarity = "mixed"
    payload = {
        "timestamp": datetime.now(_BEIJING).isoformat(),
        "current_price": float(current_price),
        "bull_dims": bull,
        "bear_dims": bear,
        "insufficient_dims": insuf,
        "direction_clarity": clarity,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src python3 -m pytest tests/test_signal_snapshot.py -v`
Expected: 4 passed

- [ ] **Step 5: 接入 analysis.py**

`src/gold_miner/pipeline/analysis.py` 在第 965-968 行（`维度方向汇总` 日志块）之后追加：

```python
        # --- 4b. 信号快照落盘 (供 adaptive_gold_monitor 理由引擎读取) ---
        try:
            from gold_miner.signals.snapshot import save_signal_snapshot
            save_signal_snapshot(bundle, getattr(result, "current_price", 0.0))
        except Exception as e:
            logger.warning(f"[3/9] 信号快照落盘失败: {e}")
```

锚点（插入位置之前的现有代码）：

```python
        bull_dims, bear_dims, insuf_dims = bundle.dimension_direction_counts()
        logger.info(
            f"  维度方向汇总: {bull_dims}维看多 | {bear_dims}维看空 | {insuf_dims}维数据不足"
        )
```

- [ ] **Step 6: 验证 analysis.py 语法 + 快照测试仍过**

Run: `PYTHONPATH=src python3 -c "import gold_miner.pipeline.analysis" && PYTHONPATH=src python3 -m pytest tests/test_signal_snapshot.py -q`
Expected: 无输出错误；4 passed

- [ ] **Step 7: Commit**

```bash
git add src/gold_miner/signals/snapshot.py tests/test_signal_snapshot.py src/gold_miner/pipeline/analysis.py
git commit -m "feat: 信号快照落盘 — pipeline维度方向写入data/signal_snapshot.json"
```

---

### Task 2: 监控脚本 — 机会配置、技术指标、surge 方向字段

**Files:**
- Modify: `scripts/adaptive_gold_monitor.py`（配置区、DEFAULT_STATE、检测逻辑区、`_check_surge`）
- Test: `tests/test_adaptive_opportunity.py`（本任务先建文件与首批用例）

**Interfaces:**
- Produces（后续任务依赖的签名）:
  - `OPP_DEFAULTS: dict`、`OPP_CONFIG_PATH: Path`、`SIGNAL_SNAPSHOT_PATH: Path`、`ORDERS_PATH: Path`
  - `_load_opp_config() -> dict`
  - `_rsi(closes: list[float], period: int = 14) -> float | None`
  - `_ma(closes: list[float], window: int) -> float | None`
  - `_check_surge` 返回 dict 新增 `"direction": "up"|"down"` 与 `"change_pct": float` 键
- DEFAULT_STATE 新增键：`tp_alert_at`、`tp_alert_price`、`dip_alert_at`、`dip_alert_price`、`in_band_levels`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_adaptive_opportunity.py`：

```python
"""止盈/抄底机会提醒测试 — scripts/adaptive_gold_monitor.py."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import adaptive_gold_monitor as m  # noqa: E402

BEIJING = m.BEIJING


def _cfg(**over):
    cfg = dict(m.OPP_DEFAULTS)
    cfg.update(over)
    return cfg


def _hist(closes):
    return [{"date": f"2026-06-{i+1:02d}", "close": c} for i, c in enumerate(closes)]


# ── surge 方向字段 ──

def test_surge_has_direction_up():
    state = {"last_price": 900.0}
    surge = m._check_surge(905.0, state)  # +0.56%
    assert surge is not None
    assert surge["direction"] == "up"
    assert surge["change_pct"] > 0


def test_surge_has_direction_down():
    state = {"last_price": 900.0}
    surge = m._check_surge(895.0, state)  # -0.56%
    assert surge is not None
    assert surge["direction"] == "down"


# ── RSI / MA ──

def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 21)]
    assert m._rsi(closes) == 100.0


def test_rsi_all_losses_is_0():
    closes = [float(100 - i) for i in range(20)]
    assert m._rsi(closes) == 0.0


def test_rsi_insufficient_data():
    assert m._rsi([1.0, 2.0, 3.0]) is None


def test_ma_basic():
    assert m._ma([1.0, 2.0, 3.0, 4.0], 4) == 2.5
    assert m._ma([1.0, 2.0], 20) is None


# ── 配置覆盖 ──

def test_load_opp_config_defaults():
    cfg = m._load_opp_config()
    assert cfg["breakout_lookback_days"] == 20
    assert cfg["min_profit_pct"] == 0.05
    assert cfg["require_surge"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: FAIL — `AssertionError`（surge 无 direction 键）与 `AttributeError: module has no attribute 'OPP_DEFAULTS'`

- [ ] **Step 3: 加配置区常量与状态默认值**

`scripts/adaptive_gold_monitor.py` 在 `ORDERS` 无关的现有路径常量块（第 107-115 行 `LOG_FILE` 之后）追加：

```python
# ═══════════════════════════════════════════════════════════════
# 机会提醒配置 (止盈/抄底) — 可选 data/private/opportunity_config.yaml 覆盖
# ═══════════════════════════════════════════════════════════════

OPP_DEFAULTS: dict = {
    "require_surge": True,          # 止盈是否需要急涨速度条件
    "breakout_lookback_days": 20,   # N日新高窗口
    "min_profit_pct": 0.05,         # 浮盈阈值
    "dip_lookback_days": 20,        # N日低点窗口
    "key_levels": [921.0, 850.0],   # 元/克; 921≈$4000/oz (USD/CNY≈7.16)
    "key_level_band_pct": 0.01,     # 关键价位带宽 ±1%
    "cooldown_take_profit_min": 60,
    "cooldown_dip_low_min": 60,
    "realert_move_pct": 0.01,       # 冷却内同向再走1%可再提醒
    "snapshot_stale_hours": 48,     # 信号快照过期阈值
}
OPP_CONFIG_PATH = PROJECT_ROOT / "data/private/opportunity_config.yaml"
SIGNAL_SNAPSHOT_PATH = PROJECT_ROOT / "data/signal_snapshot.json"
ORDERS_PATH = PROJECT_ROOT / "data/private/conditional_orders.jsonl"


def _load_opp_config() -> dict:
    """顶部默认值 + 可选 YAML 覆盖 (只覆盖已知键)."""
    cfg = dict(OPP_DEFAULTS)
    if OPP_CONFIG_PATH.exists():
        try:
            import yaml
            user_cfg = yaml.safe_load(OPP_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(user_cfg, dict):
                cfg.update({k: v for k, v in user_cfg.items() if k in cfg})
        except Exception:
            pass
    return cfg
```

`DEFAULT_STATE`（第 201-213 行）末尾追加键：

```python
    # 机会提醒 (止盈/抄底)
    "tp_alert_at": None,
    "tp_alert_price": None,
    "dip_alert_at": None,
    "dip_alert_price": None,
    "in_band_levels": [],
```

- [ ] **Step 4: 加技术指标函数**

在 `_check_rebound` 之后（检测逻辑区末尾，约第 474 行后）追加：

```python
def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI; 数据不足返回 None."""
    if len(closes) < period + 1:
        return None
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            avg_gain += d
        else:
            avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _ma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window
```

- [ ] **Step 5: `_check_surge` 返回方向字段**

替换 `_check_surge`（第 391-405 行）的 return 块为：

```python
def _check_surge(current: float, state: dict) -> dict | None:
    """价格急变检测 (继承 price_surge_monitor 逻辑)."""
    last_price = state.get("last_price")
    if not last_price:
        return None
    change_pct = (current - last_price) / last_price * 100
    if abs(change_pct) >= 0.5:
        direction = "up" if change_pct > 0 else "down"
        return {
            "type": "price_surge",
            "direction": direction,
            "change_pct": round(change_pct, 2),
            "message": f"{'📈' if direction == 'up' else '📉'} 价格{'急涨' if direction == 'up' else '急跌'}! "
                       f"{last_price:.0f}→{current:.0f} ({change_pct:+.2f}%)",
            "severity": "CRITICAL" if abs(change_pct) > 1.0 else "HIGH",
        }
    return None
```

- [ ] **Step 6: 跑测试确认通过 + 回归**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: 8 passed

- [ ] **Step 7: Commit**

```bash
git add scripts/adaptive_gold_monitor.py tests/test_adaptive_opportunity.py
git commit -m "feat: 机会提醒基础 — 配置加载+RSI/MA指标+surge方向字段"
```

---

### Task 3: 触发层检测（止盈候选 + 抄底候选）

**Files:**
- Modify: `scripts/adaptive_gold_monitor.py`（检测逻辑区，接 Task 2 的指标函数之后）
- Test: `tests/test_adaptive_opportunity.py`（追加用例）

**Interfaces:**
- Produces:
  - `_check_take_profit_breakout(current: float, historical: list[dict], cost_basis: float | None, cfg: dict, surge: dict | None) -> dict | None` — 返回 `{"type": "take_profit_breakout", "high_n": float, "lookback": int, "profit_pct": float}`
  - `_check_dip_buy_opportunity(current: float, state: dict, historical: list[dict], cfg: dict) -> dict | None` — 返回 `{"type": "dip_buy_opportunity", "broke_low": bool, "low_n": float, "lookback": int, "key_level": float | None}`；每次调用更新 `state["in_band_levels"]`
- Consumes: Task 2 的 `cfg` 键与 surge 的 `direction`。

- [ ] **Step 1: 写失败测试（追加到 tests/test_adaptive_opportunity.py）**

```python
# ── 止盈候选: 三条件同时 ──

def _surge_up():
    return {"type": "price_surge", "direction": "up", "change_pct": 0.6,
            "message": "x", "severity": "HIGH"}


def test_tp_all_conditions_trigger():
    hist = _hist([880.0] * 25)
    cand = m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), _surge_up())
    assert cand is not None
    assert cand["type"] == "take_profit_breakout"
    assert cand["high_n"] == 880.0
    assert cand["profit_pct"] == pytest.approx(50 / 890, rel=1e-3)


def test_tp_blocked_without_surge():
    hist = _hist([880.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), None) is None
    down = {"type": "price_surge", "direction": "down", "change_pct": -0.6}
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), down) is None


def test_tp_blocked_without_new_high():
    hist = _hist([950.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), _surge_up()) is None


def test_tp_blocked_without_profit():
    hist = _hist([880.0] * 25)
    # 现价885破新高, 但成本870 → 浮盈1.7% < 5%
    assert m._check_take_profit_breakout(885.0, hist, 870.0, _cfg(), _surge_up()) is None


def test_tp_blocked_without_cost_or_history():
    hist = _hist([880.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, None, _cfg(), _surge_up()) is None
    assert m._check_take_profit_breakout(940.0, _hist([880.0] * 5), 890.0, _cfg(), _surge_up()) is None


def test_tp_surge_optional_when_config_off():
    hist = _hist([880.0] * 25)
    cand = m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(require_surge=False), None)
    assert cand is not None


# ── 抄底候选: 破低点 / 关键价位边沿触发 ──

def test_dip_broke_low_triggers():
    hist = _hist([880.0] * 25)
    state = {"in_band_levels": []}
    cand = m._check_dip_buy_opportunity(870.0, state, hist, _cfg())
    assert cand is not None
    assert cand["broke_low"] is True
    assert cand["low_n"] == 880.0
    assert cand["key_level"] is None


def test_dip_key_level_edge_trigger_once():
    hist = _hist([800.0] * 25)  # 低点远离, 不触发破低点
    state = {"in_band_levels": []}
    # 进入 921±1% 带 (911.79-930.21)
    cand = m._check_dip_buy_opportunity(920.0, state, hist, _cfg())
    assert cand is not None
    assert cand["key_level"] == 921.0
    assert cand["broke_low"] is False
    assert state["in_band_levels"] == [921.0]
    # 带内横盘 → 不再触发
    assert m._check_dip_buy_opportunity(921.0, state, hist, _cfg()) is None
    # 出带 → 重置
    assert m._check_dip_buy_opportunity(940.0, state, hist, _cfg()) is None
    assert state["in_band_levels"] == []
    # 再入带 → 再次触发
    cand2 = m._check_dip_buy_opportunity(919.0, state, hist, _cfg())
    assert cand2 is not None and cand2["key_level"] == 921.0


def test_dip_no_condition_no_trigger():
    hist = _hist([870.0] * 25)
    state = {"in_band_levels": []}
    assert m._check_dip_buy_opportunity(880.0, state, hist, _cfg()) is None


def test_dip_short_history_skipped():
    state = {"in_band_levels": []}
    assert m._check_dip_buy_opportunity(870.0, state, _hist([880.0] * 5), _cfg()) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: FAIL — `AttributeError: module has no attribute '_check_take_profit_breakout'`

- [ ] **Step 3: 实现两个检测函数**

在 `_ma` 之后追加：

```python
def _check_take_profit_breakout(
    current: float,
    historical: list[dict],
    cost_basis: float | None,
    cfg: dict,
    surge: dict | None,
) -> dict | None:
    """止盈候选: 急涨 + 破N日新高 + 浮盈≥阈值, 三条件同时."""
    if cost_basis is None or len(historical) < 10:
        return None
    if cfg["require_surge"]:
        if not surge or surge.get("direction") != "up":
            return None
    lookback = int(cfg["breakout_lookback_days"])
    window = historical[-lookback:] if len(historical) >= lookback else list(historical)
    high_n = max(p["close"] for p in window)
    if current <= high_n:
        return None
    profit_pct = (current - cost_basis) / cost_basis
    if profit_pct < cfg["min_profit_pct"]:
        return None
    return {
        "type": "take_profit_breakout",
        "high_n": high_n,
        "lookback": lookback,
        "profit_pct": profit_pct,
    }


def _check_dip_buy_opportunity(
    current: float,
    state: dict,
    historical: list[dict],
    cfg: dict,
) -> dict | None:
    """买入候选: 破N日低点 或 边沿进入关键价位带 (带外→带内才触发).

    每次调用都把 state["in_band_levels"] 更新为当前在带内的价位列表.
    """
    if len(historical) < 10:
        return None
    band = cfg["key_level_band_pct"]
    levels = [float(lv) for lv in cfg["key_levels"]]
    in_band_now = [lv for lv in levels if abs(current - lv) / lv <= band]
    prev_in_band = [float(lv) for lv in state.get("in_band_levels", [])]
    entered = [lv for lv in in_band_now if lv not in prev_in_band]
    state["in_band_levels"] = in_band_now

    lookback = int(cfg["dip_lookback_days"])
    window = historical[-lookback:] if len(historical) >= lookback else list(historical)
    low_n = min(p["close"] for p in window)
    broke_low = current < low_n

    if not broke_low and not entered:
        return None
    return {
        "type": "dip_buy_opportunity",
        "broke_low": broke_low,
        "low_n": low_n,
        "lookback": lookback,
        "key_level": entered[0] if entered else None,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/adaptive_gold_monitor.py tests/test_adaptive_opportunity.py
git commit -m "feat: 止盈/抄底触发检测 — 三条件止盈候选+边沿触发关键价位"
```

---

### Task 4: 理由引擎（证据包 + 强度判定）

**Files:**
- Modify: `scripts/adaptive_gold_monitor.py`（接 Task 3 函数之后）
- Test: `tests/test_adaptive_opportunity.py`（追加用例）

**Interfaces:**
- Produces:
  - `_load_signal_snapshot(cfg: dict) -> dict | None` — 返回 `{"bull": int, "bear": int, "clarity": str, "age_h": float, "timestamp": str}`；缺失/损坏/>48h/无有效维度 → None
  - `_gather_evidence(current: float, historical: list[dict], cfg: dict) -> dict` — 返回 `{"rsi14", "ma20", "ma60", "snapshot", "events": list, "active_orders": list[str]}`
  - `_evaluate_reason(action: str, candidate: dict, ev: dict, cfg: dict) -> dict` — 返回 `{"strength": "strong"|"medium"|"weak"|"veto", "reasons": list[str], "veto_note": str}`；action ∈ {"take_profit", "dip_buy"}
- Consumes: Task 1 快照 JSON 字段名；Task 2 的 `_rsi`/`_ma`/`cfg`；Task 3 的 candidate dict。

- [ ] **Step 1: 写失败测试（追加）**

```python
# ── 理由引擎 ──

def _snap(bull, bear, clarity):
    return {"bull": bull, "bear": bear, "clarity": clarity,
            "age_h": 2.0, "timestamp": "2026-07-24T09:30:00+08:00"}


def _ev(snapshot=None, rsi=55.0, events=None, orders=None):
    return {"rsi14": rsi, "ma20": 890.0, "ma60": 885.0,
            "snapshot": snapshot, "events": events or [],
            "active_orders": orders or []}


def test_reason_tp_mixed_is_strong():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(4, 4, "mixed")), _cfg())
    assert v["strength"] == "strong"
    assert any("方向不明" in r for r in v["reasons"])


def test_reason_tp_bearish_is_strong():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(2, 5, "bearish")), _cfg())
    assert v["strength"] == "strong"
    assert any("信号转空" in r for r in v["reasons"])


def test_reason_tp_bullish_veto():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(5, 2, "bullish"), rsi=55.0), _cfg())
    assert v["strength"] == "veto"
    assert "未触发止盈建议" in v["veto_note"]


def test_reason_tp_bullish_but_overbought_is_medium():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(5, 2, "bullish"), rsi=75.0), _cfg())
    assert v["strength"] == "medium"
    assert any("超买" in r for r in v["reasons"])


def test_reason_tp_missing_snapshot_is_weak():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=None), _cfg())
    assert v["strength"] == "weak"


def test_reason_dip_bearish_veto():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": None}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(1, 5, "bearish")), _cfg())
    assert v["strength"] == "veto"
    assert "支撑未确认" in v["veto_note"]


def test_reason_dip_resonance_oversold_strong():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": 921.0}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), rsi=28.0), _cfg())
    assert v["strength"] == "strong"
    assert any("共振" in r for r in v["reasons"])
    assert any("超卖" in r for r in v["reasons"])


def test_reason_dip_single_condition_medium():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": None}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), rsi=45.0), _cfg())
    assert v["strength"] == "medium"


def test_reason_event_caution_on_dip():
    cand = {"broke_low": False, "low_n": 800.0, "lookback": 20, "key_level": 850.0}
    events = [{"name": "FOMC决议", "time": "07-25 02:00", "impact": "high"}]
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), events=events), _cfg())
    assert any("不接飞刀" in r for r in v["reasons"])


# ── 快照读取 ──

def test_load_snapshot_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", tmp_path / "nope.json")
    assert m._load_signal_snapshot(_cfg()) is None


def test_load_snapshot_stale(tmp_path, monkeypatch):
    import json as _json
    old = (datetime.now(BEIJING) - timedelta(hours=72)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": old, "bull_dims": 4, "bear_dims": 4,
                              "direction_clarity": "mixed"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    assert m._load_signal_snapshot(_cfg()) is None


def test_load_snapshot_fresh(tmp_path, monkeypatch):
    import json as _json
    fresh = (datetime.now(BEIJING) - timedelta(hours=2)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": fresh, "bull_dims": 5, "bear_dims": 2,
                              "direction_clarity": "bullish"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    snap = m._load_signal_snapshot(_cfg())
    assert snap is not None
    assert snap["clarity"] == "bullish"
    assert snap["bull"] == 5


def test_load_snapshot_zero_active_dims(tmp_path, monkeypatch):
    import json as _json
    fresh = (datetime.now(BEIJING) - timedelta(hours=1)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": fresh, "bull_dims": 0, "bear_dims": 0,
                              "direction_clarity": "mixed"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    assert m._load_signal_snapshot(_cfg()) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: FAIL — `AttributeError: module has no attribute '_evaluate_reason'`

- [ ] **Step 3: 实现理由引擎**

在 Task 3 函数之后追加：

```python
def _load_signal_snapshot(cfg: dict) -> dict | None:
    """读取 pipeline 信号快照; 缺失/损坏/超时/无有效维度 → None."""
    if not SIGNAL_SNAPSHOT_PATH.exists():
        return None
    try:
        snap = json.loads(SIGNAL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(snap["timestamp"])
        age_h = (_now() - ts).total_seconds() / 3600
        if age_h > cfg["snapshot_stale_hours"]:
            return None
        bull = int(snap.get("bull_dims", 0))
        bear = int(snap.get("bear_dims", 0))
        if bull + bear == 0:
            return None
        return {
            "bull": bull,
            "bear": bear,
            "clarity": snap.get("direction_clarity", "mixed"),
            "age_h": age_h,
            "timestamp": snap["timestamp"],
        }
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _gather_evidence(current: float, historical: list[dict], cfg: dict) -> dict:
    """理由引擎证据包: 技术面 + 信号快照 + 48h事件 + 活跃条件单. 各源失败独立降级."""
    closes = [p["close"] for p in historical] + [current]
    ev: dict = {
        "rsi14": _rsi(closes),
        "ma20": _ma(closes, 20),
        "ma60": _ma(closes, 60),
        "snapshot": _load_signal_snapshot(cfg),
        "events": [],
        "active_orders": [],
    }
    try:
        from gold_miner.data.calendar import EventCalendar, EventImpact
        cal = EventCalendar()
        ev["events"] = [
            {"name": e.name, "time": e.beijing_time_str, "impact": e.impact.value}
            for e in cal.get_upcoming(days=2, min_impact=EventImpact.MEDIUM)[:3]
        ]
    except Exception:
        pass
    try:
        if ORDERS_PATH.exists():
            for line in ORDERS_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if o.get("status") != "active":
                    continue
                if o.get("type") == "oco" and o.get("oco"):
                    tp = o["oco"]["take_profit"]["price"]
                    sl = o["oco"]["stop_loss"]["price"]
                    ev["active_orders"].append(f"OCO止盈{tp:.0f}/止损{sl:.0f}")
                else:
                    ev["active_orders"].append(
                        f"{o.get('direction', '?')}@{o.get('trigger_price', 0):.0f}"
                    )
    except Exception:
        pass
    return ev


def _evaluate_reason(action: str, candidate: dict, ev: dict, cfg: dict) -> dict:
    """理由强度判定: strong/medium/weak/veto."""
    snap = ev.get("snapshot")
    rsi = ev.get("rsi14")
    events = ev.get("events") or []

    if snap is None:
        return {
            "strength": "weak",
            "reasons": ["无近期信号快照(>48h或未运行pipeline)，理由弱，请自行确认"],
            "veto_note": "",
        }

    bull, bear, clarity = snap["bull"], snap["bear"], snap["clarity"]
    snap_line = f"信号快照({snap['timestamp'][5:16].replace('T', ' ')})：多{bull}维 空{bear}维"
    reasons: list[str] = []

    if action == "take_profit":
        if clarity == "bullish" and (rsi is None or rsi < 70):
            return {
                "strength": "veto",
                "reasons": [],
                "veto_note": f"{snap_line}，方向仍偏多，未触发止盈建议",
            }
        if clarity == "mixed":
            reasons.append(f"{snap_line}，方向不明，落袋为安")
            strength = "strong"
        elif clarity == "bearish":
            reasons.append(f"{snap_line}，信号转空，反弹止盈")
            strength = "strong"
        else:
            reasons.append(f"{snap_line}，偏多但超买")
            strength = "medium"
        if rsi is not None and rsi >= 70:
            reasons.append(f"RSI(14)={rsi:.0f}，超买区")
        if events:
            names = "、".join(e["name"] for e in events[:2])
            reasons.append(f"48h内事件：{names}，数据前落袋符合军规")
        return {"strength": strength, "reasons": reasons, "veto_note": ""}

    # action == "dip_buy"
    if clarity == "bearish":
        return {
            "strength": "veto",
            "reasons": [],
            "veto_note": f"{snap_line}，信号仍偏空，支撑未确认，未触发买入建议",
        }
    resonance = bool(candidate.get("broke_low")) and candidate.get("key_level") is not None
    if resonance and rsi is not None and rsi < 30:
        reasons.append(
            f"破{candidate['lookback']}日低点与关键价位{candidate['key_level']:.0f}共振"
        )
        reasons.append(f"RSI(14)={rsi:.0f}，超卖区")
        strength = "strong"
    else:
        strength = "medium"
        reasons.append(snap_line)
        if rsi is not None and rsi < 30:
            reasons.append(f"RSI(14)={rsi:.0f}，超卖区")
    if events:
        names = "、".join(e["name"] for e in events[:2])
        reasons.append(f"⚠️ 48h内事件：{names}，数据前不接飞刀，建议等落地")
    return {"strength": strength, "reasons": reasons, "veto_note": ""}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: 31 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/adaptive_gold_monitor.py tests/test_adaptive_opportunity.py
git commit -m "feat: 理由引擎 — 证据包组装+强/中/弱/抑制判定"
```

---

### Task 5: main() 集成 + 冷却/再提醒 + 告警构造

**Files:**
- Modify: `scripts/adaptive_gold_monitor.py`（新增 `_opp_cooldown_ok`/`_build_opp_alert`；`main()` 第 789 行历史天数、第 821-823 行后插入 4g 块）
- Test: `tests/test_adaptive_opportunity.py`（追加用例）

**Interfaces:**
- Produces:
  - `_opp_cooldown_ok(state: dict, prefix: str, current: float, direction: str, cfg: dict) -> bool` — prefix ∈ {"tp", "dip"}；direction ∈ {"up", "down"}
  - `_build_opp_alert(action: str, candidate: dict, verdict: dict, ev: dict, current: float, cost_basis: float | None) -> dict` — 返回标准 alert dict
- Consumes: Task 3 candidate、Task 4 verdict/evidence。
- main() 集成的状态写入：告警（含 veto）发出时写 `state["tp_alert_at"]`/`state["tp_alert_price"]`（或 dip 对应键），值为 `_now().isoformat()` 与 `current`。

- [ ] **Step 1: 写失败测试（追加）**

```python
# ── 冷却/再提醒 ──

def test_cooldown_blocks_within_window():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "tp_alert_price": 900.0}
    assert m._opp_cooldown_ok(state, "tp", 905.0, "up", _cfg()) is False


def test_cooldown_passes_after_window():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=90)).isoformat(),
             "tp_alert_price": 900.0}
    assert m._opp_cooldown_ok(state, "tp", 905.0, "up", _cfg()) is True


def test_cooldown_realert_on_further_rise():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "tp_alert_price": 900.0}
    # 900→910 = +1.11% ≥ 1% → 再提醒
    assert m._opp_cooldown_ok(state, "tp", 910.0, "up", _cfg()) is True


def test_cooldown_realert_on_further_drop():
    state = {"dip_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "dip_alert_price": 900.0}
    # 900→890 = -1.11% → 再提醒
    assert m._opp_cooldown_ok(state, "dip", 890.0, "down", _cfg()) is True


def test_cooldown_first_time_ok():
    assert m._opp_cooldown_ok({}, "tp", 900.0, "up", _cfg()) is True


# ── 告警构造 ──

def test_build_tp_alert_strong():
    cand = {"lookback": 20, "high_n": 900.0, "profit_pct": 0.062}
    verdict = {"strength": "strong", "reasons": ["信号快照：多4 空4，方向不明，落袋为安"],
               "veto_note": ""}
    alert = m._build_opp_alert("take_profit", cand, verdict, _ev(), 945.0, 890.0)
    assert alert["type"] == "take_profit_breakout"
    assert alert["severity"] == "HIGH"
    assert "机动仓15g" in alert["message"]
    assert "理由强度: 强" in alert["message"]


def test_build_tp_alert_veto():
    cand = {"lookback": 20, "high_n": 900.0, "profit_pct": 0.062}
    verdict = {"strength": "veto", "reasons": [], "veto_note": "信号快照：多5空2，方向仍偏多，未触发止盈建议"}
    alert = m._build_opp_alert("take_profit", cand, verdict, _ev(), 945.0, 890.0)
    assert alert["type"] == "take_profit_vetoed"
    assert "未触发止盈建议" in alert["message"]


def test_build_dip_alert_resonance():
    cand = {"broke_low": True, "low_n": 915.0, "lookback": 20, "key_level": 921.0}
    verdict = {"strength": "strong", "reasons": ["破20日低点与关键价位921共振"], "veto_note": ""}
    alert = m._build_opp_alert("dip_buy", cand, verdict, _ev(), 918.0, 894.25)
    assert alert["type"] == "dip_buy_opportunity"
    assert "共振" in alert["message"]
    assert "理由强度: 强" in alert["message"]


def test_build_dip_alert_key_level_only():
    cand = {"broke_low": False, "low_n": 800.0, "lookback": 20, "key_level": 850.0}
    verdict = {"strength": "medium", "reasons": ["信号快照：多4空4"], "veto_note": ""}
    alert = m._build_opp_alert("dip_buy", cand, verdict, _ev(), 852.0, 894.25)
    assert "关键价位 850" in alert["message"]
    assert "理由强度: 中" in alert["message"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: FAIL — `AttributeError: module has no attribute '_opp_cooldown_ok'`

- [ ] **Step 3: 实现冷却与告警构造**

在 `_evaluate_reason` 之后追加：

```python
def _opp_cooldown_ok(state: dict, prefix: str, current: float, direction: str, cfg: dict) -> bool:
    """冷却判定: 过冷却期 或 冷却内同向价格再走 realert_move_pct."""
    last_at = state.get(f"{prefix}_alert_at")
    if not last_at:
        return True
    try:
        elapsed_min = (_now() - datetime.fromisoformat(last_at)).total_seconds() / 60
    except (ValueError, TypeError):
        return True
    minutes = cfg["cooldown_take_profit_min"] if prefix == "tp" else cfg["cooldown_dip_low_min"]
    if elapsed_min >= minutes:
        return True
    last_price = state.get(f"{prefix}_alert_price")
    if last_price:
        move = (current - last_price) / last_price
        if direction == "up" and move >= cfg["realert_move_pct"]:
            return True
        if direction == "down" and move <= -cfg["realert_move_pct"]:
            return True
    return False


def _build_opp_alert(
    action: str,
    candidate: dict,
    verdict: dict,
    ev: dict,
    current: float,
    cost_basis: float | None,
) -> dict:
    """构造机会提醒告警 (含📋理由节). veto 时返回 vetoed 类型."""
    strength = verdict["strength"]
    lines: list[str] = []

    if action == "take_profit":
        if strength == "veto":
            return {
                "type": "take_profit_vetoed",
                "message": f"🔕 急涨破{candidate['lookback']}日新高，但{verdict['veto_note']}",
                "severity": "INFO",
            }
        profit_pct = candidate["profit_pct"] * 100
        lines.append(
            f"🎯 止盈机会 | 急涨破{candidate['lookback']}日新高 "
            f"{candidate['high_n']:.0f}→{current:.2f}，浮盈{profit_pct:+.1f}%"
        )
        lines.append("   💡 建议: 卖出机动仓15g的1/3~1/2锁定利润，核心仓不动")
        lines.append("   📏 纪律: 卖出后若继续新高不追回，等回调再接")
    else:
        if strength == "veto":
            return {
                "type": "dip_buy_vetoed",
                "message": f"🔕 价格触及买入区，但{verdict['veto_note']}",
                "severity": "INFO",
            }
        if candidate.get("broke_low") and candidate.get("key_level") is not None:
            cond = (f"破{candidate['lookback']}日低点 {candidate['low_n']:.0f} "
                    f"+ 关键价位{candidate['key_level']:.0f}共振")
        elif candidate.get("broke_low"):
            cond = f"跌破{candidate['lookback']}日低点 {candidate['low_n']:.0f}"
        else:
            cond = f"接近关键价位 {candidate['key_level']:.0f}"
        lines.append(f"🛒 买入机会 | {cond}，当前 {current:.2f}")
        lines.append("   💡 建议: 分批低吸(参考活跃条件单)，单品种仓位≤20万上限")

    if verdict["reasons"]:
        lines.append("   📋 理由(客观事实):")
        for r in verdict["reasons"]:
            lines.append(f"   • {r}")
    if ev.get("active_orders"):
        lines.append(f"   📑 活跃条件单: {'；'.join(ev['active_orders'][:3])}")
    label = {"strong": "强", "medium": "中", "weak": "弱"}.get(strength, strength)
    lines.append(f"   理由强度: {label}")

    return {
        "type": "take_profit_breakout" if action == "take_profit" else "dip_buy_opportunity",
        "message": "\n".join(lines),
        "severity": "HIGH" if strength == "strong" else "MEDIUM",
    }
```

- [ ] **Step 4: 跑新增测试**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py -v`
Expected: 40 passed

- [ ] **Step 5: main() 集成**

改动 1 — 第 789 行历史数据加天（MA60 需要）：

```python
    historical = _get_historical(days=90)
```

改动 2 — 在第 821-823 行（`# 4f. 反弹检测` 块）之后插入：

```python
    # 4g. 机会提醒 (止盈/抄底) — 触发层 + 理由引擎
    opp_cfg = _load_opp_config()
    tp_candidate = _check_take_profit_breakout(current, historical, cost_basis, opp_cfg, surge)
    dip_candidate = _check_dip_buy_opportunity(current, state, historical, opp_cfg)
    if tp_candidate or dip_candidate:
        ev = _gather_evidence(current, historical, opp_cfg)
        if tp_candidate and _opp_cooldown_ok(state, "tp", current, "up", opp_cfg):
            verdict = _evaluate_reason("take_profit", tp_candidate, ev, opp_cfg)
            alerts.append(
                _build_opp_alert("take_profit", tp_candidate, verdict, ev, current, cost_basis)
            )
            state["tp_alert_at"] = _now().isoformat()
            state["tp_alert_price"] = current
        if dip_candidate and _opp_cooldown_ok(state, "dip", current, "down", opp_cfg):
            verdict = _evaluate_reason("dip_buy", dip_candidate, ev, opp_cfg)
            alerts.append(
                _build_opp_alert("dip_buy", dip_candidate, verdict, ev, current, cost_basis)
            )
            state["dip_alert_at"] = _now().isoformat()
            state["dip_alert_price"] = current
```

注意：`_check_dip_buy_opportunity` 每次完整检查都调用（即使不触发），以保证 `in_band_levels` 边沿状态持续跟踪。

- [ ] **Step 6: 全量回归 + 语法验证**

Run: `PYTHONPATH=src python3 -m pytest tests/test_adaptive_opportunity.py tests/test_signal_snapshot.py tests/test_alert.py -q && PYTHONPATH=src python3 -c "import sys; sys.path.insert(0, 'scripts'); import adaptive_gold_monitor"`
Expected: 全 passed；导入无异常

- [ ] **Step 7: Commit**

```bash
git add scripts/adaptive_gold_monitor.py tests/test_adaptive_opportunity.py
git commit -m "feat: 机会提醒集成 — 止盈/抄底告警进主检测链+冷却再提醒"
```

---

### Task 6: 端到端验证 + 提交推送 + 部署

**Files:** 无新增（仅验证与部署）

- [ ] **Step 1: 全量测试**

Run: `PYTHONPATH=src python3 -m pytest tests/ -q --ignore=tests/integration 2>/dev/null || PYTHONPATH=src python3 -m pytest tests/ -q`
Expected: 无新增失败（若仓库本就有 failing 测试，确认与本改动无关并在输出中说明）

- [ ] **Step 2: 本地真实跑一次监控（不告警也正常，验证不炸）**

Run: `cd /Users/jiangxiaoqiang/Documents/workspace/ai-gold-miner && PYTHONPATH=src ADAPTIVE_MONITOR_STATE=/tmp/opp_test_state.json python3 scripts/adaptive_gold_monitor.py; echo "exit=$?"`
Expected: `exit=0`；stdout 有或无卡片都正常（取决于实时价格），关键是无 traceback、stderr 无异常

- [ ] **Step 3: 手动模拟触发验证卡片（可选但推荐）**

Run 一小段 python 验证完整链路输出格式：

```bash
PYTHONPATH=src python3 - <<'EOF'
import sys
sys.path.insert(0, "scripts")
import adaptive_gold_monitor as m

hist = [{"date": f"2026-06-{i+1:02d}", "close": 880.0} for i in range(30)]
cfg = m._load_opp_config()
surge = {"type": "price_surge", "direction": "up", "change_pct": 0.6, "message": "x", "severity": "HIGH"}
cand = m._check_take_profit_breakout(945.0, hist, 890.0, cfg, surge)
assert cand, "候选应触发"
ev = {"rsi14": 72.0, "ma20": 890.0, "ma60": 885.0, "snapshot": None, "events": [], "active_orders": ["OCO止盈950/止损852"]}
verdict = m._evaluate_reason("take_profit", cand, ev, cfg)
print(m._build_opp_alert("take_profit", cand, verdict, ev, 945.0, 890.0)["message"])
EOF
```

Expected: 打印含 🎯、📋 理由、理由强度： 弱（快照 None 时）的卡片文本

- [ ] **Step 4: 提交推送（/gcp 约定：按功能分组提交）**

```bash
git status --short
git add -A
git commit -m "test: 机会提醒端到端验证"  # 若有遗留变更
git push
```

- [ ] **Step 5: 部署到 Hermes（项目规则：/gcp 后自动部署）**

```bash
cd /Users/jiangxiaoqiang/Documents/workspace/ai-gold-miner && ./scripts/deploy_gold_miner_to_hermes.sh
```

Expected: 脚本 exit 0；输出部署清单：同步文件（`scripts/adaptive_gold_monitor.py`、`src/gold_miner/signals/snapshot.py`、`src/gold_miner/pipeline/analysis.py`、`data/signal_snapshot.json` 若已生成）、cron job 无新增（复用现有每分钟 adaptive job）

- [ ] **Step 6: 服务器侧验证**

```bash
ssh hermes "cd /home/ubuntu/ai-gold-miner && PYTHONPATH=src timeout 60 python3 scripts/adaptive_gold_monitor.py; echo exit=\$?"
```

Expected: `exit=0`，无 traceback（ssh 主机别名以 `scripts/deploy_gold_miner_to_hermes.sh` 内实际目标为准，先读该脚本确认）

---

## Self-Review 记录

- **Spec 覆盖**：§4.1→Task 3；§4.2→Task 3；§4.3→Task 2；§5.1→Task 4；§5.2→Task 4；§5.3→Task 5；§6→Task 5（关键价位 240min 冷却有意省略，见偏差 1）；§7→Task 1；§8→各任务边界测试；§9→每任务 TDD；§10→Task 6。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：candidate 键（`high_n/lookback/profit_pct`、`broke_low/low_n/key_level`）、verdict 键（`strength/reasons/veto_note`）、快照 JSON 字段（`bull_dims/bear_dims/direction_clarity/timestamp`）在 Task 1/3/4/5 间一致；`_opp_cooldown_ok` 的 prefix（"tp"/"dip"）与 state 键（`tp_alert_at` 等）一致。
