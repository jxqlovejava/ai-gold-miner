"""主动式战略投资顾问 — 从被动报告到主动决策指挥.

核心模块:
  - core          : 协议、数据模型、基类
  - early_warning : 主动预警引擎（事件日历监控）
  - action_guide  : 决策指令系统（信号 → 行动指令）
  - sentiment_guard : 情绪与节奏对齐（COT、ETF、散户反向指标）
  - extreme_guard : 极端情景预判与防御
  - consultant    : 对话式咨询接口
  - orchestrator  : Advisor 编排器（统一入口）

使用方式:
    from gold_miner.advisor import Advisor

    advisor = Advisor()
    # 今日行动指令
    guide = advisor.daily_guide()
    # 用户咨询
    answer = advisor.consult("美联储下周加息，我该怎么做？")
    # 检查预警
    alerts = advisor.check_alerts()
"""

from gold_miner.advisor.action_guide import ActionGuide
from gold_miner.advisor.consultant import Consultant
from gold_miner.advisor.core import (
    AdvisorReport,
    AlertLevel,
    UserProfile,
)
from gold_miner.advisor.early_warning import EarlyWarningEngine
from gold_miner.advisor.extreme_guard import ExtremeGuard
from gold_miner.advisor.orchestrator import Advisor
from gold_miner.advisor.sentiment_guard import SentimentGuard

__all__ = [
    "ActionGuide",
    "Advisor",
    "AdvisorReport",
    "AlertLevel",
    "Consultant",
    "EarlyWarningEngine",
    "ExtremeGuard",
    "SentimentGuard",
    "UserProfile",
]
