"""缠论核心算法测试 — 从 ai-stock-hunter 移植 (去包含/分型/笔/中枢/背驰/买卖点/分析器)."""
import numpy as np
import pandas as pd

from gold_miner.signals.chanlun.analyzer import ChanlunAnalyzer
from gold_miner.signals.chanlun.core.bi import build_bis
from gold_miner.signals.chanlun.core.bihuang import detect_divergence
from gold_miner.signals.chanlun.core.fractal import detect_fractals
from gold_miner.signals.chanlun.core.merge import MergedBar, merge_bars
from gold_miner.signals.chanlun.core.zhongshu import detect_zhongshus
from gold_miner.signals.chanlun.points import detect_points
from gold_miner.signals.chanlun.schema import Bi, ChanlunPoint, ChanlunResult, Fractal, ZhongShu

# ----------------------------------------------------------------------
# 去包含 merge
# ----------------------------------------------------------------------

def _make_ohlc_df(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_up_direction_merge_takes_larger():
    df = _make_ohlc_df([
        (8, 9, 7, 8.5),        # [7,9]  首根
        (8.5, 10, 8, 9),       # [8,10] 无包含, high↑ → direction=up
        (9, 9.5, 8.5, 9.2),    # [8.5,9.5] 被 [8,10] 包含 → 取较大高/较大低
    ])
    merged = merge_bars(df)
    assert len(merged) == 2            # 3 根合并为 2
    assert merged[-1].high == 10.0     # max(10, 9.5)
    assert merged[-1].low == 8.5       # max(8, 8.5)
    assert merged[-1].direction == "up"


def test_down_direction_merge_takes_smaller():
    df = _make_ohlc_df([
        (10, 12, 9, 10.5),     # [9,12] 首根
        (9.5, 10.5, 8.5, 9),   # [8.5,10.5] 无包含, high↓ → direction=down
        (9, 9.5, 8.8, 9.2),    # [8.8,9.5] 被 [8.5,10.5] 包含 → 取较小高/较小低
    ])
    merged = merge_bars(df)
    assert len(merged) == 2
    assert merged[-1].high == 9.5      # min(10.5, 9.5)
    assert merged[-1].low == 8.5       # min(8.5, 8.8)
    assert merged[-1].direction == "down"


def test_no_containment_keeps_all_bars():
    df = _make_ohlc_df([
        (8, 9, 7, 8.5), (8.5, 10, 8, 9), (9, 11, 8.8, 10.5),
    ])
    merged = merge_bars(df)
    assert len(merged) == 3
    assert merged[-1].direction == "up"


def test_empty_df_returns_empty():
    df = _make_ohlc_df([])
    assert merge_bars(df) == []


def test_partial_overlap_not_merged():
    # 前根 [10,12], 当前 [7,9] → 当前整体低于前根, 非包含 → 不合并
    df = _make_ohlc_df([(10, 12, 9, 10.5), (8, 9, 7, 8)])
    merged = merge_bars(df)
    assert len(merged) == 2


# ----------------------------------------------------------------------
# 分型 fractal
# ----------------------------------------------------------------------

def _merged(rows):
    return merge_bars(_make_ohlc_df(rows))


def test_top_fractal():
    merged = _merged([(8, 9, 7, 8), (9, 11, 8.5, 10.5), (10, 10.5, 8, 10)])
    fs = detect_fractals(merged)
    assert len(fs) == 1
    assert fs[0].mark == "G"
    assert fs[0].fx == 11.0


def test_bottom_fractal():
    merged = _merged([(9, 10, 8, 9.5), (8, 8.5, 6.5, 7), (7.5, 9, 7, 7.8)])
    fs = detect_fractals(merged)
    assert len(fs) == 1
    assert fs[0].mark == "D"
    assert fs[0].fx == 6.5


def test_flat_middle_no_fractal_direct():
    # 等高中间根 → 平盘不误判（严格比较分支）
    merged = [
        MergedBar(0, pd.Timestamp("2026-01-01"), 10.0, 7.0, "up"),
        MergedBar(1, pd.Timestamp("2026-01-02"), 10.0, 8.0, "up"),
        MergedBar(2, pd.Timestamp("2026-01-03"), 10.0, 8.5, "up"),
    ]
    assert detect_fractals(merged) == []


def test_insufficient_bars():
    merged = _merged([(8, 9, 7, 8), (9, 10, 8, 9)])
    assert detect_fractals(merged) == []


# ----------------------------------------------------------------------
# 笔 bi
# ----------------------------------------------------------------------

def _fx(mark, index, fx):
    if mark == "G":
        return Fractal(mark="G", dt=index, high=fx, low=fx - 1, fx=fx, index=index)
    return Fractal(mark="D", dt=index, high=fx + 1, low=fx, fx=fx, index=index)


def test_build_bis_alternates():
    fs = [_fx("D", 0, 10), _fx("G", 5, 20), _fx("D", 10, 12), _fx("G", 16, 25)]
    bis = build_bis(fs, min_len=4)
    assert len(bis) == 3
    assert [b.direction for b in bis] == ["up", "down", "up"]


def test_bi_min_length_rejected():
    fs = [_fx("D", 0, 10), _fx("G", 2, 20)]   # gap=2 < 4
    assert build_bis(fs, min_len=4) == []


def test_consecutive_same_mark_keeps_extreme():
    fs = [_fx("D", 0, 10), _fx("G", 5, 20), _fx("G", 7, 25), _fx("D", 12, 15)]
    bis = build_bis(fs, min_len=4)
    assert len(bis) == 2
    assert bis[0].end_fx.fx == 25          # 保留更高的顶
    assert bis[0].direction == "up" and bis[1].direction == "down"


def test_bi_high_low_from_endpoints():
    fs = [_fx("D", 0, 10), _fx("G", 5, 20)]
    bis = build_bis(fs, min_len=4)
    assert bis[0].high == 20.0 and bis[0].low == 10.0
    assert bis[0].start_fx.mark == "D" and bis[0].end_fx.mark == "G"


def test_no_consecutive_same_direction_after_swallow():
    # 回归 Bug2: 旧顶 G(20)@5 被新高 G(30)@12 吞没 → 迭代版应严格交替
    fs = [_fx("D", 0, 10), _fx("G", 5, 20), _fx("D", 7, 15), _fx("G", 12, 30)]
    bis = build_bis(fs, min_len=4)
    dirs = [b.direction for b in bis]
    assert len(dirs) >= 1
    assert all(dirs[i] != dirs[i + 1] for i in range(len(dirs) - 1))
    assert bis[-1].end_fx.fx == 30.0


# ----------------------------------------------------------------------
# 中枢 zhongshu
# ----------------------------------------------------------------------

def _bi(direction, high, low, area=0.0):
    if direction == "up":
        fx_a = Fractal(mark="D", dt=0, high=low + 1, low=low, fx=low, index=0)
        fx_b = Fractal(mark="G", dt=5, high=high, low=high - 1, fx=high, index=5)
    else:
        fx_a = Fractal(mark="G", dt=0, high=high, low=high - 1, fx=high, index=0)
        fx_b = Fractal(mark="D", dt=5, high=low + 1, low=low, fx=low, index=5)
    return Bi(direction=direction, start_fx=fx_a, end_fx=fx_b, high=high, low=low,
              length=5, macd_area=area, start_dt=0, end_dt=5)


def test_zhongshu_valid_overlap():
    bis = [_bi("up", 20, 10), _bi("down", 18, 12), _bi("up", 22, 15)]
    zss = detect_zhongshus(bis)
    assert len(zss) == 1
    zs = zss[0]
    assert zs.zg == 18.0     # min(20,18,22)
    assert zs.zd == 15.0     # max(10,12,15)
    assert zs.zg > zs.zd
    assert zs.state == "形成"


def test_no_overlap_no_zhongshu():
    bis = [_bi("up", 10, 1), _bi("down", 20, 11), _bi("up", 30, 21)]
    assert detect_zhongshus(bis) == []


def test_zhongshu_move_up_state():
    bis = [
        _bi("up", 18, 12), _bi("down", 16, 15), _bi("up", 17, 13),    # 中枢1 [15,16]
        _bi("up", 26, 21), _bi("down", 24, 22), _bi("up", 25, 23),    # 中枢2 [23,24]
    ]
    zss = detect_zhongshus(bis)
    assert len(zss) == 2
    assert zss[1].zd > zss[0].zg      # 23 > 16 → 上移
    assert zss[1].state == "上移"


# ----------------------------------------------------------------------
# 背驰 bihuang
# ----------------------------------------------------------------------

def test_bottom_divergence():
    bis = [_bi("down", 30, 20, area=100.0), _bi("up", 25, 18, area=30.0),
           _bi("down", 22, 15, area=50.0)]     # 低点15<20 且面积50<100
    div = detect_divergence(bis)
    assert 2 in div and div[2]["type"] == "bottom"


def test_top_divergence():
    bis = [_bi("up", 20, 10, area=100.0), _bi("down", 15, 8, area=30.0),
           _bi("up", 25, 12, area=60.0)]       # 高点25>20 且面积60<100
    div = detect_divergence(bis)
    assert 2 in div and div[2]["type"] == "top"


def test_no_divergence_when_force_grows():
    bis = [_bi("down", 30, 20, area=50.0), _bi("up", 25, 18, area=30.0),
           _bi("down", 22, 15, area=80.0)]     # 低点15<20 但面积80>50
    assert detect_divergence(bis) == {}


# ----------------------------------------------------------------------
# 买卖点 points
# ----------------------------------------------------------------------

def test_first_buy_and_second_buy():
    bis = [
        _bi("down", 40, 30, area=100.0), _bi("up", 36, 32, area=30.0),
        _bi("down", 35, 31, area=80.0),
        _bi("up", 34, 33, area=20.0),
        _bi("down", 30, 24, area=40.0),   # 底背驰 → 一买@24
        _bi("up", 30, 26, area=20.0),
        _bi("down", 27, 25, area=30.0),   # 回调不破 → 二买@25
    ]
    zss = detect_zhongshus(bis)
    points = detect_points(bis, zss, {4: {"type": "bottom", "bi_index": 4}})
    assert {(p.kind, p.price) for p in points} == {
        ("一买", 24.0), ("二买", 25.0), ("三卖", 30.0),
    }


def test_third_buy_after_breakout():
    bis = [
        _bi("down", 40, 30), _bi("up", 36, 32), _bi("down", 35, 31),   # 中枢 [32,35]
        _bi("up", 40, 33),                                             # 突破 zg=35
        _bi("down", 38, 36),                                           # 回抽低点36>35 → 三买
    ]
    zss = detect_zhongshus(bis)
    points = detect_points(bis, zss, {})
    assert any(p.kind == "三买" and p.price == 36.0 for p in points)


def test_first_sell_and_second_sell_mirror():
    bis = [
        _bi("up", 20, 10, area=100.0), _bi("down", 16, 12, area=30.0),
        _bi("up", 22, 15, area=80.0),
        _bi("down", 17, 13, area=20.0),
        _bi("up", 28, 20, area=40.0),   # 顶背驰 → 一卖@28
        _bi("down", 24, 18, area=20.0),
        _bi("up", 27, 21, area=30.0),   # 反弹不破 → 二卖@27
    ]
    zss = detect_zhongshus(bis)
    points = detect_points(bis, zss, {4: {"type": "top", "bi_index": 4}})
    assert {(p.kind, p.price) for p in points} == {
        ("一卖", 28.0), ("二卖", 27.0), ("三买", 18.0),
    }


def test_ancient_zhongshu_no_misfire():
    # 远古中枢A不应触发三买
    bis = [
        _bi("up", 20, 10), _bi("down", 18, 12), _bi("up", 22, 15),      # 中枢A [15,18]
        _bi("up", 30, 19), _bi("down", 28, 21), _bi("up", 32, 24),      # 中枢B [24,28]
        _bi("up", 40, 29), _bi("down", 38, 27), _bi("up", 42, 34),      # 中枢C [34,38]
        _bi("down", 25, 20),                                            # 假触发: 20>A.zg18
    ]
    zss = detect_zhongshus(bis)
    assert len(zss) == 3
    points = detect_points(bis, zss, {})
    assert points == []


# ----------------------------------------------------------------------
# schema DTO
# ----------------------------------------------------------------------

def test_fractal_fields():
    f = Fractal(mark="G", dt="2026-01-05", high=12.0, low=10.0, fx=12.0, index=4)
    assert f.mark == "G" and f.fx == 12.0 and f.index == 4


def test_result_to_summary_dict():
    zs = ZhongShu(zg=18.0, zd=15.0, zz=16.5, gg=20.0, dd=12.0,
                  start_dt="2026-01-01", end_dt="2026-01-10", state="形成")
    p = ChanlunPoint(kind="一买", dt="2026-02-01", price=15.0, confidence=0.7,
                     rationale="下降末段底背驰")
    r = ChanlunResult(symbol="Au99.99", name="黄金", freq="D", backend="self",
                      fractals=[], bis=[], zhongshus=[zs], points=[p],
                      current_state={"position": "中枢内"}, signals={"entry": [], "exit": []},
                      source_citations=[], confidence=0.8)
    d = r.to_summary_dict()
    assert d["backend"] == "self"
    assert d["zhongshu_count"] == 1
    assert d["last_zs"]["zg"] == 18.0
    assert d["points"][0]["kind"] == "一买"
    assert d["signals"] == {"entry": [], "exit": []}


# ----------------------------------------------------------------------
# analyzer 组合入口
# ----------------------------------------------------------------------

def _make_df(n=120, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0, 1, n)
    low = close - rng.uniform(0, 1, n)
    open_ = close + rng.normal(0, 0.3, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": 1e6}, index=idx)


def test_analyzer_produces_result():
    df = _make_df()
    r = ChanlunAnalyzer().analyze(df, "Au99.99", "黄金")
    assert r.symbol == "Au99.99"
    assert r.freq == "D"
    assert r.backend == "self"          # 纯自研核心
    assert len(r.source_citations) >= 1
    assert 0.0 <= r.confidence <= 1.0
    assert r.current_state["last_close"] == float(df["close"].iloc[-1])


def test_analyzer_data_gap_short():
    df = _make_df(20)                   # <30 根
    r = ChanlunAnalyzer().analyze(df, "Au99.99", "黄金")
    assert r.bis == [] and r.zhongshus == []
    assert "gap" in r.current_state


def test_analyzer_rangeindex_date_col_normalized():
    """回归: RangeIndex + 「日期」列 → dt 必须是真实日期。"""
    rng = np.random.default_rng(7)
    n = 120
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    dates = pd.date_range("2025-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "日期": dates, "open": close + rng.normal(0, 0.3, n),
        "high": close + rng.uniform(0, 1, n), "low": close - rng.uniform(0, 1, n),
        "close": close, "volume": 1e6,
    })                                   # index 默认 RangeIndex
    assert not isinstance(df.index, pd.DatetimeIndex)
    r = ChanlunAnalyzer().analyze(df, "Au99.99", "黄金")
    assert r.bis and r.zhongshus
    for b in r.bis:
        assert hasattr(b.start_dt, "strftime") and hasattr(b.end_dt, "strftime")
    for p in r.points:
        assert hasattr(p.dt, "strftime")
    assert r.current_state["last_close"] == float(close[-1])


def test_to_signal_long_only():
    bis = [_bi("down", 40, 30, 100.0), _bi("up", 36, 32, 30.0), _bi("down", 35, 31, 80.0),
           _bi("up", 34, 33, 20.0), _bi("down", 30, 24, 40.0), _bi("up", 30, 26, 20.0),
           _bi("down", 27, 25, 30.0)]
    zss = detect_zhongshus(bis)
    points = detect_points(bis, zss, {4: {"type": "bottom", "bi_index": 4}})
    signals = ChanlunAnalyzer.to_signal(points)
    assert any("一买" in s["kind"] for s in signals["entry"])
    assert any("二买" in s["kind"] for s in signals["entry"])
    assert all(s["kind"] not in ("一卖", "二卖", "三卖") for s in signals["entry"])
