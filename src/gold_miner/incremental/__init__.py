"""增量判断引擎 — 新事件→对金价方向的新判断.

完整金价分析 (scan) 只在用户主动触发时运行; 本引擎维护持久化"基准判断"
(decision_state.json), 突发新闻/新事件出现时运行轻量增量判断, 更新基准
并推送微信卡片, 无需等待下一次全量分析.
"""

from .judge import run_incremental

__all__ = ["run_incremental"]
