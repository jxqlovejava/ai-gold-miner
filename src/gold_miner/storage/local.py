"""本地文件系统存储实现 — 读写 data/private/ 目录."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from gold_miner.config import settings


class LocalFileStore:
    """基于本地文件系统的个人数据存储实现.

    所有文件存放在 private_data_dir (默认 data/private/) 下，
    与代码仓库隔离，避免敏感数据进入版本控制。
    """

    FILE_NAMES = {
        "portfolio": "portfolio.yaml",
        "trade_log": "trade_log.md",
        "prediction_journal": "prediction_journal.jsonl",
        "event_store": "event_store.jsonl",
        "personal_rules": "personal_rules.md",
        "investor_profile": "investor_profile.md",
        "doctrine_state": "doctrine_state.json",
        "scenarios": "scenarios.jsonl",
        "gold_history": "jd_ms_gold_history.csv",
        "bank_target_history": "bank_target_history.jsonl",
        "institutional_13f_history": "institutional_13f_history.jsonl",
        "economic_data": "economic_data.jsonl",
    }

    def __init__(self, private_data_dir: str | Path | None = None) -> None:
        self._dir = Path(private_data_dir) if private_data_dir else settings.private_data_path
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _path(self, key: str) -> Path:
        return self._dir / self.FILE_NAMES[key]

    def _read_text(self, key: str) -> str:
        path = self._path(key)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"读取 {path.name} 失败: {e}")
            return ""

    def _write_text(self, key: str, content: str) -> None:
        path = self._path(key)
        path.write_text(content, encoding="utf-8")

    def _read_jsonl(self, key: str) -> list[dict[str, Any]]:
        path = self._path(key)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"读取 {path.name} 失败: {e}")
        return records

    def _append_jsonl(self, key: str, record: dict[str, Any]) -> None:
        path = self._path(key)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_jsonl(self, key: str, records: list[dict[str, Any]]) -> None:
        path = self._path(key)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 持仓配置
    # ------------------------------------------------------------------

    def load_portfolio(self) -> dict[str, Any]:
        text = self._read_text("portfolio")
        if not text:
            return {}
        try:
            return yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            logger.warning(f"解析 portfolio.yaml 失败: {e}")
            return {}

    def save_portfolio(self, data: dict[str, Any]) -> None:
        self._write_text("portfolio", yaml.dump(data, allow_unicode=True, sort_keys=False))

    # ------------------------------------------------------------------
    # 交易日志
    # ------------------------------------------------------------------

    def append_trade(self, record: dict[str, Any]) -> None:
        """追加交易记录到 trade_log.md.

        record 格式: {"timestamp": str, "action": str, "grams": float,
                      "price": float, "reason": str, ...}
        """
        path = self._path("trade_log")
        header = f"\n## {record.get('timestamp', '')} — {record.get('action', '交易')}\n\n"
        lines = [f"- **{k}**: {v}" for k, v in record.items() if k not in ("timestamp", "action")]
        entry = header + "\n".join(lines) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)

    def load_trade_log(self) -> str:
        return self._read_text("trade_log")

    # ------------------------------------------------------------------
    # 预测日志
    # ------------------------------------------------------------------

    def load_predictions(self) -> list[dict[str, Any]]:
        return self._read_jsonl("prediction_journal")

    def save_predictions(self, records: list[dict[str, Any]]) -> None:
        self._write_jsonl("prediction_journal", records)

    def append_prediction(self, record: dict[str, Any]) -> None:
        self._append_jsonl("prediction_journal", record)

    # ------------------------------------------------------------------
    # 事件存储
    # ------------------------------------------------------------------

    def load_events(self) -> list[dict[str, Any]]:
        return self._read_jsonl("event_store")

    def save_events(self, records: list[dict[str, Any]]) -> None:
        self._write_jsonl("event_store", records)

    def append_event(self, record: dict[str, Any]) -> None:
        self._append_jsonl("event_store", record)

    # ------------------------------------------------------------------
    # 个人规则
    # ------------------------------------------------------------------

    def load_personal_rules(self) -> str:
        return self._read_text("personal_rules")

    def save_personal_rules(self, content: str) -> None:
        self._write_text("personal_rules", content)

    # ------------------------------------------------------------------
    # 投资者画像
    # ------------------------------------------------------------------

    def load_investor_profile(self) -> str:
        return self._read_text("investor_profile")

    def save_investor_profile(self, content: str) -> None:
        self._write_text("investor_profile", content)

    # ------------------------------------------------------------------
    # 军规状态
    # ------------------------------------------------------------------

    def load_doctrine_state(self) -> dict[str, Any]:
        text = self._read_text("doctrine_state")
        if not text:
            return {}
        try:
            return json.loads(text) or {}
        except json.JSONDecodeError as e:
            logger.warning(f"解析 doctrine_state.json 失败: {e}")
            return {}

    def save_doctrine_state(self, state: dict[str, Any]) -> None:
        self._write_text("doctrine_state", json.dumps(state, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------
    # 情景分析
    # ------------------------------------------------------------------

    def load_scenarios(self) -> list[dict[str, Any]]:
        return self._read_jsonl("scenarios")

    def append_scenario(self, record: dict[str, Any]) -> None:
        self._append_jsonl("scenarios", record)

    # ------------------------------------------------------------------
    # 历史数据
    # ------------------------------------------------------------------

    def load_gold_history(self) -> str:
        return self._read_text("gold_history")

    def append_gold_history(self, csv_line: str) -> None:
        path = self._path("gold_history")
        with open(path, "a", encoding="utf-8") as f:
            f.write(csv_line + "\n")

    # ------------------------------------------------------------------
    # 机构历史数据
    # ------------------------------------------------------------------

    def load_bank_target_history(self) -> list[dict[str, Any]]:
        return self._read_jsonl("bank_target_history")

    def append_bank_target(self, record: dict[str, Any]) -> None:
        """追加投行目标价记录，按 (bank, date) 去重."""
        record.setdefault("timestamp", datetime.now().isoformat())
        bank = record.get("bank", "")
        date_key = record["timestamp"][:10]
        dedup_key = (bank, date_key)

        existing = self.load_bank_target_history()
        for r in existing:
            if (r.get("bank"), r.get("timestamp", "")[:10]) == dedup_key:
                return

        self._append_jsonl("bank_target_history", record)

    def load_institutional_13f_history(self) -> list[dict[str, Any]]:
        return self._read_jsonl("institutional_13f_history")

    def append_institutional_13f(self, record: dict[str, Any]) -> None:
        """追加 13F 持仓记录，按 (institution, ticker, quarter) 去重."""
        record.setdefault("timestamp", datetime.now().isoformat())
        institution = record.get("institution", "")
        ticker = record.get("ticker", "")
        quarter = record.get("quarter", "")
        dedup_key = (institution, ticker, quarter)

        existing = self.load_institutional_13f_history()
        for r in existing:
            if (
                r.get("institution"),
                r.get("ticker"),
                r.get("quarter"),
            ) == dedup_key:
                return

        self._append_jsonl("institutional_13f_history", record)

    # ------------------------------------------------------------------
    # 经济数据
    # ------------------------------------------------------------------

    def load_economic_data(self) -> list[dict[str, Any]]:
        return self._read_jsonl("economic_data")

    def append_economic_data(self, record: dict[str, Any]) -> None:
        """追加经济数据发布记录.

        去重逻辑由调用方（EconomicDataRecorder）负责，此处仅做追加写入。
        """
        record.setdefault("fetched_at", datetime.now().isoformat())
        self._append_jsonl("economic_data", record)

    def save_economic_data(self, records: list[dict[str, Any]]) -> None:
        """覆盖写入全部经济数据记录（用于去重/修正后重写）."""
        self._write_jsonl("economic_data", records)
