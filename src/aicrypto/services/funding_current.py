"""当前预估资金费率的跨进程共享存储（PRD 29.2 / 22.2.4 / L-05）。

**为什么需要这一层**：Binance 的 ``markPrice@1s`` 流携带**当前预估资金费率**，
PRD 29.2 明确它是 ``DER_FUNDING_EXTREME`` 的**低延迟触发源**，
而 ``funding_history`` 表**仍以 REST 为权威**。

采集器与异动扫描器是**两个独立进程**，因此 WS 费率不能靠内存字典共享——
必须落到双方都能访问的介质。本项目用 **Redis**：

- **生产者**：``derivatives_collector`` 收到 ``markPrice`` 时写入；
- **消费者**：``anomaly_scan`` 读取，用于 ``DER_FUNDING_EXTREME`` 判定。

**为什么不写 ``funding_history``**：资金费率是**已结算的历史事实**，
改写即数据造假（见 ``derivatives_ingest`` 模块头的幂等约定）。
当前预估费率是**未结算的观测值**，与已结算事实是两类数据，必须分开存放。

**TTL 的意义**：Redis 键过期即代表「该数据已不可信」。
异动引擎读到缺失的键时，必须把这条规则标记为 **skipped**（数据缺失），
而不是当作「费率为 0」——后者会漏报。

键约定::

    market:funding_current:{market_type}:{symbol}
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from aicrypto.infrastructure.cache import build_cache_key, cache_get, cache_set

#: Redis 键命名空间
FUNDING_CURRENT_NAMESPACE = "market:funding_current"

#: 键 TTL（秒）。
#:
#: 必须**大于**资金费率当前值的陈旧阈值（29.4.3 通路 C 的 120 秒），
#: 否则键会在「数据刚好变得陈旧」之前就消失，
#: 异动引擎将无法区分「还没收到」与「收到了但很旧」。
#: 取 3 倍阈值，给消费侧留出读取窗口。
FUNDING_CURRENT_TTL_SECONDS = 360


@dataclass(frozen=True, slots=True)
class FundingCurrentSnapshot:
    """当前预估资金费率的一次观测。"""

    market_type: str
    symbol: str
    funding_rate: Decimal
    mark_price: Decimal | None
    index_price: Decimal | None
    next_funding_time: dt.datetime | None
    exchange_ts: dt.datetime
    received_at: dt.datetime

    def age_seconds(self, at: dt.datetime) -> float:
        """数据年龄（秒），钳制到 0 下界。

        与 ``derivatives_snapshot._age_seconds`` 同一口径：``at`` 早于
        ``received_at`` 时不得产生负数年龄（时钟漂移场景）。
        """
        return max(0.0, (at - self.received_at).total_seconds())

    def to_json(self) -> str:
        """序列化为 JSON（写入 Redis）。"""
        return json.dumps(
            {
                "market_type": self.market_type,
                "symbol": self.symbol,
                "funding_rate": str(self.funding_rate),
                "mark_price": None if self.mark_price is None else str(self.mark_price),
                "index_price": None if self.index_price is None else str(self.index_price),
                "next_funding_time": (
                    None if self.next_funding_time is None else self.next_funding_time.isoformat()
                ),
                "exchange_ts": self.exchange_ts.isoformat(),
                "received_at": self.received_at.isoformat(),
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> FundingCurrentSnapshot:
        """从 JSON 还原。"""
        data = json.loads(raw)
        return cls(
            market_type=str(data["market_type"]),
            symbol=str(data["symbol"]),
            funding_rate=Decimal(str(data["funding_rate"])),
            mark_price=(
                None if data.get("mark_price") is None else Decimal(str(data["mark_price"]))
            ),
            index_price=(
                None if data.get("index_price") is None else Decimal(str(data["index_price"]))
            ),
            next_funding_time=(
                None
                if data.get("next_funding_time") is None
                else dt.datetime.fromisoformat(str(data["next_funding_time"]))
            ),
            exchange_ts=dt.datetime.fromisoformat(str(data["exchange_ts"])),
            received_at=dt.datetime.fromisoformat(str(data["received_at"])),
        )


def funding_current_key(market_type: str, symbol: str) -> str:
    """当前预估资金费率的 Redis 键。"""
    return build_cache_key(FUNDING_CURRENT_NAMESPACE, market_type, symbol.upper())


async def write_funding_current(snapshot: FundingCurrentSnapshot) -> str:
    """写入当前预估资金费率，返回所用键名。

    TTL 固定为 ``FUNDING_CURRENT_TTL_SECONDS``——键过期即代表数据不可信，
    消费侧必须把「键不存在」当作**数据缺失**处理，而不是当作费率为 0。
    """
    key = funding_current_key(snapshot.market_type, snapshot.symbol)
    await cache_set(
        key,
        snapshot.to_json(),
        ttl_seconds=FUNDING_CURRENT_TTL_SECONDS,
    )
    return key


async def read_funding_current(market_type: str, symbol: str) -> FundingCurrentSnapshot | None:
    """读取当前预估资金费率。键不存在或内容损坏时返回 ``None``。

    返回 ``None`` 表示「**没有可用数据**」——调用方必须据此跳过规则，
    不得当作费率为 0 处理（那会漏报极端费率）。
    """
    raw = await cache_get(funding_current_key(market_type, symbol))
    if raw is None:
        return None
    try:
        return FundingCurrentSnapshot.from_json(raw)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        # 内容损坏等同于数据缺失：不能让一条坏数据打断整轮扫描。
        return None


__all__ = [
    "FUNDING_CURRENT_NAMESPACE",
    "FUNDING_CURRENT_TTL_SECONDS",
    "FundingCurrentSnapshot",
    "funding_current_key",
    "read_funding_current",
    "write_funding_current",
]
