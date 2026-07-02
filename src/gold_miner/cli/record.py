"""经济数据记录 CLI — 手动保存和查询关键经济指标发布值.

用法:
    # 单条保存
    gold-miner record save \\
        --indicator nonfarm_payrolls \\
        --release-date 2026-07-02 \\
        --actual 57000 --forecast 114000 --previous 172000 \\
        --period 2026-06 --unit "人" \\
        --source "BLS" --source-tier T0 \\
        --gold-price 4110 --dxy 101.38

    # 批量保存（从 JSON 文件）
    gold-miner record batch data/nfp_20260702.json

    # 批量保存（从 stdin）
    cat data.json | gold-miner record batch --stdin

    # 列出所有批次
    gold-miner record batches

    # 查看某个批次的详情
    gold-miner record show --batch-id nfp_20260702

    # 导出某批次为 JSON（用于回测）
    gold-miner record export --batch-id nfp_20260702
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.data.economic_data import (
    EconomicDataPoint,
    EconomicDataRecorder,
    MarketSnapshot,
)


def run_record(args: argparse.Namespace) -> None:
    """处理 record 命令 — 用自己的 parser 解析 sys.argv 中的 record 子命令."""
    # 构建 record 专用 parser 来解析 `gold-miner record <subcommand> ...`
    record_parser = argparse.ArgumentParser(
        prog="gold-miner record",
        description="经济数据记录 — 保存/查询关键经济指标发布值",
    )
    record_sub = record_parser.add_subparsers(dest="record_action")

    _build_save_parser(record_sub)
    _build_batch_parser(record_sub)
    record_sub.add_parser("batches", help="列出所有批次")
    show_p = record_sub.add_parser("show", help="查看批次详情")
    show_p.add_argument("--batch-id", required=True)
    export_p = record_sub.add_parser("export", help="导出批次为 JSON")
    export_p.add_argument("--batch-id", required=True)
    export_p.add_argument("--output", "-o", default=None)

    # 从 sys.argv 中找到 record 及其后面的参数
    try:
        idx = sys.argv.index("record")
        record_args = record_parser.parse_args(sys.argv[idx + 1:])
    except (ValueError, SystemExit):
        record_parser.print_help()
        sys.exit(1)

    recorder = EconomicDataRecorder()

    if record_args.record_action == "save":
        _run_save(record_args, recorder)
    elif record_args.record_action == "batch":
        _run_batch(record_args, recorder)
    elif record_args.record_action == "batches":
        _run_batches(recorder)
    elif record_args.record_action == "show":
        _run_show(record_args, recorder)
    elif record_args.record_action == "export":
        _run_export(record_args, recorder)
    else:
        record_parser.print_help()
        sys.exit(1)


def _build_save_parser(record_sub: argparse._SubParsersAction) -> None:
    save_parser = record_sub.add_parser("save", help="保存单条经济数据")
    save_parser.add_argument("--indicator", required=True, help="指标名称")
    save_parser.add_argument("--release-date", required=True, help="发布日期 YYYY-MM-DD")
    save_parser.add_argument("--actual", type=str, required=True, help="实际值")
    save_parser.add_argument("--forecast", type=str, default=None, help="预期值")
    save_parser.add_argument("--previous", type=str, default=None, help="前值")
    save_parser.add_argument("--period", default="")
    save_parser.add_argument("--unit", default="")
    save_parser.add_argument("--source", default="")
    save_parser.add_argument("--source-tier", default="unknown", choices=["T0", "T1", "T2", "T3"])
    save_parser.add_argument("--impact", default="high", choices=["high", "medium", "low"])
    save_parser.add_argument("--notes", default="")
    save_parser.add_argument("--observation-date", default="")
    save_parser.add_argument("--batch-id", default="")
    save_parser.add_argument("--gold-price", type=float, default=None, help="现货黄金 XAUUSD")
    save_parser.add_argument("--au9999", type=float, default=None, help="Au9999 元/克")
    save_parser.add_argument("--dxy", type=float, default=None, help="美元指数")
    save_parser.add_argument("--us-10y", type=float, default=None)
    save_parser.add_argument("--us-2y", type=float, default=None)
    save_parser.add_argument("--usd-cny", type=float, default=None)
    save_parser.add_argument("--silver", type=float, default=None)
    save_parser.add_argument("--wti", type=float, default=None)
    save_parser.add_argument("--vix", type=float, default=None)
    save_parser.add_argument("--fed-rate", type=float, default=None)
    save_parser.add_argument("--cme-hike", type=float, default=None)
    save_parser.add_argument("--force", action="store_true")


def _build_batch_parser(record_sub: argparse._SubParsersAction) -> None:
    batch_parser = record_sub.add_parser("batch", help="批量保存经济数据")
    batch_parser.add_argument("file", nargs="?", default=None, help="JSON 文件路径")
    batch_parser.add_argument("--stdin", action="store_true", help="从标准输入读取 JSON")
    batch_parser.add_argument("--force", action="store_true")


# ------------------------------------------------------------------
# 子命令实现
# ------------------------------------------------------------------


def _parse_value(raw: str | None) -> float | int | str | None:
    """智能解析值：尝试 int → float → str."""
    if raw is None:
        return None
    if raw.lower() in ("null", "none", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _build_snapshot(args: argparse.Namespace) -> MarketSnapshot | None:
    """从 CLI 参数构建市场快照."""
    has_snapshot = any(
        getattr(args, f, None) is not None
        for f in [
            "gold_price", "dxy", "us_10y", "us_2y", "usd_cny",
            "silver", "wti", "vix", "fed_rate", "cme_hike", "au9999",
        ]
    )
    if not has_snapshot:
        return None

    return MarketSnapshot(
        captured_at=datetime.now().isoformat(),
        spot_gold_usd=args.gold_price,
        au9999_cny=args.au9999,
        dxy=args.dxy,
        us_10y_yield=args.us_10y,
        us_2y_yield=args.us_2y,
        usd_cny=args.usd_cny,
        silver_usd=args.silver,
        wti_oil=args.wti,
        vix=args.vix,
        fed_rate=args.fed_rate,
        cme_fedwatch_hike_prob=args.cme_hike,
    )


def _run_save(args: argparse.Namespace, recorder: EconomicDataRecorder) -> None:
    """保存单条经济数据."""
    snapshot = _build_snapshot(args)

    point = EconomicDataPoint(
        indicator=args.indicator,
        release_date=args.release_date,
        actual=_parse_value(args.actual),
        forecast=_parse_value(args.forecast),
        previous=_parse_value(args.previous),
        observation_date=args.observation_date,
        unit=args.unit,
        period=args.period,
        source=args.source,
        source_tier=args.source_tier,
        impact=args.impact,
        notes=args.notes,
        batch_id=args.batch_id,
        market_snapshot=snapshot,
    )

    if recorder.save(point, force=args.force):
        print(f"✅ 已保存: {point.indicator} @ {point.release_date}")
        if snapshot:
            gold_str = f"${snapshot.spot_gold_usd:.0f}" if snapshot.spot_gold_usd else "N/A"
            print(f"   📊 市场快照: 黄金 {gold_str} | DXY {snapshot.dxy or 'N/A'}")
    else:
        print(f"⏭️  已跳过 (重复): {point.indicator} @ {point.release_date}")


def _run_batch(args: argparse.Namespace, recorder: EconomicDataRecorder) -> None:
    """批量保存经济数据."""
    raw: dict[str, Any] | list[dict[str, Any]] = {}

    if args.stdin:
        raw = json.load(sys.stdin)
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            logger.error(f"文件不存在: {path}")
            sys.exit(1)
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        logger.error("请提供 JSON 文件路径或使用 --stdin")
        sys.exit(1)

    # 统一为列表
    if isinstance(raw, dict):
        # {"batch_id": "...", "snapshot": {...}, "data": [...]}
        batch_id = raw.get("batch_id", "")
        snapshot_data = raw.get("snapshot")
        items = raw.get("data", [raw])
    elif isinstance(raw, list):
        batch_id = ""
        snapshot_data = None
        items = raw
    else:
        logger.error("JSON 格式错误：需要对象或数组")
        sys.exit(1)

    snapshot = MarketSnapshot.from_dict(snapshot_data) if snapshot_data else None

    points: list[EconomicDataPoint] = []
    for item in items:
        item_snapshot_data = item.pop("market_snapshot", None) or snapshot_data
        item_snapshot = (
            MarketSnapshot.from_dict(item_snapshot_data)
            if item_snapshot_data
            else snapshot
        )
        points.append(EconomicDataPoint(
            indicator=item.get("indicator", ""),
            release_date=item.get("release_date", ""),
            actual=_parse_value(item.get("actual")),
            forecast=_parse_value(item.get("forecast")),
            previous=_parse_value(item.get("previous")),
            observation_date=item.get("observation_date", ""),
            unit=item.get("unit", ""),
            period=item.get("period", ""),
            source=item.get("source", ""),
            source_tier=item.get("source_tier", "unknown"),
            impact=item.get("impact", "high"),
            notes=item.get("notes", ""),
            batch_id=item.get("batch_id", batch_id),
            market_snapshot=item_snapshot,
        ))

    saved = recorder.save_batch(points, batch_id=batch_id, force=args.force)
    print(f"✅ 批量保存完成: {saved}/{len(points)} 条")


def _run_batches(recorder: EconomicDataRecorder) -> None:
    """列出所有批次."""
    batches = recorder.list_batches()
    if not batches:
        print("📭 暂无批次记录")
        return

    print(f"{'批次ID':<25} {'日期':<12} {'条数':<6} {'快照':<6} {'指标'}")
    print("-" * 80)
    for b in batches:
        snap = "✅" if b["has_snapshot"] else "❌"
        indicators = ", ".join(b["indicators"][:4])
        if len(b["indicators"]) > 4:
            indicators += f" ...+{len(b['indicators']) - 4}"
        print(f"{b['batch_id']:<25} {b['release_date']:<12} {b['count']:<6} {snap:<6} {indicators}")


def _run_show(args: argparse.Namespace, recorder: EconomicDataRecorder) -> None:
    """查看批次详情."""
    points = recorder.find_batch(args.batch_id)
    if not points:
        print(f"📭 批次 {args.batch_id} 无记录")
        return

    # 取第一条的快照
    snapshot = None
    for p in points:
        if p.market_snapshot:
            snapshot = p.market_snapshot
            break

    print(f"\n📦 批次: {args.batch_id}")
    print(f"   日期: {points[0].release_date}  |  指标数: {len(points)}")
    if snapshot:
        print(f"   📊 市场快照:")
        if snapshot.spot_gold_usd:
            print(f"      现货黄金: ${snapshot.spot_gold_usd:.0f}/oz")
        if snapshot.au9999_cny:
            print(f"      Au9999: {snapshot.au9999_cny:.0f} 元/克")
        if snapshot.dxy:
            print(f"      DXY: {snapshot.dxy:.2f}")
        if snapshot.usd_cny:
            print(f"      USD/CNY: {snapshot.usd_cny:.4f}")
        if snapshot.us_10y_yield:
            print(f"      10Y 美债: {snapshot.us_10y_yield:.2f}%")
        if snapshot.cme_fedwatch_hike_prob is not None:
            print(f"      FedWatch 加息概率: {snapshot.cme_fedwatch_hike_prob:.0%}")
    print()
    print(f"  {'指标':<25} {'实际':>10} {'预期':>10} {'前值':>10} {'单位'}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*10} {'-'*6}")
    for p in points:
        actual_str = f"{p.actual:,}" if isinstance(p.actual, (int, float)) else str(p.actual or "-")
        forecast_str = f"{p.forecast:,}" if isinstance(p.forecast, (int, float)) else str(p.forecast or "-")
        previous_str = f"{p.previous:,}" if isinstance(p.previous, (int, float)) else str(p.previous or "-")
        print(f"  {p.indicator:<25} {actual_str:>10} {forecast_str:>10} {previous_str:>10} {p.unit:<6}")


def _run_export(args: argparse.Namespace, recorder: EconomicDataRecorder) -> None:
    """导出批次为 JSON."""
    points = recorder.find_batch(args.batch_id)
    if not points:
        logger.error(f"批次 {args.batch_id} 无记录")
        sys.exit(1)

    snapshot = None
    for p in points:
        if p.market_snapshot:
            snapshot = p.market_snapshot.to_dict()
            break

    output = {
        "batch_id": args.batch_id,
        "release_date": points[0].release_date,
        "exported_at": datetime.now().isoformat(),
        "snapshot": snapshot,
        "data": [p.to_dict() for p in points],
    }

    json_str = json.dumps(output, ensure_ascii=False, indent=2, default=str)

    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"✅ 已导出到: {args.output}")
    else:
        print(json_str)
