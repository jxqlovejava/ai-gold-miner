"""全局配置管理."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# env_file 用 __file__ 推导绝对路径, 不依赖进程 CWD.
# 背景: Hermes cron 未设 workdir 的 job 从守护进程目录(如 ~/.hermes)启动,
#   相对 ".env" 找不到 → llm_api_key 空 → 语义分析器静默禁用 → 突发新闻
#   推送退化为纯规则判定 ("⚠️规则判定·LLM不可用"). 事故: 2026-08-11 突发新闻预警.
# 仍保留相对 ".env" 作兜底, 兼容确实依赖 CWD 的部署.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """应用配置，优先从环境变量读取，其次.env文件."""

    model_config = SettingsConfigDict(
        env_file=(_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime mode
    demo_mode: bool = False

    # API Keys
    fred_api_key: str = ""
    news_api_key: str = ""
    tavily_api_key: str = ""

    # Yahoo Finance Symbols
    yahoo_symbol_spot: str = "XAUUSD=X"
    yahoo_symbol_gld: str = "GLD"
    yahoo_symbol_iau: str = "IAU"
    yahoo_symbol_dxy: str = "DX-Y.NYB"

    # Trading Parameters
    initial_capital_usd: float = 100_000.0
    max_position_pct: float = 0.8
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06

    # Multi-Objective Strategy
    strategy_default: str = "balanced"
    strategy_cost_recovery_trigger: float = -0.05
    strategy_take_profit_trigger: float = 0.08
    strategy_kelly_fraction: float = 0.25

    # Risk Profile
    risk_profile: str = "moderate"

    # Notification
    wechat_webhook_url: str = ""
    enable_notification: bool = False

    # Self-improvement loop
    enable_auto_tracking: bool = True

    # LLM / DeepSeek (用于文章分析增强)
    llm_api_key: str = ""
    llm_api_base: str = "https://api.deepseek.com/anthropic"
    llm_model: str = "deepseek-v4-pro"  # 或 deepseek-v4-flash

    # 突发新闻语义推理层 (AI 判定传导链; 无 key 或关闭时自动回退关键词规则)
    news_llm_enabled: bool = True
    news_llm_categories: list[str] = ["geopolitical", "energy", "trade", "policy", "election"]
    news_llm_max_headlines: int = 12  # 每轮最多送 AI 的候选条数 (控成本)

    # Price Alerts
    alert_big_move_pct: float = 2.0         # 大波动阈值 (%)
    alert_dxy_move_pct: float = 1.0         # DXY异动阈值 (%)
    alert_key_level_lookback: int = 20      # 关键位回溯天数
    alert_gold_silver_ratio_high: float = 85.0   # 金银比高位预警
    alert_gold_silver_ratio_low: float = 60.0    # 金银比低位预警

    # Anomaly Detection
    anomaly_divergence_threshold: float = 0.4
    anomaly_volume_zscore: float = 2.5
    anomaly_volume_surge_multiplier: float = 2.0
    trust_decay_days: int = 30
    trust_min_score: float = 0.2

    # Signal Consensus Override — 多维度信号共识覆盖
    consensus_min_active_dimensions: int = 4       # 最少活跃维度数
    consensus_ratio_threshold: float = 0.75        # 同向比例阈值（≥75%）
    consensus_light_position_threshold: float = 0.2  # 轻仓阈值（<20%触发覆盖）

    # Proxy
    mihomo_sub_url: str = ""  # mihomo/clash 订阅链接

    # Polymarket
    polymarket_enabled: bool = True
    polymarket_min_volume: float = 500.0  # 最低24h交易量过滤
    polymarket_max_markets: int = 20      # 最多采集市场数

    # Agent Scheduler
    agent_enabled: bool = False
    agent_timezone: str = "Asia/Shanghai"
    agent_schedule_pre_market: str = "08:00"      # 盘前简报
    agent_schedule_post_open: str = "09:30"       # 开盘分析
    agent_schedule_closing: str = "14:30"         # 尾盘提醒
    agent_schedule_event_scan: str = "20:30"      # 事件扫描
    agent_schedule_weekly: str = "sun-21:00"      # 周度展望
    agent_api_host: str = "0.0.0.0"
    agent_api_port: int = 8080

    # Paths
    data_dir: Path = Path("./data")
    private_data_dir: Path = Path("./data/private")
    store_type: str = "local"
    log_level: str = "INFO"

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def private_data_path(self) -> Path:
        path = Path(self.private_data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
