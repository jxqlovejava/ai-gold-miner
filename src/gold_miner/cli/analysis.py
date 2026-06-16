"""Analysis command handler."""

from __future__ import annotations

import argparse
import json
import uuid

from loguru import logger

from gold_miner.config import settings
from gold_miner.events.models import EventType, EvidenceSnapshot
from gold_miner.events.store import EventStore
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.intelligence.analyzer import ArticleAnalyzer
from gold_miner.intelligence.journal import ArticleJournal, ArticleRecord
from gold_miner.intelligence.reader import ArticleReader
from gold_miner.llm.client import LLMClient


def run_analyze(args: argparse.Namespace) -> None:
    """文章情报分析 — 摄入/列表/查看/更新/预判."""
    journal = ArticleJournal()

    # --list: 列出所有已分析文章
    if args.list:
        records = journal.list_all()
        if not records:
            print("暂无文章分析记录")
            return
        print(f"{'ID':<14} {'时间':<18} {'方向':<8} {'可信度':<8} {'来源'}")
        print("-" * 80)
        for r in records:
            suspicion = "⚠️" if r.is_suspicious else "✓"
            direction = r.sentiment_direction[:6]
            source = r.source_url[:50] if r.source_url else "(文本输入)"
            print(f"{r.id:<14} {r.created_at.strftime('%m-%d %H:%M'):<18} "
                  f"{direction:<8} {suspicion:<8} {source}")
        return

    # --show <id>: 查看详情
    if args.show:
        record = journal.get(args.show)
        if not record:
            logger.error(f"未找到记录: {args.show}")
            return
        _print_article_detail(record)
        return

    # --update <id>: 追加 LLM 分析或交叉验证
    if args.update:
        record = journal.get(args.update)
        if not record:
            logger.error(f"未找到记录: {args.update}")
            return

        updates: dict = {}
        if args.llm_analysis:
            try:
                updates["llm_analysis"] = json.loads(args.llm_analysis)
            except json.JSONDecodeError:
                updates["llm_analysis"] = {"raw": args.llm_analysis}
        if args.cross_ref:
            try:
                updates["cross_ref"] = json.loads(args.cross_ref)
                updates["status"] = "cross_referenced"
            except json.JSONDecodeError:
                updates["cross_ref"] = {"raw": args.cross_ref}

        if updates:
            journal.update(args.update, **updates)
            logger.info(f"记录 {args.update} 已更新")
        else:
            logger.warning("未提供更新内容 (--llm-analysis / --cross-ref)")
        return

    # --predict <id>: 生成价格预判
    if args.predict:
        record = journal.get(args.predict)
        if not record:
            logger.error(f"未找到记录: {args.predict}")
            return

        if not args.direction:
            logger.error("预判需要 --direction (bullish/bearish/neutral)")
            return

        journal.update(
            args.predict,
            forecast_direction=args.direction,
            forecast_confidence=args.confidence or 0.5,
            forecast_horizon_days=args.horizon or 7,
            forecast_target_pct=args.target_pct or 0.0,
            forecast_reasoning=args.reasoning or "",
            status="forecasted",
        )
        logger.info(f"预判已保存: {args.direction} (置信度: {args.confidence or 0.5:.0%})")

        # 同步写入 PredictionTracker
        if settings.enable_auto_tracking:
            forecast_record = PredictionRecord(
                id=record.id,
                timestamp=record.created_at,
                current_price=0.0,  # 由 resolve 时填写
                signals=[],
                composite_score=record.sentiment_score,
                confidence=args.confidence or 0.5,
                direction=args.direction,
                position_pct=min(abs(record.sentiment_score) * 0.5, 0.5),
                dimension_scores={"article_analysis": record.sentiment_score},
            )
            PredictionTracker().record_prediction(forecast_record)
        return

    # 默认: 摄入并分析文章
    url_or_text = args.url or args.text
    if not url_or_text:
        logger.error("请提供 --url <文章链接> 或 --text <文章文本>")
        return

    _ingest_and_analyze(url_or_text, is_url=bool(args.url), deep=args.deep)


