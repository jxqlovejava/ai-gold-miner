"""Proxy-install command handler."""

from __future__ import annotations

from loguru import logger

from gold_miner.proxy import get_proxy_manager


def run_proxy_install() -> None:
    """Install proxy binary."""
    mgr = get_proxy_manager()
    if mgr.binary:
        logger.info(f"mihomo 已安装: {mgr.binary}")
    else:
        logger.info("开始下载 mihomo 二进制...")
        if mgr.download_binary():
            logger.info("安装成功，可正常使用代理功能")
        else:
            logger.error("下载失败，请手动安装: https://github.com/MetaCubeX/mihomo/releases")
