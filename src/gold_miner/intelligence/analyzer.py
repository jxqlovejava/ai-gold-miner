"""文章分析器 — 情感打分 + 操纵话术检测 + 关键主张提取."""

import re
from dataclasses import dataclass, field


# ------------------------------------------------------------------
# 情感词典
# ------------------------------------------------------------------

BULLISH_KEYWORDS: list[str] = [
    "暴涨", "飙升", "突破", "新高", "避险需求", "避险情绪", "避险升温",
    "降息", "宽松", "通胀升温", "通胀抬头", "通胀预期", "通胀压力",
    "地缘紧张", "地缘冲突", "地缘风险", "地缘政治", "央行购金",
    "去美元化", "资金流入", "ETF增持", "增持黄金", "看涨",
    "上涨", "走高", "走强", "反弹", "牛市", "买入", "做多",
    "利多", "利好", "强劲", "走牛", "加仓", "增配",
]

BEARISH_KEYWORDS: list[str] = [
    "暴跌", "崩盘", "回调", "下跌", "走低", "走弱", "加息",
    "紧缩", "美元走强", "美元指数走强", "美元反弹", "风险偏好回升",
    "获利了结", "资金流出", "ETF减持", "减持黄金", "看跌",
    "承压", "压力", "阻力", "熊市", "卖出", "做空", "抛售",
    "利空", "疲软", "走熊", "减仓", "降配", "经济复苏",
    "避险消退", "避险降温",
]

INTENSITY_MODIFIERS: dict[str, float] = {
    "strong": ["必然", "毫无疑问", "强烈", "重大", "历史性", "确认", "已确定", "必将"],
    "moderate": ["预计", "可能", "或将", "有望", "面临", "或将", "大概率", "倾向于"],
    "weak": ["不排除", "需关注", "值得警惕", "或许", "也许", "未必", "尽管"],
}


# ------------------------------------------------------------------
# 操纵话术检测规则
# ------------------------------------------------------------------

ANONYMOUS_SOURCE_PATTERNS: list[str] = [
    "据知情人士", "业内人士", "消息人士", "知情人士透露",
    "据透露", "据称", "据悉", "传闻", "传言", "市场传言",
    "内部人士", "接近.*人士", "不愿具名",
]

TIME_PRESSURE_PATTERNS: list[str] = [
    "即将", "最后机会", "倒计时", "错过不再", "抓紧",
    "立刻", "马上", "不要再等", "现在.*最后", "马上.*暴涨",
]

AUTHORITY_PATTERNS: list[str] = [
    "专家称", "专家表示", "机构认为", "权威预测", "权威分析",
    "知名分析师", "某机构", "某专家",
]


# ------------------------------------------------------------------
# 关键主张提取
# ------------------------------------------------------------------

CLAIM_PATTERNS: list[tuple[str, str]] = [
    (r"(美联储|Fed|FOMC).{0,10}(降息|加息|维持利率|宽松|紧缩)", "货币政策"),
    (r"(通胀|CPI|PCE|物价).{0,10}(上升|下降|升温|回落|走高|走低)", "通胀预期"),
    (r"(黄金|金价).{0,10}(目标|看到|上看|下看|目标价)[\s\d,，]+(\d{3,5})", "价格目标"),
    (r"(央行|中央银行).{0,10}(购金|增持|减持|抛售)", "央行行为"),
    (r"(美元|美元指数|DXY).{0,10}(走强|走弱|上涨|下跌|反弹|回落)", "美元走势"),
    (r"(地缘|冲突|战争|制裁|封锁).{0,10}(加剧|升级|缓解|结束)", "地缘风险"),
    (r"(就业|非农|失业).{0,10}(强劲|疲软|改善|恶化)", "就业数据"),
    (r"(避险|风险偏好).{0,10}(升温|降温|回升|消退)", "市场情绪"),
]


@dataclass
class ArticleAnalysis:
    """文章分析结果."""

    # 基础信息
    word_count: int = 0
    # 情感打分
    sentiment_score: float = 0.0  # -1.0 ~ +1.0
    sentiment_direction: str = "neutral"  # bullish / bearish / neutral
    bullish_count: int = 0
    bearish_count: int = 0
    # 操纵话术检测
    manipulation_score: int = 0  # 0-7
    manipulation_flags: list[str] = field(default_factory=list)
    is_suspicious: bool = False  # manipulation_score >= 3
    # 关键主张
    claims: list[dict[str, str]] = field(default_factory=list)
    # 摘要
    summary: str = ""


