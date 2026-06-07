"""代理管理器 — 自动发现/启动 mihomo 或 clash，为 HTTP 请求提供代理."""

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from gold_miner.config import settings


class ProxyManager:
    """代理管理器.

    自动发现系统中的 mihomo / clash / clash-meta 二进制，
    启动独立代理进程，供项目中的 HTTP 请求使用。

    不修改系统代理设置，不干扰 ClashX 等现有工具。
    """

    # 候选二进制文件名（按优先级）
    BINARY_NAMES = ["mihomo", "clash-meta", "clash"]

    # 代理端口（使用非标准端口避免冲突）
    DEFAULT_PORT = 17890
    API_PORT = 19090

    # ClashX / Clash Verge 等常见外部代理端口
    EXTERNAL_PORTS = [7890, 7891, 7897, 9090]

    def __init__(self) -> None:
        self.binary: str | None = None
        self.config_path: Path | None = None
        self.process: subprocess.Popen | None = None
        self.port = self.DEFAULT_PORT
        self._find_binary()

    def _find_binary(self) -> None:
        """查找可用的代理二进制."""
        # 1. 项目目录下的 proxy/ 子目录
        project_proxy_dir = Path(__file__).parent
        for name in self.BINARY_NAMES:
            candidate = project_proxy_dir / name
            if candidate.exists() and os.access(candidate, os.X_OK):
                self.binary = str(candidate)
                logger.info(f"发现代理二进制: {self.binary}")
                return

        # 2. PATH 环境变量
        for name in self.BINARY_NAMES:
            path = shutil.which(name)
            if path:
                self.binary = path
                logger.info(f"发现代理二进制 (PATH): {self.binary}")
                return

        logger.debug(
            "未找到 mihomo / clash 二进制。"
            "如需代理外网请求，运行 gold-miner proxy-install 自动下载。"
        )

    def download_binary(self) -> bool:
        """自动下载 mihomo 二进制到项目目录.

        从 GitHub Releases 下载预编译的 mihomo 二进制。
        自动检测平台: macOS arm64/amd64, Linux amd64/arm64.

        Returns:
            是否下载成功
        """
        import platform
        import sys

        system = platform.system().lower()
        machine = platform.machine().lower()

        # 平台→GitHub asset 后缀映射
        arch_map = {
            ("darwin", "arm64"): "mihomo-darwin-arm64",
            ("darwin", "x86_64"): "mihomo-darwin-amd64",
            ("linux", "x86_64"): "mihomo-linux-amd64",
            ("linux", "aarch64"): "mihomo-linux-arm64",
            ("linux", "arm64"): "mihomo-linux-arm64",
            ("windows", "x86_64"): "mihomo-windows-amd64.exe",
        }

        asset_name = arch_map.get((system, machine))
        if not asset_name:
            logger.error(f"不支持的平台: {system}/{machine}")
            return False

        target_dir = Path(__file__).parent
        target_path = target_dir / "mihomo"

        # 尝试多个下载源
        urls = [
            f"https://github.com/MetaCubeX/mihomo/releases/latest/download/{asset_name}",
            f"https://mirror.ghproxy.com/https://github.com/MetaCubeX/mihomo/releases/latest/download/{asset_name}",
            f"https://ghproxy.net/https://github.com/MetaCubeX/mihomo/releases/latest/download/{asset_name}",
        ]

        for i, url in enumerate(urls):
            try:
                logger.info(f"下载 mihomo 二进制 ({['GitHub','ghproxy镜像1','ghproxy镜像2'][i]}): {asset_name}")
                resp = httpx.get(url, timeout=120, follow_redirects=True)
                resp.raise_for_status()

                target_path.write_bytes(resp.content)
                target_path.chmod(0o755)
                self.binary = str(target_path)
                logger.info(f"mihomo 已安装至: {target_path}")
                return True
            except Exception as e:
                logger.debug(f"下载源失败 ({url}): {e}")
                continue

        logger.error("所有下载源均失败，请手动下载 mihomo 到 src/gold_miner/proxy/")
        return False

    def _write_config(self, subscription_url: str = "") -> Path:
        """生成 mihomo 配置文件.

        如果有订阅链接，下载并合并；否则生成最小配置。
        """
        config_dir = Path(settings.data_path) / "proxy"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"

        # 基础配置
        base_config = f"""mixed-port: {self.port}
allow-lan: true
bind-address: '*'
mode: rule
log-level: warning
external-controller: '127.0.0.1:{self.API_PORT}'
dns:
    enable: true
    ipv6: false
    enhanced-mode: fake-ip
    fake-ip-range: 198.18.0.1/16
    nameserver:
        - https://doh.pub/dns-query
        - https://dns.alidns.com/dns-query
"""

        # 如果有订阅链接，添加 proxy-provider
        if subscription_url:
            provider_config = f"""
proxy-providers:
    default:
        url: "{subscription_url}"
        type: http
        interval: 86400
        path: ./provider.yaml
        health-check:
            enable: true
            url: https://www.gstatic.com/generate_204
            interval: 300
"""
            base_config += provider_config

        config_file.write_text(base_config, encoding="utf-8")
        logger.info(f"代理配置已写入: {config_file}")
        return config_file

    def start(self, subscription_url: str = "") -> bool:
        """启动代理进程.

        Args:
            subscription_url: Clash/mihomo 订阅链接（可选）

        Returns:
            是否成功启动
        """
        if not self.binary:
            logger.warning("无可用的代理二进制，跳过启动")
            return False

        if self.process and self.process.poll() is None:
            logger.info("代理进程已在运行")
            return True

        self.config_path = self._write_config(subscription_url)
        config_dir = self.config_path.parent

        try:
            self.process = subprocess.Popen(
                [self.binary, "-d", str(config_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(config_dir),
            )
            logger.info(f"代理进程已启动 (PID: {self.process.pid}, 端口: {self.port})")
            atexit.register(self.stop)
            return True
        except Exception as e:
            logger.error(f"代理进程启动失败: {e}")
            return False

    def stop(self) -> None:
        """停止代理进程."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            logger.info("代理进程已停止")

    @property
    def is_running(self) -> bool:
        """检查代理进程是否运行中."""
        return self.process is not None and self.process.poll() is None

    @property
    def http_proxy(self) -> str:
        """HTTP 代理地址."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def socks_proxy(self) -> str:
        """SOCKS5 代理地址."""
        return f"socks5://127.0.0.1:{self.port}"

    def get_client(self, **kwargs: Any) -> httpx.Client:
        """获取配置了代理的 httpx Client."""
        if self.is_running:
            kwargs.setdefault("proxy", self.http_proxy)
            logger.debug(f"httpx 使用代理: {self.http_proxy}")
        return httpx.Client(**kwargs)


# 全局单例
_proxy_manager: ProxyManager | None = None


def get_proxy_manager() -> ProxyManager:
    """获取全局代理管理器."""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


def get_proxied_client(**kwargs: Any) -> httpx.Client:
    """获取 httpx Client（如有可用代理则自动使用）."""
    mgr = get_proxy_manager()
    # 不再自动启动代理进程，避免对普通用户造成干扰
    # 如需代理，先运行 gold-miner proxy-install 并配置 MIHOMO_SUB_URL
    return mgr.get_client(**kwargs)
