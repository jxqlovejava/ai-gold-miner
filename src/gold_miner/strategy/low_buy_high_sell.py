"""分级低吸高抛建议器 (V9 成本管理原则).

中长期看多黄金 + 只做多前提下, 低吸高抛不是"频繁做 T", 而是分级执行:
- 低吸无条件: 加仓永远在安全边际内, 回调分批, 加权成本自然走低
- 高抛有条件: 只认客观信号 (ATR 移动止盈 / 再平衡 / 估值极端 / 基本面逆转)
- 核心池只低吸不动, 机动池是唯一低吸高抛执行池, 机会池只走 S 协议

⚠️ 反对「把历史均价压下去」当目标: 那是锚定偏差。成本均价是结果不是目的,
   正确目标是「在安全边际内建仓 + 控制单笔损失」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class LowBuyHighSellSignal:
    """分级低吸高抛建议信号."""

    # 池级动作
    core_pool: dict[str, Any] = field(default_factory=dict)       # 核心池建议
    tactical_pool: dict[str, Any] = field(default_factory=dict)   # 机动池建议
    opportunity_pool: dict[str, Any] = field(default_factory=dict)  # 机会池建议

    # 汇总
    low_buy_suggestion: str = "观望"      # 低吸档建议: 加仓/等待/禁用
    high_sell_suggestion: str = "持有"    # 高抛建议: 减仓/持有
    triggered_signals: list[str] = field(default_factory=list)  # 触发的高抛信号
    warnings: list[str] = field(default_factory=list)           # 警告(如锚定偏差提示)
    rule_ids: list[str] = field(default_factory=list)           # 命中的军规 ID

    def to_dict(self) -> dict[str, Any]:
        """转为字典便于输出."""
        return {
            "core_pool": self.core_pool,
            "tactical_pool": self.tactical_pool,
            "opportunity_pool": self.opportunity_pool,
            "low_buy_suggestion": self.low_buy_suggestion,
            "high_sell_suggestion": self.high_sell_suggestion,
            "triggered_signals": self.triggered_signals,
            "warnings": self.warnings,
            "rule_ids": self.rule_ids,
        }


class LowBuyHighSellAdvisor:
    """分级低吸高抛建议器.

    纯逻辑建议器, 不发起网络请求. 输入信号由调用方 (分析管线) 采集后传入.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化.

        Args:
            config: portfolio.yaml 的 long_term.low_buy_high_sell 配置.
                   为 None 时使用默认配置 (三池 40/20/20).
        """
        self.config = config or self._default_config()

    @staticmethod
    def _default_config() -> dict[str, Any]:
        """默认分级低吸高抛配置 (V9)."""
        return {
            "core_pool": {"low_buy": True, "high_sell": False},
            "tactical_pool": {"low_buy": True, "high_sell": True},
            "opportunity_pool": {"low_buy": False, "high_sell": False},
            "high_sell_signals": {
                "atr_trailing": True,
                "rebalance": True,
                "extreme_sentiment": True,
                "core_fundamental_break": True,
            },
            "low_buy_iron_rules": {
                "max_single_pct": 5,
                "gate_smart_money": True,
                "no_manual_t": True,
            },
        }

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        current_price: float,
        pools: dict[str, Any],
        atr_trailing_triggered: bool = False,
        atr_trailing_price: float | None = None,
        rebalance_overweight: bool = False,
        rsi_value: float | None = None,
        cot_net_position_change: float | None = None,  # 正=转流入, 负=转流出
        central_bank_buying_slow: bool = False,        # 央行购金连续两季<100吨
        pool_deviation_pp: dict[str, float] | None = None,  # 各池偏离目标 pp
        pool_profit_pct: dict[str, float] | None = None,    # 各池浮盈 %
    ) -> LowBuyHighSellSignal:
        """评估当前状态, 输出分级低吸高抛建议.

        Args:
            current_price: 当前金价 (元/克)
            pools: 三池配置, 如 {"core": 40, "tactical": 20, "opportunity": 20}
            atr_trailing_triggered: ATR 移动止盈是否触发 (r025)
            atr_trailing_price: ATR 移动止盈止损位
            rebalance_overweight: 是否触发仓位超配再平衡 (r020)
            rsi_value: RSI 值 (用于估值极端判断)
            cot_net_position_change: COT 净多变化 (正=聪明钱流入)
            central_bank_buying_slow: 央行购金是否转弱
            pool_deviation_pp: 各池当前占比与目标偏离百分点
            pool_profit_pct: 各池浮盈比例

        Returns:
            LowBuyHighSellSignal 建议信号
        """
        cfg = self.config
        signals: list[str] = []
        warnings: list[str] = []
        rule_ids: list[str] = []

        # ---- 核心池建议 ----
        core = cfg["core_pool"]
        core_advice = self._core_pool_advice(
            config=core,
            atr_triggered=atr_trailing_triggered,
            fundamental_break=central_bank_buying_slow,
            atr_price=atr_trailing_price,
            signals=signals,
            rule_ids=rule_ids,
        )

        # ---- 机动池建议 ----
        tactical = cfg["tactical_pool"]
        tactical_advice = self._tactical_pool_advice(
            config=tactical,
            atr_triggered=atr_trailing_triggered,
            rebalance_overweight=rebalance_overweight,
            rsi_value=rsi_value,
            cot_change=cot_net_position_change,
            pool_deviation=pool_deviation_pp,
            pool_profit=pool_profit_pct,
            signals=signals,
            rule_ids=rule_ids,
            warnings=warnings,
        )

        # ---- 机会池建议 ----
        opportunity = cfg["opportunity_pool"]
        opp_advice = self._opportunity_pool_advice(config=opportunity, signals=signals)

        # ---- 聪明钱闸门 (MK4) ----
        gate_closed = self._smart_money_gate_closed(cot_net_position_change, rule_ids)

        # ---- 汇总 ----
        low_buy = self._summarize_low_buy(
            core_advice, tactical_advice, opp_advice, gate_closed, warnings
        )
        high_sell = self._summarize_high_sell(core_advice, tactical_advice)

        return LowBuyHighSellSignal(
            core_pool=core_advice,
            tactical_pool=tactical_advice,
            opportunity_pool=opp_advice,
            low_buy_suggestion=low_buy,
            high_sell_suggestion=high_sell,
            triggered_signals=signals,
            warnings=warnings,
            rule_ids=rule_ids,
        )

    # ------------------------------------------------------------------
    # 各池建议
    # ------------------------------------------------------------------
    def _core_pool_advice(
        self,
        *,
        config: dict[str, Any],
        atr_triggered: bool,
        fundamental_break: bool,
        atr_price: float | None,
        signals: list[str],
        rule_ids: list[str],
    ) -> dict[str, Any]:
        """核心池: 只低吸, 高抛仅 ATR 或基本面逆转."""
        action = "持有"
        detail = "长期底仓, 不动"

        if config["high_sell"] and atr_triggered:
            action = "ATR 移动止盈减半"
            detail = f"r025: 跌破移动止盈位 {atr_price if atr_price else '?'} → 减一半"
            signals.append("core_atr_trailing")
            rule_ids.append("r025")
        elif config["high_sell"] and fundamental_break:
            action = "基本面逆转减仓"
            detail = "央行购金连续两季<100吨 → 结构性买盘承压, 评估减仓"
            signals.append("core_fundamental_break")
            rule_ids.append("r020")

        return {"pool": "core", "action": action, "detail": detail}

    def _tactical_pool_advice(
        self,
        *,
        config: dict[str, Any],
        atr_triggered: bool,
        rebalance_overweight: bool,
        rsi_value: float | None,
        cot_change: float | None,
        pool_deviation: dict[str, float] | None,
        pool_profit: dict[str, float] | None,
        signals: list[str],
        rule_ids: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        """机动池: 唯一低吸高抛执行池."""
        action = "持有"
        detail = "波段池, 无高抛信号触发"

        # 高抛信号判断
        high_sell = False
        reasons: list[str] = []

        if config["high_sell"] and atr_triggered:
            high_sell = True
            reasons.append("ATR 移动止盈触发 (r025)")
            signals.append("tactical_atr_trailing")
            rule_ids.append("r025")

        if config["high_sell"] and rebalance_overweight:
            deviation = (pool_deviation or {}).get("tactical", 0)
            profit = (pool_profit or {}).get("tactical", 0)
            if deviation > 10 and profit > 20:
                high_sell = True
                reasons.append(f"仓位超配再平衡 (r020): 偏离{deviation:.0f}pp 浮盈{profit:.0f}%")
                signals.append("tactical_rebalance")
                rule_ids.append("r020")

        if config["high_sell"] and rsi_value is not None and rsi_value > 80:
            if cot_change is not None and cot_change < 0:
                high_sell = True
                reasons.append(f"估值/情绪极端 (r030): RSI {rsi_value:.0f} + COT 转流出")
                signals.append("tactical_extreme_sentiment")
                rule_ids.append("r030")

        if high_sell:
            action = "波段高抛"
            detail = "; ".join(reasons)
        else:
            warnings.append("机动池: 无高抛信号, 不因'赚了一点'手动做T (r030)")

        return {"pool": "tactical", "action": action, "detail": detail}

    def _opportunity_pool_advice(
        self, *, config: dict[str, Any], signals: list[str]
    ) -> dict[str, Any]:
        """机会池: 不做波段, 只走 S 协议."""
        return {
            "pool": "opportunity",
            "action": "S 协议待命",
            "detail": "只按 S 协议一次性出击, 不做低吸高抛波段",
        }

    # ------------------------------------------------------------------
    # 闸门与汇总
    # ------------------------------------------------------------------
    def _smart_money_gate_closed(
        self, cot_change: float | None, rule_ids: list[str]
    ) -> bool:
        """聪明钱闸门 (MK4): COT 转流出时关闭, 禁止主动低吸."""
        if cot_change is None:
            return False
        if cot_change < 0:
            rule_ids.append("r020")  # 宁可踏空不可接飞刀
            return True
        return False

    def _summarize_low_buy(
        self,
        core: dict[str, Any],
        tactical: dict[str, Any],
        opp: dict[str, Any],
        gate_closed: bool,
        warnings: list[str],
    ) -> str:
        """汇总低吸建议."""
        if gate_closed:
            warnings.append("聪明钱闸门关闭 (COT 转流出): 禁止主动低吸, 只靠低吸单被动接货")
            return "禁用 (MK4 闸门)"
        # 核心池可低吸 (回调分批) + 机动池可低吸 → 等待回调
        if core["action"] == "持有" and tactical["action"] == "持有":
            return "等待回调低吸 (核心池/机动池)"
        return "分批低吸"

    def _summarize_high_sell(self, core: dict[str, Any], tactical: dict[str, Any]) -> str:
        """汇总高抛建议."""
        actions = [core["action"], tactical["action"]]
        if any("减" in a or "高抛" in a for a in actions):
            return "减仓 (信号触发)"
        return "持有"
