"""个人数据存储抽象层 — Protocol 定义."""

from __future__ import annotations

from typing import Any, Protocol


class PersonalDataStore(Protocol):
    """个人敏感数据的存储接口.

    实现可以是本地文件系统、加密存储、远程数据库等。
    所有方法使用标准 Python 类型，便于替换实现。
    """

    # ------------------------------------------------------------------
    # 持仓配置
    # ------------------------------------------------------------------

    def load_portfolio(self) -> dict[str, Any]:
        """加载持仓配置，返回解析后的 dict.

        对应原 data/portfolio.yaml。
        文件不存在时返回空 dict。
        """
        ...

    def save_portfolio(self, data: dict[str, Any]) -> None:
        """保存持仓配置.

        对应原 data/portfolio.yaml。
        """
        ...

    # ------------------------------------------------------------------
    # 交易日志
    # ------------------------------------------------------------------

    def append_trade(self, record: dict[str, Any]) -> None:
        """追加交易记录.

        对应原 data/trade_log.md 的追加写入模式。
        record 至少包含: timestamp, action, grams, price, reason。
        """
        ...

    def load_trade_log(self) -> str:
        """加载完整交易日志文本.

        返回 markdown 格式的交易日志内容。
        文件不存在时返回空字符串。
        """
        ...

    # ------------------------------------------------------------------
    # 预测日志
    # ------------------------------------------------------------------

    def load_predictions(self) -> list[dict[str, Any]]:
        """加载所有预测记录.

        对应原 data/prediction_journal.jsonl。
        返回按时间排序的 dict 列表。
        文件不存在时返回空列表。
        """
        ...

    def save_predictions(self, records: list[dict[str, Any]]) -> None:
        """覆盖保存所有预测记录.

        用于结算后重写完整文件。
        """
        ...

    def append_prediction(self, record: dict[str, Any]) -> None:
        """追加单条预测记录.

        只追加不读取，性能优于 save_predictions。
        """
        ...

    # ------------------------------------------------------------------
    # 事件存储
    # ------------------------------------------------------------------

    def load_events(self) -> list[dict[str, Any]]:
        """加载所有事件记录.

        对应原 data/event_store.jsonl。
        返回按时间排序的 dict 列表。
        文件不存在时返回空列表。
        """
        ...

    def save_events(self, records: list[dict[str, Any]]) -> None:
        """覆盖保存所有事件记录.

        用于需要重写完整文件的场景。
        """
        ...

    def append_event(self, record: dict[str, Any]) -> None:
        """追加单条事件记录.

        事件存储的核心操作（只追加）。
        """
        ...

    # ------------------------------------------------------------------
    # 个人规则
    # ------------------------------------------------------------------

    def load_personal_rules(self) -> str:
        """加载个人规则 markdown 文本.

        对应原 data/personal_rules.md。
        文件不存在时返回空字符串。
        """
        ...

    def save_personal_rules(self, content: str) -> None:
        """保存个人规则 markdown 文本."""
        ...

    # ------------------------------------------------------------------
    # 投资者画像
    # ------------------------------------------------------------------

    def load_investor_profile(self) -> str:
        """加载投资者画像 markdown 文本.

        对应原 investor_profile.md。
        文件不存在时返回空字符串。
        """
        ...

    def save_investor_profile(self, content: str) -> None:
        """保存投资者画像 markdown 文本."""
        ...

    # ------------------------------------------------------------------
    # 军规状态
    # ------------------------------------------------------------------

    def load_doctrine_state(self) -> dict[str, Any]:
        """加载军规启用状态.

        对应原 data/doctrine_state.json。
        文件不存在时返回空 dict。
        """
        ...

    def save_doctrine_state(self, state: dict[str, Any]) -> None:
        """保存军规启用状态."""
        ...

    # ------------------------------------------------------------------
    # 情景分析
    # ------------------------------------------------------------------

    def load_scenarios(self) -> list[dict[str, Any]]:
        """加载所有情景分析记录.

        对应原 data/scenarios.jsonl。
        返回 dict 列表。
        文件不存在时返回空列表。
        """
        ...

    def append_scenario(self, record: dict[str, Any]) -> None:
        """追加单条情景分析记录."""
        ...

    # ------------------------------------------------------------------
    # 历史数据
    # ------------------------------------------------------------------

    def load_gold_history(self) -> str:
        """加载黄金历史价格 CSV 文本.

        对应原 data/jd_ms_gold_history.csv。
        返回 CSV 格式字符串。
        文件不存在时返回空字符串。
        """
        ...

    def append_gold_history(self, csv_line: str) -> None:
        """追加单条 CSV 记录（不含 header）."""
        ...