def _ingest_and_analyze(input_str: str, is_url: bool = False, deep: bool = False) -> None:
    """摄入文章并执行规则分析."""
    logger.info("=" * 60)
    logger.info("文章情报分析")
    logger.info("=" * 60)

    # 1. 读取
    if is_url:
        logger.info(f"抓取文章: {input_str}")
        text = ArticleReader.from_url(input_str)
        if not text:
            logger.error("文章抓取失败")
            return
    else:
        text = ArticleReader.from_text(input_str)

    logger.info(f"文章长度: {len(text)} 字符")

    # 2. 规则分析
    analyzer = ArticleAnalyzer()
    analysis = analyzer.analyze(text)

    # 3. 输出分析结果
    _print_analysis_result(analysis, text)

    # 4. 提取标题
    title = text[:80].replace("\n", " ").strip()
    if len(text) > 80:
        title += "..."

    # 5. 保存
    source_url = input_str if is_url else ""
    record = ArticleRecord(
        id=uuid.uuid4().hex[:12],
        source_url=source_url,
        title=title,
        text_preview=text[:200],
        word_count=analysis.word_count,
        sentiment_score=analysis.sentiment_score,
        sentiment_direction=analysis.sentiment_direction,
        manipulation_score=analysis.manipulation_score,
        manipulation_flags=analysis.manipulation_flags,
        is_suspicious=analysis.is_suspicious,
        claims=analysis.claims,
    )
    ArticleJournal().save(record)

    # 6. LLM 深度分析 (可选)
    if deep:
        logger.info("[LLM] 使用 DeepSeek 进行深度分析...")
        llm = LLMClient()
        llm_result = llm.analyze_article(
            text=text,
            rule_sentiment=analysis.sentiment_direction,
            rule_score=analysis.sentiment_score,
            rule_claims=analysis.claims,
            manipulation_flags=analysis.manipulation_flags,
        )

        if llm_result and not llm_result.get("parse_error"):
            journal = ArticleJournal()
            journal.update(record.id, llm_analysis=llm_result, status="cross_referenced")

            print()
            print("─" * 60)
            print("  LLM 深度分析 (DeepSeek)")
            print("─" * 60)
            print(f"  方向: {llm_result.get('sentiment', '?')}")
            print(f"  置信度: {llm_result.get('confidence', 0):.0%}")
            print(f"  可信度: {llm_result.get('credibility', 0):.0%}")
            print(f"  时间窗口: {llm_result.get('horizon_days', '?')}天")
            if llm_result.get("is_pumping"):
                print("  ⚠️ 疑似带节奏")
            if llm_result.get("is_institutional_manipulation"):
                print("  ⚠️ 疑似机构操纵")
            if llm_result.get("key_drivers"):
                print(f"  核心驱动: {', '.join(llm_result['key_drivers'])}")
            print(f"  推理: {llm_result.get('reasoning', '')[:200]}")
            print("─" * 60)

            # 自动生成预判
            llm_dir = llm_result.get("sentiment", "neutral")
            llm_conf = llm_result.get("confidence", 0.5)
            llm_horizon = llm_result.get("horizon_days", 7)
            reasoning = llm_result.get("reasoning", "")
            journal.update(
                record.id,
                forecast_direction=llm_dir,
                forecast_confidence=llm_conf,
                forecast_horizon_days=llm_horizon,
                forecast_reasoning=reasoning,
                status="forecasted",
            )
            logger.info(f"LLM 预判已自动保存: {llm_dir} (置信度: {llm_conf:.0%})")

            # 同步到 PredictionTracker
            if settings.enable_auto_tracking:
                forecast_record = PredictionRecord(
                    id=record.id,
                    timestamp=record.created_at,
                    current_price=0.0,
                    signals=[],
                    composite_score=analysis.sentiment_score,
                    confidence=llm_conf,
                    direction=llm_dir,
                    position_pct=min(abs(analysis.sentiment_score) * 0.5, 0.5),
                    dimension_scores={"article_llm": analysis.sentiment_score},
                )
                PredictionTracker().record_prediction(forecast_record)

            # EventStore: 记录文章预判 + 证据
            _record_article_prediction_events(
                record_id=record.id,
                analysis=analysis,
                direction=llm_dir,
                confidence=llm_conf,
                horizon_days=llm_horizon,
                reasoning=reasoning,
                source_url=source_url,
            )
        else:
            logger.warning("LLM 分析失败，使用规则分析结果")

    # 7. 提示下一步
    print()
    logger.info(f"分析已保存 (id: {record.id})")
    if not deep and analysis.claims:
        print("\n可交叉验证的关键主张:")
        for i, c in enumerate(analysis.claims[:5], 1):
            print(f"  {i}. [{c['category']}] {c['claim']}")
        print("\n提示: 使用 --deep 自动调用 DeepSeek 深度分析，或手动:")
        print(f"  gold-miner analyze --update {record.id} --cross-ref '{{...}}'")
    print(f"  gold-miner analyze --predict {record.id} --direction <bullish|bearish> --confidence <0.X>")


