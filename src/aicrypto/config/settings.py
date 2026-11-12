"""配置体系（PRD 第 32 章）。

**唯一真源**是本模块的 :class:`Settings`。所有配置项必须同时出现在：

1. ``config/base.toml``（非敏感）或 ``.env.example``（敏感）；
2. PRD 第 32.4 章对应小节的表格；
3. 本模块的 Settings 模型。

CI 通过 :mod:`aicrypto.config.catalog` 校验三者一致（32.5 / 36.4 config-check）。

加载优先级（32.1，低 → 高）::

    代码默认值 → .env / .env.local → 环境变量 → 数据库中的用户偏好

硬约束：**安全相关项（密钥、token、白名单、dry-run 开关）只能来自环境变量**，
不允许数据库或 API 覆盖（32.1）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]
#: 默认配置文件目录
CONFIG_DIR = PROJECT_ROOT / "config"


class AppEnv(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class MarketType(StrEnum):
    """市场类型。30.2 的 CHECK 约束取值。"""

    SPOT = "SPOT"
    USDM_FUTURES = "USDM_FUTURES"


class Timeframe(StrEnum):
    """K 线周期。30.2 的 CHECK 约束取值。"""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"


class DeliveryMode(StrEnum):
    """通知投递模式（22.4.4.1）。"""

    BROADCAST = "broadcast"
    FIRST_SUCCESS = "first_success"
    SEVERITY_ROUTING = "severity_routing"


class QuotaCountingMode(StrEnum):
    """配额计数模式（22.4.4.2）。"""

    PER_EVENT = "per_event"
    PER_CHANNEL = "per_channel"


class LockMode(StrEnum):
    """调度锁模式（23.1.2）。"""

    POSTGRES_ADVISORY = "postgres_advisory"
    LOCAL = "local"


class LLMProvider(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


class Settings(BaseSettings):
    """应用配置。

    字段名即环境变量名（大小写不敏感）。
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用基础 (32.4) ----------------
    APP_ENV: AppEnv = Field(default=AppEnv.LOCAL, description="local / staging / prod")
    APP_TIMEZONE: str = Field(default="Asia/Shanghai", description="用户时区")
    APP_HOST: str = Field(default="127.0.0.1", description="后端绑定地址，默认仅回环")
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: LogFormat = Field(default=LogFormat.JSON)
    DATABASE_URL: str = Field(default="", description="PostgreSQL 连接串")
    REDIS_URL: str = Field(default="", description="Redis 连接串")
    FRONTEND_URL: str = Field(default="http://localhost:3000")
    DASHBOARD_PUBLIC_BASE_URL: str = Field(
        default="", description="短链基准地址；公网可达时必须设置为真实地址"
    )

    # ---------------- 鉴权 (34) ----------------
    AUTH_ENABLED: bool = Field(default=True)
    AUTH_TOKEN: SecretStr | None = Field(default=None, description="Bearer token")
    AUTH_TOKEN_SHA256: str | None = Field(default=None, description="只存哈希时使用")
    AUTH_RATE_LIMIT_PER_MIN: int = Field(default=10, ge=1)
    ALLOWED_IPS: str = Field(default="", description="逗号分隔；未设置时不启用")
    PUBLIC_CALLBACK_ENABLED: bool = Field(default=False)
    PUBLIC_CALLBACK_PORT: int = Field(default=8081, ge=1, le=65535)
    PUBLIC_CALLBACK_TOKEN: SecretStr | None = Field(default=None)

    # ---------------- 行情采集 (29) ----------------
    MARKET_SYMBOLS: str = Field(default="BTCUSDT,ETHUSDT")
    MARKET_TYPES: str = Field(default="SPOT,USDM_FUTURES")
    MARKET_TIMEFRAMES: str = Field(default="5m,15m,1h,4h")
    MARKET_ENABLED_INTERVALS_1M: bool = Field(default=False)
    MARKET_WS_RECONNECT_MAX_MS: int = Field(default=30000, ge=100)
    MARKET_OI_POLL_SECONDS: int = Field(default=60, ge=10)
    MARKET_CROSSCHECK_SECONDS: int = Field(default=300, ge=30)
    MARKET_BACKFILL_ENABLED: bool = Field(default=True)
    MARKET_BACKFILL_MAX_KLINES: int = Field(default=1000, ge=1, le=1000)
    MARKET_BOOK_SNAPSHOT_INTERVAL_SECONDS: int = Field(default=60, ge=1)
    MARKET_BOOK_DEPTH_LEVELS: int = Field(default=20, ge=1, le=100)
    CLOCK_SYNC_INTERVAL_SECONDS: int = Field(default=600, ge=60)
    CLOCK_SKEW_ALERT_MS: int = Field(default=5000, ge=0)

    # ---------------- 异动与规则 (22) ----------------
    ANOMALY_SCAN_INTERVAL_SECONDS: int = Field(default=60, ge=5)
    ANOMALY_RULE_CONFIG_PATH: str = Field(default="config/base.toml")
    ANOMALY_EVAL_HORIZONS: str = Field(default="1h,4h")
    ANOMALY_RULES_DISABLED: str = Field(default="")

    # ---------------- 通知 (22.4 / 22.5) ----------------
    FEISHU_ENABLED: bool = Field(default=False)
    FEISHU_WEBHOOK_URL: SecretStr | None = Field(default=None)
    FEISHU_SECRET: SecretStr | None = Field(default=None)
    WECOM_APP_ENABLED: bool = Field(default=False)
    WECOM_CORP_ID: str | None = Field(default=None)
    WECOM_CORP_SECRET: SecretStr | None = Field(default=None)
    WECOM_AGENT_ID: str | None = Field(default=None)
    WECOM_TO_USER: str | None = Field(default=None)
    WECOM_TO_PARTY: str | None = Field(default=None)
    WECOM_TO_TAG: str | None = Field(default=None)
    WECOM_APP_MSG_TYPE: Literal["text", "markdown"] = Field(default="text")
    WECOM_BOT_ENABLED: bool = Field(default=False)
    WECOM_BOT_WEBHOOK_URL: SecretStr | None = Field(default=None)
    NOTIFY_DAILY_QUOTA: int = Field(default=60, ge=0)
    NOTIFY_QUIET_HOURS: str = Field(default="00:00-07:00")
    NOTIFY_CRITICAL_BYPASS_QUIET: bool = Field(default=False)
    NOTIFY_CHANNEL_PRIORITY: str = Field(default="feishu,wecom_app")
    NOTIFY_DELIVERY_MODE: DeliveryMode = Field(default=DeliveryMode.BROADCAST)
    NOTIFY_SEVERITY_ROUTING: str = Field(default="")
    NOTIFY_QUOTA_COUNTING_MODE: QuotaCountingMode = Field(default=QuotaCountingMode.PER_EVENT)
    NOTIFY_DEDUP_TTL_SECONDS: int = Field(default=86400, ge=0)
    NOTIFY_COOLDOWN_CRITICAL_SECONDS: int = Field(default=300, ge=0)
    NOTIFY_COOLDOWN_WARN_SECONDS: int = Field(default=1800, ge=0)
    NOTIFY_MAX_RETRIES: int = Field(default=4, ge=0)
    NOTIFY_RETRY_BACKOFF: str = Field(default="1s,3s,10s,30s")
    NOTIFY_AI_MERGE_WINDOW_SECONDS: int = Field(default=30, ge=0)
    NOTIFY_WECOM_TOKEN_REFRESH_MARGIN_SECONDS: int = Field(default=300, ge=0)

    # ---------------- LLM (24) ----------------
    LLM_PROVIDER_DEFAULT: LLMProvider = Field(default=LLMProvider.CLOUD)
    LLM_CLOUD_BASE_URL: str | None = Field(default=None)
    LLM_CLOUD_API_KEY: SecretStr | None = Field(default=None)
    LLM_PROVIDER_LOCAL_BASE_URL: str = Field(default="http://ollama:11434/v1")
    LLM_PROVIDER_LOCAL_MODEL: str | None = Field(default=None)
    LLM_PROVIDER_LOCAL_API_KEY: SecretStr | None = Field(default="ollama")
    LLM_TIER_LIGHT_MODEL: str | None = Field(default=None)
    LLM_TIER_STANDARD_MODEL: str | None = Field(default=None)
    LLM_TIER_DEEP_MODEL: str | None = Field(default=None)
    LLM_DAILY_TOKEN_BUDGET: int | None = Field(default=None, description="日 token 硬上限")
    LLM_DAILY_COST_BUDGET_USD: float | None = Field(default=None, description="日费用硬上限")
    LLM_MAX_INPUT_TOKENS: int = Field(default=24000, ge=1024)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=4000, ge=256)
    LLM_AGENT_MAX_TOKENS: int = Field(default=8000, ge=256)
    LLM_TIMEOUT_SECONDS: int = Field(default=60, ge=1)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0)
    LLM_SCHEMA_REPAIR_ATTEMPTS: int = Field(default=1, ge=0)
    LLM_ANALYSIS_TOTAL_BUDGET_SECONDS: int = Field(default=180, ge=1)

    # ---------------- 调度 (23) ----------------
    SCHEDULER_ENABLED: bool = Field(default=True)
    SCHEDULER_TIMEZONE: str = Field(default="UTC", description="cron 一律按 UTC 解释")
    SCHEDULER_MAX_BACKFILL_SECONDS: int = Field(default=86400, ge=0)
    SCHEDULER_LOCK_MODE: LockMode = Field(default=LockMode.POSTGRES_ADVISORY)
    SCHEDULER_WORKER_POOL: int = Field(default=4, ge=1)

    # ---------------- Freqtrade (P1) ----------------
    FREQTRADE_ENABLED: bool = Field(default=False)
    FREQTRADE_API_BASE_URL: str | None = Field(default=None)
    FREQTRADE_API_USERNAME: str | None = Field(default=None)
    FREQTRADE_API_PASSWORD: SecretStr | None = Field(default=None)
    FREQTRADE_DRY_RUN_ENFORCE: bool = Field(default=True, description="启动时强制校验 dry_run=true")
    FREQTRADE_IMAGE_TAG: str | None = Field(default=None, description="必须固定版本，禁止 latest")

    # ---------------- 可观测性 (37) ----------------
    METRICS_ENABLED: bool = Field(default=True)
    METRICS_BIND: str = Field(default="127.0.0.1:9100")
    METRICS_AUTH_REQUIRED: bool = Field(default=False)
    LOG_SAMPLING_RATE: float = Field(default=1.0, ge=0.0, le=1.0)

    # ---------------- 备份与保留 (27) ----------------
    BACKUP_ENABLED: bool = Field(default=True)
    BACKUP_DIR: str = Field(default="/data/backups")
    BACKUP_RETENTION_DAYS: int = Field(default=14, ge=1)
    BACKUP_RESTORE_DRILL_ENABLED: bool = Field(default=True)
    RETENTION_ENABLED: bool = Field(default=True)

    # ---------------- 校验器 ----------------
    @field_validator("LOG_SAMPLING_RATE")
    @classmethod
    def _check_sampling_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("LOG_SAMPLING_RATE 必须在 0.0 与 1.0 之间")
        return value

    @field_validator("NOTIFY_QUIET_HOURS")
    @classmethod
    def _check_quiet_hours(cls, value: str) -> str:
        """校验免打扰时段格式 ``HH:MM-HH:MM``。"""
        try:
            start_str, end_str = value.split("-", 1)
            for part in (start_str, end_str):
                hh, mm = part.split(":")
                if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                    raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError(f"NOTIFY_QUIET_HOURS 格式非法: {value!r}，应为 HH:MM-HH:MM") from exc
        return value

    @field_validator("NOTIFY_RETRY_BACKOFF")
    @classmethod
    def _check_retry_backoff(cls, value: str) -> str:
        """校验退避序列，如 ``1s,3s,10s,30s``。"""
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts:
            raise ValueError("NOTIFY_RETRY_BACKOFF 不能为空")
        for part in parts:
            if not part.endswith("s") or not part[:-1].isdigit():
                raise ValueError(f"NOTIFY_RETRY_BACKOFF 元素非法: {part!r}，应为 '10s' 形式")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> Settings:
        """跨字段不变量校验。失败必须阻止进程启动（32.3）。"""
        if not self.DATABASE_URL:
            raise ValueError("DATABASE_URL 缺失：配置校验失败必须阻止进程启动")
        if not self.REDIS_URL:
            raise ValueError("REDIS_URL 缺失：配置校验失败必须阻止进程启动")
        if self.AUTH_ENABLED:
            if self.AUTH_TOKEN is None and not self.AUTH_TOKEN_SHA256:
                raise ValueError("AUTH_ENABLED=true 时 AUTH_TOKEN 或 AUTH_TOKEN_SHA256 必须配置")
        if self.PUBLIC_CALLBACK_ENABLED and self.PUBLIC_CALLBACK_TOKEN is None:
            raise ValueError("PUBLIC_CALLBACK_ENABLED=true 时 PUBLIC_CALLBACK_TOKEN 必须配置")
        if self.FEISHU_ENABLED and self.FEISHU_WEBHOOK_URL is None:
            raise ValueError("FEISHU_ENABLED=true 时 FEISHU_WEBHOOK_URL 必须配置")
        if self.WECOM_APP_ENABLED:
            missing = [
                name
                for name in ("WECOM_CORP_ID", "WECOM_CORP_SECRET", "WECOM_AGENT_ID")
                if getattr(self, name) in (None, "")
            ]
            if missing:
                raise ValueError(f"WECOM_APP_ENABLED=true 时必须配置: {', '.join(missing)}")
        if self.WECOM_BOT_ENABLED and self.WECOM_BOT_WEBHOOK_URL is None:
            raise ValueError("WECOM_BOT_ENABLED=true 时 WECOM_BOT_WEBHOOK_URL 必须配置")
        if self.FREQTRADE_ENABLED:
            if not self.FREQTRADE_DRY_RUN_ENFORCE:
                raise ValueError("FREQTRADE_ENABLED=true 时 FREQTRADE_DRY_RUN_ENFORCE 必须为 true")
            if not self.FREQTRADE_IMAGE_TAG or self.FREQTRADE_IMAGE_TAG == "latest":
                raise ValueError("FREQTRADE_IMAGE_TAG 必须固定版本，禁止 latest")
        if self.LLM_MAX_OUTPUT_TOKENS > self.LLM_AGENT_MAX_TOKENS:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS 不得大于 LLM_AGENT_MAX_TOKENS")
        if self.APP_HOST not in ("127.0.0.1", "localhost", "::1") and not self.AUTH_ENABLED:
            raise ValueError("暴露到非回环地址时必须开启鉴权（34.5）")
        return self

    # ---------------- 派生属性 ----------------
    @property
    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.MARKET_SYMBOLS.split(",") if s.strip()]

    @property
    def market_types(self) -> list[MarketType]:
        return [MarketType(s.strip().upper()) for s in self.MARKET_TYPES.split(",") if s.strip()]

    @property
    def timeframes(self) -> list[Timeframe]:
        frames = [t.strip() for t in self.MARKET_TIMEFRAMES.split(",") if t.strip()]
        if self.MARKET_ENABLED_INTERVALS_1M and "1m" not in frames:
            frames.append("1m")
        return [Timeframe(f) for f in frames]

    @property
    def disabled_anomaly_rules(self) -> set[str]:
        return {r.strip() for r in self.ANOMALY_RULES_DISABLED.split(",") if r.strip()}

    @property
    def allowed_ips(self) -> set[str]:
        return {ip.strip() for ip in self.ALLOWED_IPS.split(",") if ip.strip()}

    @property
    def channel_priority(self) -> list[str]:
        return [c.strip() for c in self.NOTIFY_CHANNEL_PRIORITY.split(",") if c.strip()]

    @property
    def severity_routing(self) -> dict[str, list[str]]:
        """解析 ``CRITICAL:wecom_app,INFO:feishu``。"""
        routing: dict[str, list[str]] = {}
        for chunk in self.NOTIFY_SEVERITY_ROUTING.split(","):
            if not chunk.strip():
                continue
            severity, _, channels = chunk.partition(":")
            routing[severity.strip().upper()] = [
                c.strip() for c in channels.split(",") if c.strip()
            ]
        return routing

    @property
    def retry_backoff_seconds(self) -> list[int]:
        return [int(p.strip()[:-1]) for p in self.NOTIFY_RETRY_BACKOFF.split(",") if p.strip()]

    @property
    def is_loopback_only(self) -> bool:
        return self.APP_HOST in ("127.0.0.1", "localhost", "::1")

    @property
    def timezone(self) -> dt.tzinfo:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self.APP_TIMEZONE)
        except Exception as exc:  # pragma: no cover - 启动期即失败
            raise ValueError(f"APP_TIMEZONE 非法: {self.APP_TIMEZONE!r}") from exc

    # ---------------- 配置摘要与指纹 (32.3 / 32.7) ----------------
    def redacted_summary(self) -> dict[str, Any]:
        """启动时打印的脱敏配置摘要。敏感项只显示 ``***``。"""
        from aicrypto.common.redaction import mask_config_value

        summary: dict[str, Any] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                summary[name] = "***" if value.get_secret_value() else None
            else:
                summary[name] = mask_config_value(name, value)
        return summary

    def config_hash(self) -> str:
        """生效配置的哈希，写入 ``config_versions.config_hash``（32.3）。"""
        payload = json.dumps(self.redacted_summary(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例（进程启动时加载一次，32.3）。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """仅测试使用：清空缓存的 Settings。"""
    global _settings
    _settings = None
