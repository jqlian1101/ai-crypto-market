"""项目级异常基类与权威错误码字典。

PRD 第 31.6 章是错误码的唯一真源：格式 ``{DOMAIN}_{REASON}``，全大写，
下划线分隔。新增错误码必须先登记在本模块的 :data:`ERROR_CATALOG`，禁止
在业务代码里临时造码（见 31.6 引言）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """权威错误码。``str(ErrorCode.X)`` 即响应中的 ``code`` 字段值。"""

    # --- 通用 (COMMON_*) ---
    COMMON_BAD_REQUEST = "COMMON_BAD_REQUEST"
    COMMON_VALIDATION_FAILED = "COMMON_VALIDATION_FAILED"
    COMMON_UNAUTHORIZED = "COMMON_UNAUTHORIZED"
    COMMON_FORBIDDEN = "COMMON_FORBIDDEN"
    COMMON_NOT_FOUND = "COMMON_NOT_FOUND"
    COMMON_CONFLICT = "COMMON_CONFLICT"
    COMMON_IDEMPOTENCY_KEY_REUSED = "COMMON_IDEMPOTENCY_KEY_REUSED"
    COMMON_RATE_LIMITED = "COMMON_RATE_LIMITED"
    COMMON_INTERNAL_ERROR = "COMMON_INTERNAL_ERROR"
    COMMON_SERVICE_UNAVAILABLE = "COMMON_SERVICE_UNAVAILABLE"
    COMMON_TIMEOUT = "COMMON_TIMEOUT"

    # --- 行情与数据 (DATA_*) ---
    DATA_NOT_FOUND = "DATA_NOT_FOUND"
    DATA_QUALITY_STALE = "DATA_QUALITY_STALE"
    DATA_QUALITY_MISSING = "DATA_QUALITY_MISSING"
    DATA_QUALITY_INVALID = "DATA_QUALITY_INVALID"
    DATA_GAP_DETECTED = "DATA_GAP_DETECTED"
    DATA_RATE_LIMITED = "DATA_RATE_LIMITED"
    DATA_SOURCE_DIVERGENCE = "DATA_SOURCE_DIVERGENCE"
    DATA_TIMESTAMP_INVALID = "DATA_TIMESTAMP_INVALID"

    # --- 分析与模型 (ANALYSIS_* / LLM_*) ---
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    ANALYSIS_SNAPSHOT_MISSING = "ANALYSIS_SNAPSHOT_MISSING"
    ANALYSIS_SKIPPED_DEPENDENCY = "ANALYSIS_SKIPPED_DEPENDENCY"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_SCHEMA_INVALID = "LLM_SCHEMA_INVALID"
    LLM_BUDGET_EXCEEDED = "LLM_BUDGET_EXCEEDED"
    LLM_CONTENT_REJECTED = "LLM_CONTENT_REJECTED"

    # --- 异动与通知 (ANOMALY_* / NOTIFY_*) ---
    ANOMALY_RULE_NOT_FOUND = "ANOMALY_RULE_NOT_FOUND"
    ANOMALY_RULE_VERSION_INVALID = "ANOMALY_RULE_VERSION_INVALID"
    ANOMALY_SCAN_IN_PROGRESS = "ANOMALY_SCAN_IN_PROGRESS"
    NOTIFY_CHANNEL_REQUIRED = "NOTIFY_CHANNEL_REQUIRED"
    NOTIFY_CHANNEL_DISABLED = "NOTIFY_CHANNEL_DISABLED"
    NOTIFY_CHANNEL_UNAVAILABLE = "NOTIFY_CHANNEL_UNAVAILABLE"
    NOTIFY_CHANNEL_UNKNOWN = "NOTIFY_CHANNEL_UNKNOWN"
    NOTIFY_QUOTA_EXCEEDED = "NOTIFY_QUOTA_EXCEEDED"
    NOTIFY_QUIET_HOURS = "NOTIFY_QUIET_HOURS"
    NOTIFY_DEDUPLICATED = "NOTIFY_DEDUPLICATED"
    NOTIFY_QUOTA_COUNTING_MODE = "NOTIFY_QUOTA_COUNTING_MODE"

    # --- 预测与评估 (PRED_* / EVAL_*) ---
    PRED_NOT_FOUND = "PRED_NOT_FOUND"
    PRED_ALREADY_EVALUATED = "PRED_ALREADY_EVALUATED"
    PRED_IMMUTABLE = "PRED_IMMUTABLE"
    EVAL_INSUFFICIENT_SAMPLE = "EVAL_INSUFFICIENT_SAMPLE"
    EVAL_RUN_IN_PROGRESS = "EVAL_RUN_IN_PROGRESS"
    EVAL_LOOKAHEAD_VIOLATION = "EVAL_LOOKAHEAD_VIOLATION"

    # --- 模拟交易与风控 (PAPER_* / RISK_* / FT_*) ---
    PAPER_ACCOUNT_NOT_FOUND = "PAPER_ACCOUNT_NOT_FOUND"
    PAPER_ACCOUNT_PAUSED = "PAPER_ACCOUNT_PAUSED"
    PAPER_ORDER_NOT_FOUND = "PAPER_ORDER_NOT_FOUND"
    PAPER_RECON_DIFF_UNRESOLVED = "PAPER_RECON_DIFF_UNRESOLVED"
    PAPER_DRY_RUN_VIOLATION = "PAPER_DRY_RUN_VIOLATION"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    RISK_SERVICE_UNAVAILABLE = "RISK_SERVICE_UNAVAILABLE"
    RISK_HALTED = "RISK_HALTED"
    FT_ADAPTER_TIMEOUT = "FT_ADAPTER_TIMEOUT"
    FT_INSTANCE_DOWN = "FT_INSTANCE_DOWN"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """一个错误码的元数据。"""

    code: ErrorCode
    http_status: int
    message: str
    retryable: bool = False


def _spec(code: ErrorCode, status: int, message: str, retryable: bool = False) -> ErrorSpec:
    return ErrorSpec(code=code, http_status=status, message=message, retryable=retryable)


#: 权威错误码目录：``code -> spec``。CI 可校验其与 PRD 31.6 一致。
ERROR_CATALOG: dict[ErrorCode, ErrorSpec] = {
    spec.code: spec
    for spec in (
        # 通用
        _spec(ErrorCode.COMMON_BAD_REQUEST, 400, "请求格式或参数非法"),
        _spec(ErrorCode.COMMON_VALIDATION_FAILED, 422, "字段级校验失败"),
        _spec(ErrorCode.COMMON_UNAUTHORIZED, 401, "未认证或 token 无效"),
        _spec(ErrorCode.COMMON_FORBIDDEN, 403, "已认证但无权访问该资源"),
        _spec(ErrorCode.COMMON_NOT_FOUND, 404, "资源不存在"),
        _spec(ErrorCode.COMMON_CONFLICT, 409, "状态冲突"),
        _spec(ErrorCode.COMMON_IDEMPOTENCY_KEY_REUSED, 409, "同一幂等键，请求体不同"),
        _spec(ErrorCode.COMMON_RATE_LIMITED, 429, "本服务限流", retryable=True),
        _spec(ErrorCode.COMMON_INTERNAL_ERROR, 500, "未预期错误", retryable=True),
        _spec(ErrorCode.COMMON_SERVICE_UNAVAILABLE, 503, "依赖不可用", retryable=True),
        _spec(ErrorCode.COMMON_TIMEOUT, 504, "处理超时", retryable=True),
        # 行情与数据
        _spec(ErrorCode.DATA_NOT_FOUND, 404, "指定时间段无数据"),
        _spec(ErrorCode.DATA_QUALITY_STALE, 409, "数据陈旧，拒绝生成依赖该数据的分析"),
        _spec(ErrorCode.DATA_QUALITY_MISSING, 409, "数据缺失，拒绝生成依赖该数据的分析"),
        _spec(ErrorCode.DATA_QUALITY_INVALID, 409, "数据非法"),
        _spec(ErrorCode.DATA_GAP_DETECTED, 409, "检测到 K 线缺口"),
        _spec(ErrorCode.DATA_RATE_LIMITED, 429, "交易所限流", retryable=True),
        _spec(ErrorCode.DATA_SOURCE_DIVERGENCE, 409, "多源/多接口价格不一致"),
        _spec(ErrorCode.DATA_TIMESTAMP_INVALID, 400, "时间区间非法"),
        # 分析与模型
        _spec(ErrorCode.ANALYSIS_NOT_FOUND, 404, "分析不存在"),
        _spec(ErrorCode.ANALYSIS_SNAPSHOT_MISSING, 409, "快照不存在或已过期"),
        _spec(ErrorCode.ANALYSIS_SKIPPED_DEPENDENCY, 409, "依赖任务未成功"),
        _spec(ErrorCode.LLM_UNAVAILABLE, 503, "模型服务不可用", retryable=True),
        _spec(ErrorCode.LLM_RATE_LIMITED, 429, "模型限流", retryable=True),
        _spec(ErrorCode.LLM_TIMEOUT, 504, "模型调用超时", retryable=True),
        _spec(ErrorCode.LLM_SCHEMA_INVALID, 502, "模型输出不符合 Schema"),
        _spec(ErrorCode.LLM_BUDGET_EXCEEDED, 429, "超出日预算硬上限"),
        _spec(ErrorCode.LLM_CONTENT_REJECTED, 400, "输入被内容策略拒绝"),
        # 异动与通知
        _spec(ErrorCode.ANOMALY_RULE_NOT_FOUND, 404, "规则不存在"),
        _spec(ErrorCode.ANOMALY_RULE_VERSION_INVALID, 400, "规则版本非法"),
        _spec(ErrorCode.ANOMALY_SCAN_IN_PROGRESS, 409, "已有扫描任务在跑"),
        _spec(ErrorCode.NOTIFY_CHANNEL_REQUIRED, 400, "缺少 channel 参数"),
        _spec(ErrorCode.NOTIFY_CHANNEL_DISABLED, 409, "渠道未启用"),
        _spec(ErrorCode.NOTIFY_CHANNEL_UNAVAILABLE, 503, "渠道健康检查失败", retryable=True),
        _spec(ErrorCode.NOTIFY_CHANNEL_UNKNOWN, 400, "未知渠道名"),
        _spec(ErrorCode.NOTIFY_QUOTA_EXCEEDED, 429, "账户级配额耗尽"),
        _spec(ErrorCode.NOTIFY_QUIET_HOURS, 409, "处于免打扰时段"),
        _spec(ErrorCode.NOTIFY_DEDUPLICATED, 200, "被去重层拦截"),
        _spec(ErrorCode.NOTIFY_QUOTA_COUNTING_MODE, 400, "配额计数模式非法"),
        # 预测与评估
        _spec(ErrorCode.PRED_NOT_FOUND, 404, "预测不存在"),
        _spec(ErrorCode.PRED_ALREADY_EVALUATED, 409, "已评估，重复评估被拒"),
        _spec(ErrorCode.PRED_IMMUTABLE, 409, "试图修改已创建的预测内容"),
        _spec(ErrorCode.EVAL_INSUFFICIENT_SAMPLE, 200, "样本不足"),
        _spec(ErrorCode.EVAL_RUN_IN_PROGRESS, 409, "评估任务已在运行"),
        _spec(ErrorCode.EVAL_LOOKAHEAD_VIOLATION, 409, "检测到未来数据泄漏，评估中止"),
        # 模拟交易与风控
        _spec(ErrorCode.PAPER_ACCOUNT_NOT_FOUND, 404, "模拟账户不存在"),
        _spec(ErrorCode.PAPER_ACCOUNT_PAUSED, 409, "账户已暂停"),
        _spec(ErrorCode.PAPER_ORDER_NOT_FOUND, 404, "订单不存在"),
        _spec(
            ErrorCode.PAPER_RECON_DIFF_UNRESOLVED, 409, "存在未解释对账差异，阻断新开仓"
        ),
        _spec(ErrorCode.PAPER_DRY_RUN_VIOLATION, 500, "Freqtrade 非 dry-run，拒绝启动"),
        _spec(ErrorCode.RISK_LIMIT_EXCEEDED, 409, "单笔风险/杠杆/日亏损限制"),
        _spec(ErrorCode.RISK_SERVICE_UNAVAILABLE, 503, "风控服务不可用，拒绝新开仓", True),
        _spec(ErrorCode.RISK_HALTED, 409, "已进入风控暂停态"),
        _spec(ErrorCode.FT_ADAPTER_TIMEOUT, 504, "Freqtrade 超时"),
        _spec(ErrorCode.FT_INSTANCE_DOWN, 503, "Freqtrade 实例不可用", retryable=True),
    )
}


class AICryptoError(Exception):
    """项目级异常基类（36.7：禁止裸 except，禁止 except Exception: pass）。"""

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        spec = ERROR_CATALOG.get(code)
        if spec is None:  # pragma: no cover - 由 catalog 测试守护
            raise ValueError(f"未登记的错误码: {code}")
        self.code = code
        self.message = message or spec.message
        self.details: dict[str, Any] = details or {}
        self.retryable = spec.retryable if retryable is None else retryable
        self.http_status = spec.http_status
        super().__init__(self.message)

    def to_payload(self, request_id: str) -> dict[str, Any]:
        """构造 31.2 定义的失败响应体。"""
        return {
            "error": {
                "code": str(self.code),
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
                "request_id": request_id,
            }
        }


class ConfigurationError(AICryptoError):
    """配置校验失败。32.3 要求：校验失败必须阻止进程启动。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.COMMON_BAD_REQUEST, message=message, details=details)


