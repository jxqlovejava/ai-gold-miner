"""config 的 .env 加载路径回归测试.

事故 (2026-08-11): Hermes cron 未设 workdir 的 job (如 gold_news.py) 从守护进程
目录 (~/.hermes) 启动, config 里相对 ".env" 找不到 → LLM_API_KEY 空 → 语义分析器
静默禁用 → 突发新闻推送退化为纯规则判定 ("⚠️规则判定·LLM不可用"), 且因 enabled=False
不记录回退, 健康告警也不触发.
"""

from __future__ import annotations

from gold_miner.config import _ENV_FILE


def test_env_file_is_absolute():
    """env_file 必须是绝对路径, 不依赖进程 CWD."""
    assert _ENV_FILE.is_absolute()
    assert _ENV_FILE.name == ".env"


def test_env_file_points_to_project_root():
    """绝对路径应指向仓库根 .env (与 config.py 同仓库, 由 __file__ 推导)."""
    # config.py 位于 <root>/src/gold_miner/config.py, parents[2] = 仓库根
    assert (_ENV_FILE.parent / "src" / "gold_miner" / "config.py").is_file()


def test_env_file_has_relative_fallback():
    """Settings.model_config 同时保留相对 ".env" 兜底, 兼容依赖 CWD 的部署."""
    from gold_miner.config import Settings

    env_files = Settings.model_config.get("env_file")
    assert isinstance(env_files, (tuple, list))
    assert ".env" in [str(e) for e in env_files]
