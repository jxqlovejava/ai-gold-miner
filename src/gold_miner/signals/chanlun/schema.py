"""缠论结构 DTO — 分型/笔/中枢/买卖点/分析结果。全 frozen 不可变。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Fractal:
    """顶/底分型。mark: "G"(顶) / "D"(底)。fx 顶取 high、底取 low。"""

    mark: str
    dt: Any                       # 中间去包含K线时间
    high: float
    low: float
    fx: float
    index: int                    # 去包含K线下标


@dataclass(frozen=True)
class Bi:
    """笔 — 连接相邻顶底分型的最小走势单元。"""

    direction: str                # "up" / "down"
    start_fx: Fractal
    end_fx: Fractal
    high: float
    low: float
    length: int                   # 两端分型间去包含K线数
    macd_area: float              # 段内 |MACD柱| 面积（背驰用）
    start_dt: Any
    end_dt: Any


@dataclass(frozen=True)
class ZhongShu:
    """中枢 — ≥3 笔重叠价格区域。"""

    zg: float                     # 上沿 = min(构成笔 high)
    zd: float                     # 下沿 = max(构成笔 low)
    zz: float                     # 中轴 = (zg+zd)/2
    gg: float                     # 区域最高
    dd: float                     # 区域最低
    start_dt: Any
    end_dt: Any
    state: str                    # "形成"/"延伸"/"上移"/"下移"
    bi_indexes: tuple = ()        # 构成中枢的笔 index（三买/三卖扫笔用）


@dataclass(frozen=True)
class ChanlunPoint:
    """买卖点信号。"""

    kind: str                     # "一买"/"二买"/"三买"/"一卖"/"二卖"/"三卖"
    dt: Any
    price: float
    confidence: float             # 0.0-1.0
    rationale: str


@dataclass(frozen=True)
class ChanlunResult:
    """缠论全量分析结果。"""

    symbol: str
    name: str
    freq: str                     # "D" / "W"
    backend: str                  # "self" / "czsc"
    fractals: list[Fractal]
    bis: list[Bi]
    zhongshus: list[ZhongShu]
    points: list[ChanlunPoint]
    current_state: dict           # 现价位置/中枢状态/最近买卖点
    signals: dict                 # {"entry": [...], "exit": [...]}
    source_citations: list[dict]
    confidence: float

    def to_summary_dict(self) -> dict:
        """序列化为 dict 供报告/信号生成消费。"""
        return {
            "backend": self.backend,
            "freq": self.freq,
            "bi_count": len(self.bis),
            "zhongshu_count": len(self.zhongshus),
            "last_zs": (
                {"zg": self.zhongshus[-1].zg, "zd": self.zhongshus[-1].zd,
                 "zz": self.zhongshus[-1].zz, "state": self.zhongshus[-1].state,
                 "gg": self.zhongshus[-1].gg, "dd": self.zhongshus[-1].dd}
                if self.zhongshus else None
            ),
            "points": [
                {"kind": p.kind, "dt": str(p.dt), "price": p.price,
                 "confidence": p.confidence, "rationale": p.rationale}
                for p in self.points
            ],
            "current_state": self.current_state,
            "signals": self.signals,
            "confidence": self.confidence,
        }
