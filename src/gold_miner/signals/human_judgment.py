"""人工判断接口 — 异常信号的人工审核."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.config import settings


@dataclass
class HumanJudgment:
    report_id: str
    verdict: str  # confirmed | dismissed | uncertain
    notes: str = ""
    judged_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class HumanJudgmentStore:
    """人工判断持久化存储.

    数据保存在 data/human_judgments.json。
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.file = self.data_dir / "human_judgments.json"
        self._judgments: dict[str, HumanJudgment] = {}
        self._load()

    def _load(self) -> None:
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text())
            for rid, j in data.items():
                self._judgments[rid] = HumanJudgment(
                    report_id=rid,
                    verdict=j.get("verdict", "uncertain"),
                    notes=j.get("notes", ""),
                    judged_at=(
                        datetime.fromisoformat(j["judged_at"])
                        if j.get("judged_at") else datetime.now()
                    ),
                    metadata=j.get("metadata", {}),
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"HumanJudgmentStore加载失败: {e}")

    def _save(self) -> None:
        data = {}
        for rid, j in self._judgments.items():
            data[rid] = {
                "verdict": j.verdict,
                "notes": j.notes,
                "judged_at": j.judged_at.isoformat(),
                "metadata": j.metadata,
            }
        self.file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def submit_judgment(
        self, report_id: str, verdict: str, notes: str = "",
    ) -> HumanJudgment:
        """提交人工判断."""
        judgment = HumanJudgment(
            report_id=report_id,
            verdict=verdict,
            notes=notes,
        )
        self._judgments[report_id] = judgment
        self._save()
        logger.info(f"人工判断已记录: {report_id} → {verdict}")
        return judgment

    def get_judgment(self, report_id: str) -> HumanJudgment | None:
        return self._judgments.get(report_id)

    def pending_reviews(
        self, anomaly_reports: list[Any],
    ) -> list[Any]:
        """筛选待人工审核的异常报告."""
        pending: list[Any] = []
        for report in anomaly_reports:
            if getattr(report, "requires_human_review", False):
                rid = getattr(report, "detected_at", datetime.now()).isoformat()
                if rid not in self._judgments:
                    pending.append(report)
        return pending

    def judged_history(self) -> list[HumanJudgment]:
        return sorted(
            self._judgments.values(),
            key=lambda j: j.judged_at,
            reverse=True,
        )
