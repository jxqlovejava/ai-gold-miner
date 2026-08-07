"""代理管理器 — 自动发现/启动 mihomo 或 clash，为 HTTP 请求提供代理."""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from gold_miner.config import settings


class _SharedClientWrapper:
    """httpx.Client 连接池复用包装器.

    拦截 close()/__exit__(), 使多个调用方可安全使用 `with` 语法
    而不关闭底层共享连接池。

    不调用底层 client.__enter__() — httpx.Client 在 UNOPENED 状态下
    请求完全正常，且多线程可安全并发使用同一连接池。
    真正关闭仅在整个 pipeline 结束时触发.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *args: Any) -> None:
        pass  # 不关闭共享连接池

    def close(self) -> None:
        pass  # 不关闭共享连接池


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
        self._shared_client: httpx.Client | None = None
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

        如果有订阅链接，下载订阅配置并覆盖端口/controller；
        否则生成最小直连配置。
        """
        config_dir = Path(settings.data_path) / "proxy"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"

        if subscription_url:
            try:
                headers = {"User-Agent": "clash.meta/mihomo"}
                resp = httpx.get(
                    subscription_url, headers=headers, timeout=60, follow_redirects=True
                )
                resp.raise_for_status()
                raw = resp.text

                # 覆盖端口，避免与系统中其他 clash/mihomo 实例冲突
                raw = self._override_config_value(raw, "mixed-port", self.port)
                raw = self._override_config_value(
                    raw, "external-controller", f"'127.0.0.1:{self.API_PORT}'"
                )
                # 注入干净 DNS, 避免境内 DNS 对部分域名污染导致证书失败
                raw = self._ensure_dns_block(raw)

                config_file.write_text(raw, encoding="utf-8")
                logger.info(f"订阅配置已写入: {config_file}")
                return config_file
            except Exception as e:
                logger.warning(f"下载订阅配置失败，回退到最小配置: {e}")

        # 最小直连配置（无订阅时使用）
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
    nameserver-policy:
        "polymarket.com":
            - https://cloudflare-dns.com/dns-query
            - https://dns.google/resolve

proxies:
    - name: DIRECT
      type: direct

proxy-groups:
    - name: Proxy
      type: select
      proxies:
        - DIRECT

rules:
    - MATCH,Proxy
"""
        config_file.write_text(base_config, encoding="utf-8")
        logger.info(f"代理配置已写入: {config_file}")
        return config_file

    @staticmethod
    def _ensure_dns_block(config_text: str) -> str:
        """若订阅配置无 dns: 段, 注入干净 DNS nameserver-policy.

        目的: 境内 DNS（doh.pub/alidns）对部分境外域名（如 polymarket.com）
        存在污染, 导致 mihomo 的 Direct 节点解析到错误 IP → 证书 hostname 不匹配.
        为这些域名指定 Cloudflare/Google DoH（mihomo 会经代理转发 DoH 请求）.
        """
        if re.search(r"^dns\s*:", config_text, re.MULTILINE):
            return config_text
        dns_block = (
            "dns:\n"
            "    enable: true\n"
            "    ipv6: false\n"
            "    nameserver:\n"
            "        - https://doh.pub/dns-query\n"
            "        - https://dns.alidns.com/dns-query\n"
            "    nameserver-policy:\n"
            '        "polymarket.com":\n'
            "            - https://cloudflare-dns.com/dns-query\n"
            "            - https://dns.google/resolve\n"
        )
        return dns_block + config_text

    @staticmethod
    def _override_config_value(config_text: str, key: str, value: Any) -> str:
        """覆盖 YAML 配置中指定 key 的值（整行替换）."""
        import re

        pattern = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*).+$", re.MULTILINE)
        if pattern.search(config_text):
            return pattern.sub(rf"\g<1>{value}", config_text)
        # 未找到则在文件开头插入
        return f"{key}: {value}\n{config_text}"

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
        # 项目根目录，mihomo 需要以此为 cwd 才能正确加载相对路径配置
        project_root = Path(self.binary).resolve().parents[3]

        # 删除旧的 provider.yaml，避免 -d 模式下 mihomo 将其作为附加配置加载
        legacy_provider = config_dir / "provider.yaml"
        if legacy_provider.exists():
            legacy_provider.unlink()

        try:
            # 隔离 HOME/XDG_CONFIG_HOME，避免 mihomo 加载用户全局 clash 配置
            home_dir = config_dir / ".mihomo-home"
            home_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["HOME"] = str(home_dir)
            env["XDG_CONFIG_HOME"] = str(home_dir / ".config")

            self.process = subprocess.Popen(
                [self.binary, "-d", str(config_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(project_root),
                env=env,
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

    @staticmethod
    def _probe_port(port: int, timeout: float = 0.3) -> bool:
        """探测本机端口是否有代理在监听."""
        import socket

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

    def _detect_available_port(self) -> int | None:
        """探测可用的代理端口: 优先项目端口, 其次外部 ClashX/Clash Verge 等端口."""
        for port in [self.port, *self.EXTERNAL_PORTS]:
            if self._probe_port(port):
                return port
        return None

    @property
    def is_running(self) -> bool:
        """检查代理是否可用（自启动进程 或 本机已在运行的 mihomo/clash）.

        关键: 若外部已有代理进程在监听（如用户手动启动的 mihomo），
        fresh 进程也应识别并复用，而不是直连被墙域名导致 SSL 证书失败.
        """
        if self.process is not None and self.process.poll() is None:
            return True
        return self._detect_available_port() is not None

    def _wait_for_proxy(self, timeout: float = 30.0) -> bool:
        """等待代理端口可用."""
        import socket

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.2)
        return False

    @property
    def http_proxy(self) -> str:
        """HTTP 代理地址."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def socks_proxy(self) -> str:
        """SOCKS5 代理地址."""
        return f"socks5://127.0.0.1:{self.port}"

    def get_client(self, **kwargs: Any) -> httpx.Client:
        """获取配置了代理的 httpx Client（连接池复用, 避免每次新建TLS握手）.

        返回一个共享连接池的 wrapper — callers 可安全使用 `with` 语法.
        自动探测本机已运行的 mihomo/clash（含外部 ClashX 等工具），
        避免 fresh 进程探测不到代理而直连被墙域名.
        """
        from gold_miner.utils.http_fallback import _httpx_proxy_kwargs
        port = self._detect_available_port()
        if port:
            proxy_url = f"http://127.0.0.1:{port}"
            kwargs = _httpx_proxy_kwargs(proxy_url, **kwargs)
            logger.debug(f"httpx 使用代理: {proxy_url}")

        if self._shared_client is None:
            self._shared_client = httpx.Client(**kwargs)
        return _SharedClientWrapper(self._shared_client)


# 全局单例
_proxy_manager: ProxyManager | None = None


def get_proxy_manager() -> ProxyManager:
    """获取全局代理管理器."""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


def get_proxied_client(**kwargs: Any) -> httpx.Client:
    """获取 httpx Client（如有可用代理则自动使用）.

    若用户已配置 MIHOMO_SUB_URL 且代理二进制存在，会自动启动代理进程。
    """
    mgr = get_proxy_manager()
    if not mgr.is_running and mgr.binary and settings.mihomo_sub_url:
        mgr.start(settings.mihomo_sub_url)
        if not mgr._wait_for_proxy():
            logger.warning("代理端口未就绪，请求将尝试直连")
    return mgr.get_client(**kwargs)