def _record_article_prediction_events(
    record_id: str,
    analysis,
    direction: str,
    confidence: float,
    horizon_days: int,
    reasoning: str = "",
    source_url: str = "",
) -> None:
    """向 EventStore 写入文章情报预判事件."""
    store = EventStore()

    refs: list[dict] = []
    if source_url:
        refs.append({
            "ref_type": "article",
            "ref_id": record_id,
            "url": source_url,
            "title": analysis.summary[:80] if analysis.summary else "",
        })
    for claim in analysis.claims[:5]:
        refs.append({
            "ref_type": "claim",
            "ref_id": record_id,
            "title": f"[{claim.get('category', '')}] {claim.get('claim', '')}",
            "description": claim.get("pattern", ""),
        })

    store.append(
        EventType.PREDICTION_MADE,
        record_id,
        {
            "direction": direction,
            "composite_score": round(analysis.sentiment_score, 4),
            "confidence": round(confidence, 4),
            "position_pct": round(min(abs(analysis.sentiment_score) * 0.5, 0.5), 2),
            "horizon_days": horizon_days,
            "source": "article",
            "auto_resolve": horizon_days <= 7,
            "current_price": 0.0,
        },
    )

    snapshot = EvidenceSnapshot.from_price_data(
        prediction_id=record_id,
        spot_gold=0.0,
        composite_score=round(analysis.sentiment_score, 4),
        confidence=round(confidence, 4),
        source_type="article",
        source_refs=refs,
        signals=[
            {
                "name": f"文章情感: {analysis.sentiment_direction}",
                "dimension": "news",
                "direction": direction,
                "score": analysis.sentiment_score,
                "description": f"操纵得分: {analysis.manipulation_score}/7, 字数: {analysis.word_count}",
            }
        ],
        dimension_scores={"article_analysis": analysis.sentiment_score},
    )
    store.append(
        EventType.EVIDENCE_ATTACHED,
        record_id,
        {"snapshot": snapshot},
    )

    logger.debug(f"EventStore 已记录文章预判: {record_id[:8]}... ({direction})")


def _print_analysis_result(analysis, text: str) -> None:
    """打印规则分析结果."""
    print()
    print("─" * 60)
    print("  规则分析结果")
    print("─" * 60)

    icon = "📈" if analysis.sentiment_direction == "bullish" else "📉" if analysis.sentiment_direction == "bearish" else "➡️"
    print(f"  {icon} 情感倾向: {analysis.sentiment_direction} "
          f"(得分: {analysis.sentiment_score:+.2f})")
    print(f"     看涨词: {analysis.bullish_count}个 | 看跌词: {analysis.bearish_count}个")

    print()
    if analysis.is_suspicious:
        print(f"  ⚠️ 可信度: 疑似带节奏 ({analysis.manipulation_score}/7项)")
        for flag in analysis.manipulation_flags:
            print(f"     - {flag}")
    else:
        print(f"  ✓ 可信度: 暂未检测到明显操纵话术 ({analysis.manipulation_score}/7项)")

    if analysis.claims:
        print(f"\n  📋 关键主张 ({len(analysis.claims)}条):")
        for c in analysis.claims:
            print(f"     [{c['category']}] {c['claim']}")

    print("─" * 60)


def _print_article_detail(record) -> None:
    """打印文章分析详情."""
    print()
    print("=" * 60)
    print(f"  文章分析详情: {record.id}")
    print("=" * 60)
    print(f"  来源: {record.source_url or '(文本输入)'}")
    print(f"  时间: {record.created_at.strftime('%Y-%m-%d %H:%M')}")
    print(f"  字数: {record.word_count}")
    print()
    print(f"  情感方向: {record.sentiment_direction} ({record.sentiment_score:+.2f})")
    print(f"  操纵得分: {record.manipulation_score}/7 {'⚠️' if record.is_suspicious else '✓'}")
    if record.manipulation_flags:
        for f in record.manipulation_flags:
            print(f"    - {f}")
    print()
    if record.claims:
        print("  关键主张:")
        for c in record.claims:
            print(f"    [{c['category']}] {c['claim']}")
    print()
    if record.llm_analysis:
        print(f"  LLM分析: {json.dumps(record.llm_analysis, ensure_ascii=False)[:200]}")
    if record.cross_ref:
        print(f"  交叉验证: {json.dumps(record.cross_ref, ensure_ascii=False)[:200]}")
    if record.forecast_direction:
        print(f"  价格预判: {record.forecast_direction} "
              f"(置信度: {record.forecast_confidence:.0%}, "
              f"窗口: {record.forecast_horizon_days}天)")
        if record.forecast_reasoning:
            print(f"  推理: {record.forecast_reasoning[:200]}")
    print("=" * 60)
