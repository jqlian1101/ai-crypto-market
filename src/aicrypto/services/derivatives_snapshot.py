"""衍生品与流动性快照构建（PRD 4.5 / 29.6）。

从 ``open_interest_history`` / ``funding_history`` / ``orderbook_snapshots`` /
``market_trades`` 读数，产出分析快照的 ``derivatives`` 与 ``liquidity`` 两块内容。

**核心约束**（29.6）：任一数据源缺失或陈旧时，对应字段标 ``missing_data``
并**留空**，绝不用 0 或陈旧值替代——这样下游 Agent 才知道「不知道」，
而不是「值为 0」。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from aicrypto.domain.market.derivatives import (
    DataState,
    FundingPoint,
    OpenInterestPoint,
    OrderbookPoint,
    QuotePoint,
    TradePoint,
    compute_basis,
    depth_imbalance,
    funding_change,
    funding_flip,
    funding_percentile,
    large_trades,
    long_short_skew,
    oi_change_pct,
    oi_trend,
    spread_bps,
    taker_volume_split,
)
from aicrypto.services.derivatives_ingest import (
    latest_funding_timestamp,
    latest_oi_timestamp,
    load_funding_rates,
    load_latest_orderbook,
    load_open_interest,
    load_recent_trades,
)

#: OI 变化率窗口（22.2.4：1h）
OI_CHANGE_WINDOW_SECONDS = 3600
#: 盘口陈旧阈值（秒）。超过则该市场报价视为 STALE，不参与基差计算。
BOOK_STALE_SECONDS = 180.0
#: 成交统计回看窗口（秒）
TRADE_WINDOW_SECONDS = 3600


def _missing(reason: str) -> dict[str, Any]:
    """统一的「缺失」标记。"""
    return {"status": "missing_data", "reason": reason}


def _age_seconds(now: dt.datetime, business_ts: dt.datetime) -> float:
    """数据年龄（秒）。

    钳制到 0 下界：``now`` 早于数据业务时间时（时钟漂移或调用方传了
    历史时刻）不得产生负数年龄——负数会让「陈旧」判定被静默绕过。
    """
    return max(0.0, (now - business_ts).total_seconds())


def build_derivatives(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """构建衍生品特征块（OI + Funding）。"""
    oi_rows = load_open_interest(
        db,
        market_type=market_type,
        symbol=symbol,
        since=now - dt.timedelta(seconds=OI_CHANGE_WINDOW_SECONDS * 2),
        as_of=now,
    )
    funding_rows = load_funding_rates(
        db,
        market_type=market_type,
        symbol=symbol,
        as_of=now,
        limit=100,
    )

    if not oi_rows and not funding_rows:
        return _missing("持仓量与资金费率均无数据")

    result: dict[str, Any] = {}

    # ---- Open Interest ----
    if oi_rows:
        oi_points = [
            OpenInterestPoint(ts=row.ts, open_interest=row.open_interest) for row in oi_rows
        ]
        change = oi_change_pct(oi_points, window_seconds=OI_CHANGE_WINDOW_SECONDS)
        latest_ts = latest_oi_timestamp(db, market_type=market_type, symbol=symbol, as_of=now)
        age = _age_seconds(now, latest_ts) if latest_ts else None
        result["open_interest"] = {
            "status": "ok",
            "latest": float(oi_rows[-1].open_interest),
            "latest_ts": oi_rows[-1].ts.isoformat(),
            "change_1h_pct": change,
            "trend": oi_trend(oi_points, window_seconds=OI_CHANGE_WINDOW_SECONDS),
            "staleness_seconds": age,
            "samples": len(oi_rows),
            # 29.4.3：OI 只能轮询，检出延迟天然高于价格异动
            "sampling": "REST_POLL",
        }
    else:
        result["open_interest"] = _missing("无持仓量采样")

    # ---- Funding ----
    if funding_rows:
        funding_points = [
            FundingPoint(
                funding_time=row.funding_time,
                funding_rate=row.funding_rate,
                mark_price=row.mark_price,
            )
            for row in funding_rows
        ]
        current = funding_rows[-1].funding_rate
        latest_ts = latest_funding_timestamp(db, market_type=market_type, symbol=symbol, as_of=now)
        age = _age_seconds(now, latest_ts) if latest_ts else None
        result["funding"] = {
            "status": "ok",
            "current_rate": float(current),
            "current_funding_time": funding_rows[-1].funding_time.isoformat(),
            "mark_price": (
                None if funding_rows[-1].mark_price is None else float(funding_rows[-1].mark_price)
            ),
            "percentile": funding_percentile(funding_points, current=current),
            "change": funding_change(funding_points),
            "flipped": funding_flip(funding_points),
            "staleness_seconds": age,
            "samples": len(funding_rows),
            # 资金费率为 8h 口径，年化按 3 x 365 折算
            "annualized_rate": float(current) * 3 * 365,
        }
    else:
        result["funding"] = _missing("无资金费率记录")

    # ---- 价格与 OI 联合 ----
    oi_block = result["open_interest"]
    funding_block = result["funding"]
    oi_change = oi_block.get("change_1h_pct") if isinstance(oi_block, dict) else None
    funding_rate = funding_block.get("current_rate") if isinstance(funding_block, dict) else None
    result["price_oi_divergence"] = {
        "status": "ok" if oi_change is not None else "missing_data",
        "detected": False,
        "long_short_skew": long_short_skew(
            oi_change=oi_change,
            funding_rate=funding_rate,
            price_change_pct=None,
        ),
    }
    return result


def build_liquidity(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """构建流动性特征块（盘口 + 成交）。"""
    book_row = load_latest_orderbook(db, market_type=market_type, symbol=symbol, as_of=now)
    trades = load_recent_trades(
        db,
        market_type=market_type,
        symbol=symbol,
        since=now - dt.timedelta(seconds=TRADE_WINDOW_SECONDS),
        as_of=now,
    )

    result: dict[str, Any] = {}

    if book_row is None:
        result["orderbook"] = _missing("无盘口快照")
    else:
        age = _age_seconds(now, book_row.captured_at)
        if age > BOOK_STALE_SECONDS:
            result["orderbook"] = {
                "status": "missing_data",
                "reason": f"盘口快照陈旧 {age:.0f}s",
                "staleness_seconds": age,
            }
        else:
            book = OrderbookPoint(
                captured_at=book_row.captured_at,
                best_bid=book_row.best_bid,
                best_ask=book_row.best_ask,
                bid_qty_sum=book_row.bid_qty_sum,
                ask_qty_sum=book_row.ask_qty_sum,
            )
            result["orderbook"] = {
                "status": "ok",
                "captured_at": book_row.captured_at.isoformat(),
                "best_bid": float(book_row.best_bid),
                "best_ask": float(book_row.best_ask),
                "mid_price": float(book.mid_price),
                "spread_bps": spread_bps(book),
                "depth_imbalance": depth_imbalance(book),
                "bid_qty_sum": float(book_row.bid_qty_sum),
                "ask_qty_sum": float(book_row.ask_qty_sum),
                "depth_levels": len(book_row.bids or []),
                "staleness_seconds": age,
                # 4.5：盘口快照不是完整市场深度事实，必须说明采样频率
                "sampling_note": "仅 top20 快照，非完整深度；不得据此推断机构意图",
            }

    if not trades:
        result["taker_flow"] = _missing("无成交记录")
    else:
        points = [
            TradePoint(
                exchange_ts=t.exchange_ts,
                price=t.price,
                quantity=t.quantity,
                is_taker_buy=t.is_taker_buy,
            )
            for t in trades
        ]
        split = taker_volume_split(points)
        result["taker_flow"] = {
            "status": "ok",
            "window_seconds": TRADE_WINDOW_SECONDS,
            "samples": len(points),
            **split,
            "large_trades": large_trades(points),
        }
    return result


def build_basis(
    db: Session,
    *,
    symbol: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """构建 Spot-Futures 基差。

    基差必须两侧都有效才计算（29.6）：任一方缺失或陈旧都不算，
    并明确标记原因。
    """
    spot = _quote_point(db, market_type="SPOT", symbol=symbol, now=now)
    futures = _quote_point(db, market_type="USDM_FUTURES", symbol=symbol, now=now)
    result = compute_basis(spot, futures)
    if not result.is_available:
        # 明确区分「没采到」与「采到了但陈旧」，避免下游误判原因
        if spot is None and futures is None:
            reason = "现货与期货均无盘口快照"
        elif spot is None:
            reason = "无现货盘口快照"
        elif futures is None:
            reason = "无期货盘口快照"
        else:
            reason = result.detail or result.state.value
        return {
            "status": "missing_data",
            "basis_pct": None,
            "reason": reason,
            "spot_state": spot.state.value if spot else "MISSING",
            "futures_state": futures.state.value if futures else "MISSING",
        }
    return {
        "status": "ok",
        "basis_pct": result.basis_pct,
        "spot_mid_price": float(spot.mid_price) if spot else None,
        "futures_mid_price": float(futures.mid_price) if futures else None,
        "spot_captured_at": spot.captured_at.isoformat() if spot else None,
        "futures_captured_at": futures.captured_at.isoformat() if futures else None,
        "source_watermarks": {
            "spot": spot.captured_at.isoformat() if spot else None,
            "futures": futures.captured_at.isoformat() if futures else None,
        },
    }


def _quote_point(
    db: Session, *, market_type: str, symbol: str, now: dt.datetime
) -> QuotePoint | None:
    """从最新盘口快照构造报价点，附带陈旧状态。"""
    row = load_latest_orderbook(db, market_type=market_type, symbol=symbol, as_of=now)
    if row is None:
        return None
    age = _age_seconds(now, row.captured_at)
    state = DataState.GOOD if age <= BOOK_STALE_SECONDS else DataState.STALE
    return QuotePoint(
        market_type=market_type,
        captured_at=row.captured_at,
        best_bid=row.best_bid,
        best_ask=row.best_ask,
        state=state,
    )


def build_derivatives_snapshot(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """构建完整的衍生品 + 流动性 + 基差快照。"""
    derivatives = build_derivatives(db, market_type=market_type, symbol=symbol, now=now)
    liquidity = build_liquidity(db, market_type=market_type, symbol=symbol, now=now)
    basis = build_basis(db, symbol=symbol, now=now)
    return {
        "derivatives": derivatives,
        "liquidity": liquidity,
        "basis": basis,
    }


__all__ = [
    "BOOK_STALE_SECONDS",
    "OI_CHANGE_WINDOW_SECONDS",
    "TRADE_WINDOW_SECONDS",
    "build_basis",
    "build_derivatives",
    "build_derivatives_snapshot",
    "build_liquidity",
]