class AuthenticationError(AICryptoError):
    """34.3：未认证或令牌无效。"""

    def __init__(self, message: str = "未认证或 token 无效") -> None:
        super().__init__(ErrorCode.COMMON_UNAUTHORIZED, message=message)


class AuthorizationError(AICryptoError):
    """34.3：已认证但无权访问。"""

    def __init__(self, message: str = "无权访问该资源") -> None:
        super().__init__(ErrorCode.COMMON_FORBIDDEN, message=message)


class NotFoundError(AICryptoError):
    def __init__(self, message: str = "资源不存在", **details: Any) -> None:
        super().__init__(ErrorCode.COMMON_NOT_FOUND, message=message, details=details)


class ConflictError(AICryptoError):
    def __init__(self, message: str = "状态冲突", **details: Any) -> None:
        super().__init__(ErrorCode.COMMON_CONFLICT, message=message, details=details)


class DataQualityError(AICryptoError):
    """4.3：数据质量不满足，拒绝生成依赖该数据的分析。"""

    def __init__(self, code: ErrorCode, message: str | None = None, **details: Any) -> None:
        super().__init__(code, message=message, details=details)


class ServiceUnavailableError(AICryptoError):
    def __init__(self, message: str = "依赖不可用", **details: Any) -> None:
        super().__init__(ErrorCode.COMMON_SERVICE_UNAVAILABLE, message=message, details=details)


def error_code_of(exc: Exception) -> ErrorCode:
    """把任意异常映射到错误码，用于统一异常处理器。"""
    if isinstance(exc, AICryptoError):
        return exc.code
    return ErrorCode.COMMON_INTERNAL_ERROR
