"""市场快照（PRD 第 4.8 章 / 第 30.3 节）。

**防未来数据泄漏的强制约束**：

``as_of`` 之后的任何数据**不得**进入 ``content``。构建器在写入前必须校验
``content`` 中所有时间戳 <= ``as_of``。此校验必须有自动测试用例（36.3 / 15.3）。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from aicrypto.common.ids import new_snapshot_id
from aicrypto.domain.market.quality import DataQuality

#: 快照内容结构版本
SNAPSHOT_SCHEMA_VERSION = "1"

#: ISO-8601 时间戳匹配（含 Z 与 ±hh:mm 偏移）
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)

#: 快照内容中允许出现时间戳的键名（用于定向扫描）
_TIMESTAMP_KEYS = (
    "as_of",
    "observed_at",
    "timestamp",
    "time",
    "open_time",
    "close_time",
    "created_at",
    "updated_at",
    "updatedAt",
    "last_update",
    "watermark",
    "exchange_ts",
    "event_time",
)


@dataclass(slots=True)
class SnapshotContent:
    """快照内容。

    字段与 4.8 的建议清单一一对应。快照按 **Agent 视图裁剪**：
    不是所有 Agent 都看到全量（24.4），这是 token 预算的主要控制手段。
    """

    current_price: dict[str, Any] = field(default_factory=dict)
    multi_timeframe_ohlcv: dict[str, Any] = field(default_factory=dict)
    indicators: dict[str, Any] = field(default_factory=dict)
    market_structure: dict[str, Any] = field(default_factory=dict)
    volatility_and_volume: dict[str, Any] = field(default_factory=dict)
    derivatives: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    historical_patterns: dict[str, Any] = field(default_factory=dict)
    existing_predictions: dict[str, Any] = field(default_factory=dict)
    account_and_positions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_price": self.current_price,
            "multi_timeframe_ohlcv": self.multi_timeframe_ohlcv,
            "indicators": self.indicators,
            "market_structure": self.market_structure,
            "volatility_and_volume": self.volatility_and_volume,
            "derivatives": self.derivatives,
            "liquidity": self.liquidity,
            "market_regime": self.market_regime,
            "data_quality": self.data_quality,
            "historical_patterns": self.historical_patterns,
            "existing_predictions": self.existing_predictions,
            "account_and_positions": self.account_and_positions,
        }

    def trimmed_for(self, view: str) -> dict[str, Any]:
        """按 Agent 视图裁剪，剔除与该 Agent 无关的语义块。"""
        allowed = AGENT_VIEWS.get(view)
        if allowed is None:
            return self.to_dict()
        full = self.to_dict()
        return {k: v for k, v in full.items() if k in allowed}


#: 各 Agent 可见的快照视图（24.4：各自独立快照视图）
AGENT_VIEWS: dict[str, frozenset[str]] = {
    "technical": frozenset(
        {
            "current_price",
            "multi_timeframe_ohlcv",
            "indicators",
            "volatility_and_volume",
            "market_regime",
            "data_quality",
        }
    ),
    "structure": frozenset(
        {
            "current_price",
            "multi_timeframe_ohlcv",
            "market_structure",
            "market_regime",
            "data_quality",
        }
    ),
    "derivatives": frozenset(
        {
            "current_price",
            "derivatives",
            "market_regime",
            "data_quality",
        }
    ),
    "liquidity": frozenset(
        {
            "current_price",
            "liquidity",
            "volatility_and_volume",
            "data_quality",
        }
    ),
    "regime": frozenset(
        {
            "current_price",
            "multi_timeframe_ohlcv",
            "indicators",
            "volatility_and_volume",
            "market_regime",
            "data_quality",
        }
    ),
    "master": frozenset(
        {
            "current_price",
            "multi_timeframe_ohlcv",
            "indicators",
            "market_structure",
            "volatility_and_volume",
            "derivatives",
            "liquidity",
            "market_regime",
            "data_quality",
            "historical_patterns",
            "existing_predictions",
        }
    ),
    "risk": frozenset(
        {
            "current_price",
            "market_regime",
            "data_quality",
            "account_and_positions",
        }
    ),
}


@dataclass(slots=True)
class MarketSnapshot:
    """市场快照。"""

    symbol: str
    market_type: str
    as_of: dt.datetime
    content: SnapshotContent
    available_as_of: dt.datetime | None = None
    data_quality: DataQuality = DataQuality.GOOD
    data_quality_detail: dict[str, Any] = field(default_factory=dict)
    feature_version: str = "0.1.0"
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    source_watermarks: dict[str, Any] = field(default_factory=dict)
    time_range_start: dt.datetime | None = None
    time_range_end: dt.datetime | None = None
    snapshot_id: str = ""
    created_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = new_snapshot_id()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "as_of": self.as_of.isoformat(),
            "available_as_of": (self.available_as_of.isoformat() if self.available_as_of else None),
            "content": self.content.to_dict(),
            "data_quality": str(self.data_quality),
            "data_quality_detail": self.data_quality_detail,
            "feature_version": self.feature_version,
            "schema_version": self.schema_version,
            "source_watermarks": self.source_watermarks,
            "time_range_start": (
                self.time_range_start.isoformat() if self.time_range_start else None
            ),
            "time_range_end": self.time_range_end.isoformat() if self.time_range_end else None,
        }


def _parse_timestamp(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def iter_timestamps(
    payload: Any, *, parent_key: str | None = None
) -> list[tuple[str, dt.datetime]]:
    """递归收集内容中的时间戳。

    返回 ``(路径, 时间戳)`` 列表。路径形如 ``content.indicators.rsi.as_of``。
    """
    found: list[tuple[str, dt.datetime]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{parent_key}.{key}" if parent_key else str(key)
            found.extend(iter_timestamps(value, parent_key=path))
        return found
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            path = f"{parent_key}[{index}]" if parent_key else f"[{index}]"
            found.extend(iter_timestamps(value, parent_key=path))
        return found
    if isinstance(payload, str) and parent_key:
        last_segment = parent_key.rsplit(".", 1)[-1].split("[", 1)[0]
        looks_like_time_key = last_segment in _TIMESTAMP_KEYS
        if looks_like_time_key or _TIMESTAMP_RE.fullmatch(payload.strip()):
            parsed = _parse_timestamp(payload.strip())
            if parsed is not None:
                found.append((parent_key, parsed))
    return found


def find_future_timestamps(content: Any, as_of: dt.datetime) -> list[tuple[str, dt.datetime]]:
    """找出 ``as_of`` 之后的时间戳 —— 未来数据泄漏。

    这是 36.3 必测清单中「快照不含未来数据」的实现。
    """
    bound = as_of if as_of.tzinfo else as_of.replace(tzinfo=dt.UTC)
    violations: list[tuple[str, dt.datetime]] = []
    for path, moment in iter_timestamps(content):
        if moment > bound:
            violations.append((path, moment))
    return violations


def assert_no_future_data(content: Any, as_of: dt.datetime) -> None:
    """校验快照不含未来数据。违反时抛 :class:`ValueError`。"""
    violations = find_future_timestamps(content, as_of)
    if violations:
        detail = ", ".join(f"{path}={moment.isoformat()}" for path, moment in violations)
        raise ValueError(f"快照包含 as_of 之后的数据: {detail}")
