"""多渠道通知 — 企业微信 + 预留鸿蒙推送."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from loguru import logger


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


@dataclass
class Notification:
    title: str
    body: str
    severity: Severity = Severity.INFO
    markdown: str = ""


class NotificationRouter:
    """通知路由器 — 按严重度分发到不同渠道."""

    def __init__(self) -> None:
        self._wechat_enabled = False
        self._harmonyos_enabled = False
        self._setup_wechat()

    def _setup_wechat(self) -> None:
        try:
            from gold_miner.config import settings
            if settings.wechat_webhook_url and settings.enable_notification:
                self._wechat_enabled = True
                logger.info("企业微信通知已启用")
        except Exception:
            pass

    def send(self, title: str, body: str = "", severity: Severity = Severity.INFO,
             markdown: str = "") -> bool:
        """发送通知到所有已启用渠道."""
        success = True

        if severity == Severity.HIGH:
            if not self._send_wechat(title, body, markdown):
                success = False
        elif severity == Severity.MEDIUM:
            if not self._send_wechat(title, body, markdown):
                success = False
        else:
            # INFO级别仅控制台
            logger.info(f"[{title}] {body}")

        return success

    def send_briefing(self, markdown: str) -> bool:
        """发送简报（Markdown格式）."""
        return self._send_wechat_md("黄金Agent简报", markdown)

    def send_alert(self, title: str, body: str) -> bool:
        """发送高优先级告警."""
        logger.warning(f"🚨 {title}: {body}")
        return self._send_wechat(f"🚨 {title}", body)

    # ------------------------------------------------------------------
    # 企业微信
    # ------------------------------------------------------------------

    def _send_wechat(self, title: str, body: str = "", markdown: str = "") -> bool:
        if not self._wechat_enabled:
            return False
        try:
            from gold_miner.execution.notifier import Notifier
            n = Notifier()
            msg = markdown or f"{title}\n{body}" if body else title
            return n.send(msg)
        except Exception as e:
            logger.warning(f"企业微信发送失败: {e}")
            return False

    def _send_wechat_md(self, title: str, content: str) -> bool:
        if not self._wechat_enabled:
            return False
        try:
            from gold_miner.execution.notifier import Notifier
            n = Notifier()
            return n.send_markdown(content)
        except Exception as e:
            logger.warning(f"企业微信Markdown发送失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 鸿蒙推送 (预留)
    # ------------------------------------------------------------------

    def enable_harmonyos(self, config: dict) -> None:
        """配置鸿蒙推送 (后续实现)."""
        logger.info(f"鸿蒙推送预留: {config}")
        self._harmonyos_enabled = True

    def _send_harmonyos(self, title: str, body: str) -> bool:
        """鸿蒙推送 (预留接口，接入华为Push Kit后实现)."""
        if not self._harmonyos_enabled:
            return False
        # TODO: 接入华为Push Kit
        logger.info(f"[HarmonyOS预留] {title}: {body}")
        return True
