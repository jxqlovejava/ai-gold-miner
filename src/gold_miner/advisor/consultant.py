"""对话式咨询接口 — 用户输入疑虑/分析，获得策略级回应.

核心逻辑:
  1. 意图识别 — 解析用户输入的咨询类型
  2. 路由到对应模块 — action_guide / early_warning / extreme_guard / sentiment_guard
  3. 结合用户当前仓位/策略，给出个性化回应
  4. 引用军规，增强说服力

使用方式:
    consultant = Consultant()
    answer = consultant.answer("美联储下周加息，我该清仓吗？", current_position_pct=0.5)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.advisor.core import (
    AdvisorReport,
    AlertLevel,
    UserProfile,
)
from gold_miner.advisor.early_warning import EarlyWarningEngine
from gold_miner.advisor.extreme_guard import ExtremeGuard
from gold_miner.advisor.sentiment_guard import SentimentGuard
from gold_miner.doctrine.checker import DoctrineChecker
from gold_miner.llm.client import LLMClient


# ---------------------------------------------------------------------------
# 意图识别
# ---------------------------------------------------------------------------

class IntentType:
    EVENT_IMPACT = "event_impact"       # "美联储要加息了..."
    POSITION_REVIEW = "position_review"  # "我现在的仓位合适吗"
    SENTIMENT_CHECK = "sentiment_check"  # "现在市场情绪怎样"
    EXTREME_CONCERN = "extreme_concern"  # "如果打仗怎么办"
    BUY_SELL_ADVICE = "buy_sell_advice"  # "现在该买还是卖"
    GENERAL = "general"                  # 其他


INTENT_PATTERNS: dict[str, list[str]] = {
    IntentType.EVENT_IMPACT: [
        "加息", "降息", "非农", "cpi", "ppi", "pce", "pmi", "fed", "联储",
        "决议", "会议", "数据", "公布", "发布",
    ],
    IntentType.POSITION_REVIEW: [
        "仓位", "持仓", "比例", "合适吗", "重吗", "轻吗", "太多", "太少",
    ],
    IntentType.SENTIMENT_CHECK: [
        "情绪", "恐慌", "贪婪", "恐惧", "乐观", "悲观", "散户", "机构",
    ],
    IntentType.EXTREME_CONCERN: [
        "战争", "打仗", "危机", "崩盘", "暴跌", "黑天鹅", "灰犀牛",
        "最坏", "如果", "假设",
    ],
    IntentType.BUY_SELL_ADVICE: [
        "买", "卖", "清仓", "加仓", "减仓", "入场", "出场",
        "做多", "做空", "时机",
    ],
}


@dataclass
class ParsedIntent:
    intent: str
    confidence: float
    keywords: list[str]
    user_position_pct: float = 0.0
    user_question: str = ""


class Consultant:
    """投资咨询顾问 — 像资深投资大师一样回答用户问题."""

    def __init__(self) -> None:
        self.early_warning = EarlyWarningEngine()
        self.extreme_guard = ExtremeGuard()
        self.sentiment_guard = SentimentGuard()
        self.doctrine = DoctrineChecker()
        self.llm = LLMClient()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def answer(
        self,
        question: str,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
        user_profile: UserProfile | None = None,
    ) -> AdvisorReport:
        """回答用户咨询.

        Args:
            question: 用户问题/疑虑
            current_position_pct: 当前仓位
            avg_cost: 持仓均价
            user_profile: 用户画像

        Returns:
            AdvisorReport，report_type="consult"
        """
        logger.info(f"[Consultant] 收到咨询: {question[:50]}...")

        profile = user_profile or UserProfile()

        # 1. 意图识别
        intent = self._parse_intent(question)
        logger.info(f"[Consultant] 意图识别: {intent.intent} (置信度 {intent.confidence:.0%})")

        # 2. 路由处理
        sub_report = self._route(intent, current_position_pct, avg_cost)

        # 3. 结合用户画像生成个性化回应
        answer = self._build_personalized_answer(
            intent, sub_report, current_position_pct, avg_cost, profile
        )

        # 4. LLM 润色（如果可用）
        if self.llm.enabled:
            answer = self._polish_with_llm(question, answer, intent)

        return AdvisorReport(
            report_type="consult",
            consultation_answer=answer,
            alerts=sub_report.alerts if sub_report else [],
            sentiment=sub_report.sentiment if sub_report else None,
            stress_tests=sub_report.stress_tests if sub_report else [],
            confidence=intent.confidence,
            sources=["IntentParser", "AdvisorModules", "DoctrineChecker"]
            + (["LLM"] if self.llm.enabled else []),
        )

    # ------------------------------------------------------------------
    # 意图解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_intent(question: str) -> ParsedIntent:
        """解析用户意图."""
        q_lower = question.lower()
        scores: dict[str, int] = {}
        matched_keywords: dict[str, list[str]] = {}

        for intent, keywords in INTENT_PATTERNS.items():
            score = 0
            matched = []
            for kw in keywords:
                if kw.lower() in q_lower:
                    score += 1
                    matched.append(kw)
            if score > 0:
                scores[intent] = score
                matched_keywords[intent] = matched

        if not scores:
            return ParsedIntent(
                intent=IntentType.GENERAL,
                confidence=0.3,
                keywords=[],
                user_question=question,
            )

        best_intent = max(scores, key=scores.get)
        total_kw = sum(len(INTENT_PATTERNS[k]) for k in INTENT_PATTERNS)
        confidence = min(scores[best_intent] / len(INTENT_PATTERNS[best_intent]) * 2, 1.0)

        return ParsedIntent(
            intent=best_intent,
            confidence=round(confidence, 2),
            keywords=matched_keywords.get(best_intent, []),
            user_question=question,
        )

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route(
        self,
        intent: ParsedIntent,
        position_pct: float,
        avg_cost: float,
    ) -> AdvisorReport | None:
        """根据意图路由到对应模块."""
        if intent.intent == IntentType.EVENT_IMPACT:
            # 检查是否有相关事件预警
            return self.early_warning.scan(days_ahead=14)

        if intent.intent == IntentType.SENTIMENT_CHECK:
            return self.sentiment_guard.analyze()

        if intent.intent == IntentType.EXTREME_CONCERN:
            # 从问题中提取关键词
            for scenario in ["战争", "危机", "崩盘", "通胀"]:
                if scenario in intent.user_question:
                    return self.extreme_guard.check_scenario(scenario, position_pct)
            return self.extreme_guard.stress_test(position_pct)

        if intent.intent == IntentType.POSITION_REVIEW:
            # 使用军规审查当前仓位
            return self._review_position(position_pct, avg_cost)

        if intent.intent == IntentType.BUY_SELL_ADVICE:
            # 路由到 action_guide（在外层处理）
            return None

        return None

    def _review_position(self, position_pct: float, avg_cost: float) -> AdvisorReport:
        """审查当前仓位."""
        decision = {
            "action": "hold",
            "position_pct": position_pct,
            "gold_price": avg_cost,  # 简化
        }
        result = self.doctrine.check(decision, context={"current_position_pct": position_pct})

        warnings = []
        for v in result.violations:
            if not v.passed:
                icon = "🚫" if v.rule.severity == "block" else "⚠️"
                warnings.append(f"{icon} {v.rule.name}: {v.message}")

        if not warnings:
            warnings.append("✅ 当前仓位通过所有军规检查")

        return AdvisorReport(
            report_type="consult",
            confidence=0.8,
            warnings=warnings,
            sources=["DoctrineChecker"],
        )

    # ------------------------------------------------------------------
    # 回应生成
    # ------------------------------------------------------------------

    def _build_personalized_answer(
        self,
        intent: ParsedIntent,
        sub_report: AdvisorReport | None,
        position_pct: float,
        avg_cost: float,
        profile: UserProfile,
    ) -> str:
        """构建个性化回应."""
        parts: list[str] = []

        # 开头：确认理解
        parts.append(self._greeting(intent))

        # 中间：分析 + 建议
        if intent.intent == IntentType.EVENT_IMPACT:
            parts.append(self._answer_event_impact(intent, sub_report))
        elif intent.intent == IntentType.POSITION_REVIEW:
            parts.append(self._answer_position_review(position_pct, avg_cost, profile))
        elif intent.intent == IntentType.SENTIMENT_CHECK:
            parts.append(self._answer_sentiment(sub_report))
        elif intent.intent == IntentType.EXTREME_CONCERN:
            parts.append(self._answer_extreme(intent, sub_report))
        elif intent.intent == IntentType.BUY_SELL_ADVICE:
            parts.append(self._answer_buy_sell(intent, position_pct, profile))
        else:
            parts.append(self._answer_general(intent))

        # 结尾：提醒
        parts.append(self._closing(position_pct, profile))

        return "\n\n".join(parts)

    @staticmethod
    def _greeting(intent: ParsedIntent) -> str:
        greetings = {
            IntentType.EVENT_IMPACT: "关于事件影响的分析，以下是基于数据和军规的判断：",
            IntentType.POSITION_REVIEW: "仓位审查结果如下：",
            IntentType.SENTIMENT_CHECK: "当前市场情绪扫描完成：",
            IntentType.EXTREME_CONCERN: "极端情景分析如下：",
            IntentType.BUY_SELL_ADVICE: "买卖时机分析：",
            IntentType.GENERAL: "感谢您的提问，以下是我的分析：",
        }
        return greetings.get(intent.intent, greetings[IntentType.GENERAL])

    def _answer_event_impact(
        self, intent: ParsedIntent, sub_report: AdvisorReport | None
    ) -> str:
        if sub_report and sub_report.alerts:
            alert = sub_report.alerts[0]
            days_until = (alert.scheduled_at - datetime.now()).days
            return (
                f"**{alert.event_name}** 将在约{days_until}天后发生。"
                f"\n\n历史数据显示此类事件通常导致黄金{alert.gold_direction} "
                f"波动约±{alert.expected_move_pct:.1%}。"
                f"\n\n**建议**: {alert.advice_summary}"
            )
        return "未检测到明确的相关事件。建议维持现有策略，不因猜测事件而盲目操作。"

    def _answer_position_review(
        self, position_pct: float, avg_cost: float, profile: UserProfile
    ) -> str:
        lines = [f"当前仓位 **{position_pct:.0%}**，投资风格 **{profile.risk_tolerance}**"]

        if position_pct > profile.max_single_position_pct:
            lines.append(
                f"\n⚠️ **警告**: 仓位超过上限({profile.max_single_position_pct:.0%})，"
                f"违反军规。建议立即减仓。"
            )
        elif position_pct > 0.6:
            lines.append(
                f"\n仓位偏重。若市场出现意外波动，回撤会较大。"
                f"建议设置严格止损。"
            )
        elif position_pct < 0.1 and profile.risk_tolerance != "low":
            lines.append(
                f"\n仓位过轻，可能错过趋势行情。"
                f"若信号支持，可考虑逐步建仓至20-30%。"
            )
        else:
            lines.append(f"\n✅ 仓位在合理范围内，继续执行策略。")

        if avg_cost > 0:
            lines.append(f"\n持仓均价: ${avg_cost:.2f}")

        return "\n".join(lines)

    @staticmethod
    def _answer_sentiment(sub_report: AdvisorReport | None) -> str:
        if sub_report and sub_report.sentiment:
            s = sub_report.sentiment
            return (
                f"散户情绪: **{s.retail_sentiment}**"
                f"{' (极端!)' if s.retail_extreme else ''}"
                f"\n机构动向: **{s.institutional_signal}**"
                f"\nETF 流向: **{s.etf_flow_signal}**"
                f"\n\n**节奏对齐建议**: {s.alignment_note}"
            )
        return "情绪数据暂不可用。建议参考恐惧贪婪指数和VIX自行判断。"

    @staticmethod
    def _answer_extreme(
        intent: ParsedIntent, sub_report: AdvisorReport | None
    ) -> str:
        if sub_report and sub_report.stress_tests:
            t = sub_report.stress_tests[0]
            return (
                f"**{t.scenario_name}** 情景分析："
                f"\n- 预估概率: {t.probability_estimate:.0%}"
                f"\n- 最大潜在回撤: {t.max_drawdown_pct:.1%}"
                f"\n- 准备度: {t.preparedness_score:.0%}"
                f"\n\n**对冲建议**: {t.hedge_recommendation}"
            )
        return "已记录您的担忧。建议定期运行极端情景体检，保持防御意识。"

    @staticmethod
    def _answer_buy_sell(
        intent: ParsedIntent, position_pct: float, profile: UserProfile
    ) -> str:
        # 不直接给买卖建议，而是引导思考
        return (
            "直接问'该买还是卖'往往是被情绪驱动。"
            "\n\n请先回答自己："
            f"\n1. 当前仓位 {position_pct:.0%} 是否在你的舒适区？"
            f"\n2. 你的风险承受力是 {profile.risk_tolerance}，这次操作符合吗？"
            f"\n3. 有无明确信号支持？还是只是'感觉'？"
            "\n\n建议先运行 `gold-miner advisor daily` 获取基于信号的指令，"
            "而非凭直觉操作。"
        )

    @staticmethod
    def _answer_general(intent: ParsedIntent) -> str:
        return (
            "我没有完全理解您的具体关注点。"
            "\n\n您可以尝试这样问我："
            "\n- '美联储下周加息怎么办' → 事件影响分析"
            "\n- '我仓位50%合适吗' → 仓位审查"
            "\n- '现在市场情绪怎样' → 情绪扫描"
            "\n- '打仗了怎么办' → 极端情景"
        )

    @staticmethod
    def _closing(position_pct: float, profile: UserProfile) -> str:
        if position_pct == 0:
            return "\n💡 提醒: 空仓也是一种仓位。不要因为焦虑而盲目入场。"
        if position_pct > 0.7:
            return "\n💡 提醒: 重仓时纪律比预测更重要。严格执行止损。"
        return "\n💡 提醒: 投资是长跑，不要被短期波动左右心态。"

    # ------------------------------------------------------------------
    # LLM 润色
    # ------------------------------------------------------------------

    def _polish_with_llm(self, question: str, answer: str, intent: ParsedIntent) -> str:
        """用 LLM 润色回应，使其更像投资大师的口吻."""
        try:
            prompt = (
                f"你是一位拥有30年经验的资深黄金投资大师。"
                f"\n\n用户问题: {question}"
                f"\n\n你的初步回答:\n{answer}"
                f"\n\n请润色回答，使其："
                f"\n1. 语气沉稳、专业，像一位智者"
                f"\n2. 强调纪律和长期思维"
                f"\n3. 适当引用投资智慧（如巴菲特、达里奥、林奇的格言）"
                f"\n4. 保持内容准确，不要编造数据"
                f"\n5. 用中文回答"
            )
            result = self.llm.analyze_article(text=prompt, rule_sentiment="neutral", rule_score=0)
            if result and not result.get("parse_error"):
                polished = result.get("analysis", "")
                if polished and len(polished) > 50:
                    return polished
        except Exception as e:
            logger.debug(f"LLM 润色失败: {e}")

        return answer

