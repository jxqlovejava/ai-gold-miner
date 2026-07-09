"""环境诊断工具 — 检查项目运行所需的所有依赖和配置."""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

from gold_miner.config import settings
from gold_miner.storage import get_store


@dataclass
class CheckResult:
    """单次检查结果."""

    name: str
    passed: bool
    message: str
    critical: bool = True


@dataclass
class DoctorReport:
    """诊断报告."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed or not r.critical for r in self.results)

    @property
    def critical_passed(self) -> bool:
        return all(r.passed for r in self.results if r.critical)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)


class Doctor:
    """环境诊断器 — 检查 Python 版本、配置、API key、网络连通性."""

    MIN_PYTHON_VERSION = (3, 9)
    NETWORK_TIMEOUT = 5  # seconds

    def __init__(self) -> None:
        self.report = DoctorReport()

    def run_all(self) -> DoctorReport:
        """运行全部诊断检查."""
        self._check_python_version()
        self._check_env_file()
        self._check_private_data_dir()
        self._check_api_keys()
        self._check_network_connectivity()
        self._check_storage()
        return self.report

    def _check_python_version(self) -> None:
        version = sys.version_info[:2]
        passed = version >= self.MIN_PYTHON_VERSION
        self.report.add(
            CheckResult(
                name="Python 版本",
                passed=passed,
                message=f"Python {version[0]}.{version[1]} (需要 >= {self.MIN_PYTHON_VERSION[0]}.{self.MIN_PYTHON_VERSION[1]})",
                critical=True,
            )
        )

    def _check_env_file(self) -> None:
        env_path = Path(".env")
        passed = env_path.exists()
        self.report.add(
            CheckResult(
                name=".env 配置文件",
                passed=passed,
                message=".env 存在" if passed else ".env 不存在，运行 gold-miner setup 创建",
                critical=False,
            )
        )

    def _check_private_data_dir(self) -> None:
        path = settings.private_data_path
        try:
            path.mkdir(parents=True, exist_ok=True)
            test_file = path / ".doctor_write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            passed = True
            message = f"{path} 存在且可写"
        except OSError as e:
            passed = False
            message = f"{path} 不可写: {e}"
        self.report.add(
            CheckResult(
                name="私有数据目录",
                passed=passed,
                message=message,
                critical=True,
            )
        )

    def _check_api_keys(self) -> None:
        keys = {
            "news_api_key": settings.news_api_key,
            "tavily_api_key": settings.tavily_api_key,
            "fred_api_key": settings.fred_api_key,
            "llm_api_key": settings.llm_api_key,
        }
        has_any = any(v and v.strip() and not v.startswith("your_") for v in keys.values())
        self.report.add(
            CheckResult(
                name="API Keys",
                passed=has_any,
                message="至少一个 API key 已配置" if has_any else "未配置任何 API key（可选，但功能受限）",
                critical=False,
            )
        )

    def _check_network_connectivity(self) -> None:
        # Yahoo Finance (primary data source)
        yahoo_ok = self._can_connect("finance.yahoo.com", 443)
        self.report.add(
            CheckResult(
                name="Yahoo Finance 连通性",
                passed=yahoo_ok,
                message="可访问" if yahoo_ok else "无法连接（检查网络/代理）",
                critical=True,
            )
        )

        # NewsAPI (optional)
        if settings.news_api_key and not settings.news_api_key.startswith("your_"):
            news_ok = self._can_connect("newsapi.org", 443)
            self.report.add(
                CheckResult(
                    name="NewsAPI 连通性",
                    passed=news_ok,
                    message="可访问" if news_ok else "无法连接",
                    critical=False,
                )
            )

        # Tavily (optional)
        if settings.tavily_api_key and not settings.tavily_api_key.startswith("your_"):
            tavily_ok = self._can_connect("api.tavily.com", 443)
            self.report.add(
                CheckResult(
                    name="Tavily 连通性",
                    passed=tavily_ok,
                    message="可访问" if tavily_ok else "无法连接",
                    critical=False,
                )
            )

    def _can_connect(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=self.NETWORK_TIMEOUT):
                return True
        except OSError:
            return False

    def _check_storage(self) -> None:
        try:
            store = get_store()
            # Try reading portfolio — if no real data, should return empty dict
            portfolio = store.load_portfolio()
            passed = True
            message = f"存储接口正常 (portfolio={'有数据' if portfolio else '空，使用示例数据'} )"
        except Exception as e:
            passed = False
            message = f"存储接口异常: {e}"
        self.report.add(
            CheckResult(
                name="存储接口",
                passed=passed,
                message=message,
                critical=True,
            )
        )


def run_doctor() -> int:
    """CLI 入口: 运行诊断并打印报告.

    Returns:
        0 if all critical checks pass, 1 otherwise.
    """
    print("=" * 50)
    print("  Gold Miner 环境诊断")
    print("=" * 50)
    print()

    doctor = Doctor()
    report = doctor.run_all()

    for r in report.results:
        icon = "✅" if r.passed else "⚠️" if not r.critical else "❌"
        level = "[关键]" if r.critical else "[可选]"
        print(f"  {icon} {r.name} {level}")
        print(f"     {r.message}")
        print()

    if report.critical_passed:
        print("=" * 50)
        print("  ✅ 所有关键检查通过，系统可正常运行")
        print("=" * 50)
        return 0
    else:
        print("=" * 50)
        print(f"  ❌ {sum(1 for r in report.results if r.critical and not r.passed)} 项关键检查失败")
        print("  请修复上述问题后重新运行 gold-miner doctor")
        print("=" * 50)
        return 1


if __name__ == "__main__":
    sys.exit(run_doctor())
