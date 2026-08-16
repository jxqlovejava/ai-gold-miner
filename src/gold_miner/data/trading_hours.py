"""国内黄金 (民生积存金) 交易日 + 交易时段判断 — 单一真相源.

供监控脚本 / 哨兵引擎共用, 避免各脚本各维护一份节假日/时段逻辑导致漂移.

- is_cn_trading_day:            是否为国内黄金交易日 (工作日 + 非法定节假日 + 调休补班)
- is_accumulation_trading_time: 是否为民生积存金交易时段
  (周一至周五 9:05 开盘 — 次日 02:00 收盘, 含夜盘 21:00-02:00)
- next_cn_trading_day:          下一个国内交易日

2026-08-16 新增: 从 sentinel/engine.py 迁移交易日逻辑, 并新增交易时段判断.
背景: adaptive_gold_monitor 等监控脚本休市期间无门禁, 冻结的积存金价格反复触发
同条件告警 (cost_proximity/rebound 等), 每 5 分钟推送一次微信 — 纯噪音.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

# 国内主要节假日 (非交易日) — 覆盖 2026 下半年 (日期为北京时间)
# 周六/周日永远非交易日; 法定节假日补班/放假在此维护
_CN_HOLIDAYS: set[str] = {
    # 中秋节 (2026-09-25, 周五) + 周末
    "2026-09-25",
    # 国庆节 (2026-10-01 ~ 10-07)
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
}

# 调休补班日 (周六/周日但为工作日, 市场正常交易) — 按官方调休安排维护
_CN_MAKEUP_WORKDAYS: set[str] = {
    # 2026 中秋/国庆调休补班示例 (以国务院正式通知为准):
    # "2026-09-27",  # 若国庆前周日补班
}

# 民生积存金交易时段 (hermes_crontab.txt 权威注释):
#   周一至周五 9:05 开盘, 次日 02:00 收盘 (含夜盘 21:00-02:00)
_ACCUM_OPEN_MIN = 9 * 60 + 5       # 9:05
_ACCUM_CLOSE_MIN = 2 * 60          # 次日 02:00


def is_cn_trading_day(day: datetime) -> bool:
    """判断北京时间 day 是否为国内黄金交易日.

    积存金/上金所交易日 = 工作日且非法定节假日, 或调休补班日(周六日实为交易日)。
    """
    bj = day.astimezone(BEIJING)
    date_str = bj.strftime("%Y-%m-%d")
    # 法定节假日 → 非交易日 (即使是工作日)
    if date_str in _CN_HOLIDAYS:
        return False
    # 调休补班日 → 交易日 (即使是周六/周日)
    if date_str in _CN_MAKEUP_WORKDAYS:
        return True
    if bj.weekday() >= 5:  # 周六(5)/周日(6) 且非补班 → 非交易日
        return False
    return True


def is_accumulation_trading_time(now: datetime) -> bool:
    """判断北京时间 now 是否处于民生积存金交易时段.

    交易时段 = 交易日 9:05 开盘 → 次日 02:00 收盘.
    规则:
      - 00:00-02:00 凌晨段: 属于前一交易日的夜盘延续 → 判断前一日是否交易日
        (周五夜盘延续到周六凌晨 02:00; 周日夜盘不存在, 因周一是新交易日).
      - 02:00-9:05 之间: 休市 (冻结价, 无新信号).
      - 9:05 之后: 判断当日是否交易日.

    休市期间积存金价格冻结, 监控应静默; 宏观事件提醒 (FOMC/CPI 等) 不受影响
    仍可发生, 由调用方在门禁外单独处理.
    """
    bj = now.astimezone(BEIJING)
    hm = bj.hour * 60 + bj.minute

    if hm < _ACCUM_CLOSE_MIN:  # 00:00-02:00 → 前一交易日夜盘
        prev_day = bj - timedelta(days=1)
        return is_cn_trading_day(prev_day)

    if hm >= _ACCUM_OPEN_MIN:  # 9:05 后 → 当日交易日
        return is_cn_trading_day(bj)

    return False  # 02:00-9:05 休市


def next_cn_trading_day(day: datetime) -> datetime:
    """返回 day 之后的第一个国内交易日 (北京时间)."""
    bj = day.astimezone(BEIJING)
    candidate = bj + timedelta(days=1)
    while not is_cn_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate
