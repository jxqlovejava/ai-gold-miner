"""缠论信号桥接测试 — ChanlunResult → Signal 体系 + pipeline 接入检查。"""
import numpy as np
import pandas as pd

from gold_miner.signals.base import SignalDirection, SignalStrength
from gold_miner.signals.chanlun.schema import ChanlunPoint, ChanlunResult, ZhongShu
from gold_miner.signals.chanlun_signal import ChanlunSignalGenerator


def _make_df(n=120, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.3, n),
        "high": close + rng.uniform(0, 1, n),
        "low": close - rng.uniform(0, 1, n),
        "close": close,
        "volume": 1e6,
    }, index=idx)


def test_generate_signals_dimension_technical():
    gen = ChanlunSignalGenerator(_make_df())
    sigs = gen.generate_signals()
    assert all(s.dimension == "technical" for s in sigs)
    assert all(s.strength == SignalStrength.WEAK for s in sigs)


def test_data_gap_returns_empty():
    gen = ChanlunSignalGenerator(_make_df(20))
    assert gen.generate_signals() == []
    d = gen.summary_dict()
    assert "gap" in d["current_state"]


def test_point_signal_buy_maps_bullish():
    p = ChanlunPoint(kind="一买", dt="2025-03-01", price=98.0,
                     confidence=0.7, rationale="下降末段底背驰+底分型确认")
    sig = ChanlunSignalGenerator._point_signal(p)
    assert sig is not None
    assert sig.direction == SignalDirection.BULLISH
    assert "缠论一买" in sig.name
    assert sig.score > 0
    assert sig.metadata["source_tier"] == "T2"
    assert "分批建仓锚点参考" in sig.description


def test_recent_points_filters_ancient_and_dedupes():
    """远古买卖点不作活跃信号; 买/卖点各保留最新 1 个."""
    points = [
        ChanlunPoint(kind="一买", dt="2020-01-01", price=277.0, confidence=0.7, rationale="远古"),
        ChanlunPoint(kind="一买", dt="2026-08-01", price=900.0, confidence=0.7, rationale="近期"),
        ChanlunPoint(kind="三买", dt="2026-08-03", price=860.0, confidence=0.8, rationale="最新"),
        ChanlunPoint(kind="一卖", dt="2026-07-15", price=920.0, confidence=0.7, rationale="近期卖"),
        ChanlunPoint(kind="一卖", dt="2021-01-01", price=1800.0, confidence=0.7, rationale="远古卖"),
    ]
    recent = ChanlunSignalGenerator._recent_points(points, recency_days=45)
    kinds = sorted(p.kind for p in recent)
    assert kinds == ["一卖", "三买"]          # 买点取最新三买, 卖点取最近一卖
    assert all(p.price != 277.0 and p.price != 1800.0 for p in recent)


def test_recent_points_empty():
    assert ChanlunSignalGenerator._recent_points([], recency_days=45) == []


def test_point_signal_sell_maps_bearish_as_reduce_ref():
    p = ChanlunPoint(kind="一卖", dt="2025-03-01", price=105.0,
                     confidence=0.7, rationale="上升末段顶背驰")
    sig = ChanlunSignalGenerator._point_signal(p)
    assert sig is not None
    assert sig.direction == SignalDirection.BEARISH
    assert sig.score < 0
    assert "减仓/止盈参考" in sig.description


def test_summary_dict_structure():
    gen = ChanlunSignalGenerator(_make_df())
    d = gen.summary_dict()
    for k in ("backend", "freq", "bi_count", "zhongshu_count",
              "points", "current_state", "signals", "confidence"):
        assert k in d
    assert d["freq"] == "D"


