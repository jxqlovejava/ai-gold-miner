"""推送通知模块 — 微信等."""

import httpx

from gold_miner.config import settings


class Notifier:
    def __init__(self) -> None:
        self.webhook_url = settings.wechat_webhook_url
        self.enabled = settings.enable_notification and bool(self.webhook_url)

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            payload = {"msgtype": "text", "text": {"content": message}}
            response = httpx.post(self.webhook_url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def send_markdown(self, content: str) -> bool:
        if not self.enabled:
            return False
        try:
            payload = {"msgtype": "markdown", "markdown": {"content": content}}
            response = httpx.post(self.webhook_url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception:
            return False