class ArticleAnalyzer:
    """文章分析器 — 规则引擎."""

    def analyze(self, text: str) -> ArticleAnalysis:
        if not text:
            return ArticleAnalysis()

        result = ArticleAnalysis()
        result.word_count = len(text)

        # 1. 情感分析
        result.bullish_count = self._count_keywords(text, BULLISH_KEYWORDS)
        result.bearish_count = self._count_keywords(text, BEARISH_KEYWORDS)

        intensity = self._measure_intensity(text)
        raw_score = 0.0
        total = result.bullish_count + result.bearish_count
        if total > 0:
            raw_score = (result.bullish_count - result.bearish_count) / total

        if total >= 3:
            result.sentiment_score = max(-1.0, min(1.0, raw_score * intensity))
            if result.sentiment_score > 0.15:
                result.sentiment_direction = "bullish"
            elif result.sentiment_score < -0.15:
                result.sentiment_direction = "bearish"
            else:
                result.sentiment_direction = "neutral"

        # 2. 操纵话术检测
        result.manipulation_flags = self._detect_manipulation(text, result)
        result.manipulation_score = len(result.manipulation_flags)
        result.is_suspicious = result.manipulation_score >= 3

        # 3. 关键主张提取
        result.claims = self._extract_claims(text)

        # 4. 摘要
        result.summary = self._generate_summary(result)

        return result

    # ------------------------------------------------------------------
    # 情感分析
    # ------------------------------------------------------------------

    @staticmethod
    def _count_keywords(text: str, keywords: list[str]) -> int:
        count = 0
        for kw in keywords:
            count += len(re.findall(re.escape(kw), text))
        return count

    @staticmethod
    def _measure_intensity(text: str) -> float:
        """测量修饰词强度，返回 0.5~2.0 的乘数."""
        weights: list[float] = []
        for kw in INTENSITY_MODIFIERS["strong"]:
            if kw in text:
                weights.append(2.0)
        for kw in INTENSITY_MODIFIERS["moderate"]:
            if kw in text:
                weights.append(1.0)
        for kw in INTENSITY_MODIFIERS["weak"]:
            if kw in text:
                weights.append(0.5)

        if not weights:
            return 1.0
        return sum(weights) / len(weights)

    # ------------------------------------------------------------------
    # 操纵话术检测
    # ------------------------------------------------------------------

    def _detect_manipulation(
        self, text: str, analysis: ArticleAnalysis
    ) -> list[str]:
        flags: list[str] = []

        # 1. 单一方向
        total = analysis.bullish_count + analysis.bearish_count
        if total >= 3:
            if analysis.bullish_count == 0 and analysis.bearish_count >= 3:
                flags.append("单一方向: 全文仅看跌，无反面论述")
            elif analysis.bearish_count == 0 and analysis.bullish_count >= 3:
                flags.append("单一方向: 全文仅看涨，无反面论述")

        # 2. 匿名来源
        for pat in ANONYMOUS_SOURCE_PATTERNS:
            if re.search(pat, text):
                flags.append(f"匿名来源: 含'{pat}'等非具名信息源")
                break

        # 3. 情绪密度过高
        emotional_count = analysis.bullish_count + analysis.bearish_count
        word_estimate = max(len(re.findall(r"[一-鿿]+", text)), 1)
        if emotional_count / word_estimate > 0.08:
            flags.append(f"情绪密度过高: {emotional_count}/{word_estimate} > 8%")

        # 4. 时间压力
        for pat in TIME_PRESSURE_PATTERNS:
            if pat in text:
                flags.append(f"时间压力: 含'{pat}'类紧迫话术")
                break

        # 5. 权威绑架
        for pat in AUTHORITY_PATTERNS:
            if pat in text:
                # 检查是否有具体数字支撑
                numbers = re.findall(r"\d+(?:\.\d+)?%?", text)
                if len(numbers) < 2:
                    flags.append(f"权威绑架: 引用'{pat}'但缺少具体数据")
                break

        # 6. 数据缺失
        vague_claims = re.findall(
            r"(大幅|显著|明显|急剧|快速|缓慢)(增长|下降|上升|下跌|增加|减少)",
            text,
        )
        if len(vague_claims) >= 3 and len(re.findall(r"\d+\.?\d*%", text)) < 3:
            flags.append("数据缺失: 使用模糊描述但缺少具体数字")

        # 7. 推销倾向
        promo_patterns = ["加群", "跟单", "开户", "VIP", "扫码", "订阅", "付费"]
        for pat in promo_patterns:
            if pat in text:
                flags.append(f"推销倾向: 含'{pat}'类推广话术")
                break

        return flags

    # ------------------------------------------------------------------
    # 关键主张提取
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_claims(text: str) -> list[dict[str, str]]:
        claims: list[dict[str, str]] = []
        seen: set[str] = set()

        for pattern, category in CLAIM_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                claim_text = "".join(match) if isinstance(match, tuple) else match
                claim_text = claim_text.strip()
                if claim_text and claim_text not in seen and len(claim_text) > 4:
                    seen.add(claim_text)
                    claims.append({
                        "category": category,
                        "claim": claim_text,
                        "pattern": pattern,
                    })

        return claims[:10]  # 最多10条

    # ------------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_summary(analysis: ArticleAnalysis) -> str:
        parts: list[str] = []

        if analysis.sentiment_direction != "neutral":
            direction_cn = "看涨" if analysis.sentiment_direction == "bullish" else "看跌"
            parts.append(
                f"情感倾向: {direction_cn} "
                f"(得分: {analysis.sentiment_score:+.2f}, "
                f"看涨词{analysis.bullish_count}个/看跌词{analysis.bearish_count}个)"
            )
        else:
            parts.append("情感倾向: 中性或信号不足")

        if analysis.is_suspicious:
            parts.append(f"可信度: 疑似带节奏 ({analysis.manipulation_score}/7项命中)")
        else:
            parts.append(f"可信度: 暂未检测到明显操纵 ({analysis.manipulation_score}/7项)")

        if analysis.claims:
            parts.append(f"提取关键主张: {len(analysis.claims)}条")

        return " | ".join(parts)
