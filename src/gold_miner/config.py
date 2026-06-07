"""全局配置管理."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，优先从环境变量读取，其次.env文件."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    # Proxy
    mihomo_sub_url: str = ""  # mihomo/clash 订阅链接

    # Polymarket
    polymarket_enabled: bool = True
    polymarket_min_volume: float = 500.0  # 最低24h交易量过滤
    polymarket_max_markets: int = 20      # 最多采集市场数

    # Paths
    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