def test_generate_signals_with_points_and_zs():
    """构造含买卖点+中枢的结果 → generate_signals 产出方向正确。"""
    zs = ZhongShu(zg=100.0, zd=96.0, zz=98.0, gg=102.0, dd=94.0,
                  start_dt="2025-01-01", end_dt="2025-02-01", state="形成")
    points = [
        ChanlunPoint(kind="一买", dt="2025-02-05", price=94.5, confidence=0.7,
                     rationale="下降末段底背驰"),
        ChanlunPoint(kind="三买", dt="2025-03-01", price=101.0, confidence=0.8,
                     rationale="突破中枢ZG后回抽不进入中枢"),
    ]
    r = ChanlunResult(symbol="Au99.99", name="黄金", freq="D", backend="self",
                      fractals=[], bis=[], zhongshus=[zs], points=points,
                      current_state={"last_close": 103.0, "bi_count": 6,
                                     "zhongshu_state": "形成",
                                     "zg": 100.0, "zd": 96.0, "zz": 98.0,
                                     "position": "中枢上方"},
                      signals={"entry": [], "exit": []},
                      source_citations=[], confidence=0.75)

    gen = ChanlunSignalGenerator(_make_df())
    gen._result = r  # 注入构造结果，绕过真实分析
    sigs = gen.generate_signals()
    bulls = [s for s in sigs if s.direction == SignalDirection.BULLISH]
    # 最近窗口过滤: 仅保留最新的买点（三买@2025-03-01），一买@2025-02-05 被最新买点覆盖
    assert len(bulls) == 1
    assert any("缠论三买" in s.name for s in bulls)
    assert not any("缠论一买" in s.name for s in sigs)
    assert all(s.metadata["source_tier"] == "T2" for s in sigs)


def test_break_below_zhongshu_bearish():
    zs = ZhongShu(zg=100.0, zd=96.0, zz=98.0, gg=102.0, dd=94.0,
                  start_dt="2025-01-01", end_dt="2025-02-01", state="形成")
    r = ChanlunResult(symbol="Au99.99", name="黄金", freq="D", backend="self",
                      fractals=[], bis=[], zhongshus=[zs], points=[],
                      current_state={"last_close": 95.0, "bi_count": 6,
                                     "zhongshu_state": "形成",
                                     "zg": 100.0, "zd": 96.0, "zz": 98.0,
                                     "position": "中枢下方"},
                      signals={"entry": [], "exit": []},
                      source_citations=[], confidence=0.75)

    gen = ChanlunSignalGenerator(_make_df())
    gen._result = r
    sigs = gen.generate_signals()
    assert any(s.name == "缠论跌破中枢下沿" and
               s.direction == SignalDirection.BEARISH for s in sigs)


# ----------------------------------------------------------------------
# pipeline 接入检查 + 报告板块
# ----------------------------------------------------------------------

def test_pipeline_wires_chanlun():
    """主 scan 信号步必须注册缠论生成器."""
    import inspect

    from gold_miner.pipeline import analysis as analysis_mod

    src = inspect.getsource(analysis_mod.AnalysisPipeline._step_generate_signals)
    assert "ChanlunSignalGenerator" in src
    assert '"chanlun"' in src or "'chanlun'" in src


def test_format_chanlun_structure_block():
    from gold_miner.pipeline.analysis import AnalysisPipeline

    summary = {
        "backend": "self", "freq": "D", "bi_count": 6, "zhongshu_count": 1,
        "last_zs": {"zg": 100.0, "zd": 96.0, "zz": 98.0, "state": "上移",
                    "gg": 102.0, "dd": 94.0},
        "points": [
            {"kind": "三买", "dt": "2025-03-01", "price": 101.0,
             "confidence": 0.8, "rationale": "突破中枢ZG后回抽"},
        ],
        "current_state": {"last_close": 103.0, "position": "中枢上方"},
        "signals": {"entry": [], "exit": []}, "confidence": 0.75,
    }
    block = AnalysisPipeline._format_chanlun_structure(summary)
    assert "缠论结构" in block
    assert "ZD 96.0" in block and "ZG 100.0" in block
    assert "中枢上方" in block
    assert "三买" in block and "分批建仓锚点参考" in block


def test_format_chanlun_structure_gap_returns_empty():
    from gold_miner.pipeline.analysis import AnalysisPipeline

    summary = {"current_state": {"gap": "[DATA_GAP] 缠论: 数据不足30根"},
               "confidence": 0.0}
    assert AnalysisPipeline._format_chanlun_structure(summary) == ""
