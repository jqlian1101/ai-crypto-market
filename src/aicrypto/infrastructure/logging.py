"""结构化日志（PRD 第 37.1 章 / 第 36.7 章）。

要求：

- 用 ``structlog`` 输出 JSON；
- 禁止 ``print``；
- 禁止在日志中输出完整 prompt（只输出摘要与 hash）；
- 敏感值全部掩码（32.6）；
- 上下文键固定为 ``trace_id`` / ``request_id`` / ``job_run_id``。
"""

from __future__ import annotations

import logging
import random
import sys
from typing import Any

import structlog

from aicrypto.common.redaction import redact

#: 上下文绑定键（37.1）
CONTEXT_KEYS = ("trace_id", "request_id", "job_run_id")


def _mask_prompt_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """把 ``prompt`` 字段替换为长度与 hash，绝不输出完整 prompt（36.7）。"""
    for key in ("prompt", "system_prompt", "user_prompt", "messages"):
        value = event_dict.get(key)
        if isinstance(value, str):
            import hashlib

            event_dict[key] = {
                "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
            }
        elif isinstance(value, list):
            event_dict[key] = {"items": len(value)}
    return event_dict


def _redact_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """对所有字符串值做敏感信息脱敏（32.6）。"""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value)
    return event_dict


def configure_logging(
    *,
    level: str = "INFO",
    json_format: bool = True,
    sampling_rate: float = 1.0,
) -> None:
    """配置全局日志。进程启动时调用一次。"""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )
    # 第三方库日志降到 WARNING，避免刷屏
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "httpx", "websockets"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _mask_prompt_processor,
        _redact_processor,
        _sampling_processor,
    ]

    if json_format:
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        # 用 stdlib 的 LoggerFactory 而不是 PrintLoggerFactory：
        # 上面的 ``structlog.stdlib.add_logger_name`` 依赖 logger 有
        # ``.name`` 属性，``PrintLogger`` 没有，会在配置阶段就抛
        # ``AttributeError: 'PrintLogger' object has no attribute 'name'``。
        # 走 stdlib 还有一个好处：日志统一由 ``logging.basicConfig``
        # 写到 stdout，JSON 格式的日志行不会被第三方库的 handler 拆散。
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """获取绑定了模块名的 logger。"""
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """绑定上下文（trace_id / request_id / job_run_id）。"""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清空上下文。"""
    structlog.contextvars.clear_contextvars()


def set_log_sampling_rate(rate: float) -> None:
    """设置日志采样率（32.4 ``LOG_SAMPLING_RATE``）。

    采样在 processor 层实现：低于阈值的普通事件被丢弃，
    但 ``warning`` 及以上级别的事件**永远保留**。
    """
    global _sampling_rate
    _sampling_rate = max(0.0, min(rate, 1.0))


_sampling_rate = 1.0


def _sampling_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """按采样率丢弃普通级别日志。"""
    level = event_dict.get("level", "")
    if level in ("warning", "error", "critical"):
        return event_dict
    if _sampling_rate >= 1.0:
        return event_dict
    if random.random() < _sampling_rate:
        return event_dict
    raise structlog.DropEvent
