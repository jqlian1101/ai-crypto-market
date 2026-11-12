"""ULID 前缀 ID 生成（PRD 第 30.1 章）。

约定：

===========  ==========================  ==============================
前缀          实体                          格式
===========  ==========================  ==============================
``snap_``     市场快照                     ``snap_{yyyymmdd}_{ulid10}``
``run_``      Agent / 分析运行              ``run_{yyyymmdd}_{ulid10}``
``out_``      Agent 输出                   ``out_{ulid10}``
``sig_``      信号（四层统一）              ``sig_{ulid10}``
``pred_``     预测记录                     ``pred_{ulid10}``
``ae_``       异动事件                     ``ae_{yyyymmdd}_{seq4}``
``ne_``       通知事件                     ``ne_{ulid10}``
``job_``      任务运行                     ``job_{ulid10}``
``txn_``      账务分录                     ``txn_{ulid10}``
===========  ==========================  ==============================

使用 ULID 而非 UUID4：ULID 前缀含时间戳，天然按创建时间排序，索引局部性更好。
"""

from __future__ import annotations

import datetime as dt
import threading

from ulid import ULID

#: 需要日期段的 ID 前缀
DATED_PREFIXES = frozenset({"snap", "run", "ae"})

#: 不带日期段的 ID 前缀
PLAIN_PREFIXES = frozenset({"out", "sig", "pred", "ne", "job", "txn"})

_ULID_LENGTH = 10

_seq_lock = threading.Lock()
_seq_counters: dict[tuple[str, str], int] = {}


def _today(now: dt.datetime) -> str:
    return now.strftime("%Y%m%d")


def new_ulid() -> str:
    """生成一个 26 位 ULID 字符串。"""
    return str(ULID())


def new_id(prefix: str, *, now: dt.datetime | None = None) -> str:
    """按前缀生成 ID。

    - ``snap``/``run``/``ae`` → ``{prefix}_{yyyymmdd}_{ulid10}``
    - 其余前缀 → ``{prefix}_{ulid10}``
    """
    if prefix in DATED_PREFIXES:
        moment = now or dt.datetime.now(dt.timezone.utc)
        return f"{prefix}_{_today(moment)}_{new_ulid()[-_ULID_LENGTH:]}"
    if prefix in PLAIN_PREFIXES:
        return f"{prefix}_{new_ulid()}"
    raise ValueError(f"未登记的 ID 前缀: {prefix!r}")


def new_snapshot_id(*, now: dt.datetime | None = None) -> str:
    return new_id("snap", now=now)


def new_run_id(*, now: dt.datetime | None = None) -> str:
    return new_id("run", now=now)


def new_output_id() -> str:
    return new_id("out")


def new_signal_id() -> str:
    return new_id("sig")


def new_prediction_id() -> str:
    return new_id("pred")


def new_notification_id() -> str:
    return new_id("ne")


def new_job_run_id() -> str:
    return new_id("job")


def new_txn_id() -> str:
    return new_id("txn")


def new_anomaly_event_id(
    *,
    now: dt.datetime | None = None,
    sequence: int | None = None,
) -> str:
    """生成异动事件 ID ``ae_{yyyymmdd}_{seq4}``。

    ``sequence`` 为 None 时使用进程内自增计数器（按天 + 资产维度）。
    同一进程内单调递增；跨进程唯一性由数据库唯一约束兜底。
    """
    moment = now or dt.datetime.now(dt.timezone.utc)
    day = _today(moment)
    if sequence is None:
        with _seq_lock:
            key = (day, "default")
            _seq_counters[key] = _seq_counters.get(key, 0) + 1
            sequence = _seq_counters[key]
    return f"ae_{day}_{sequence:04d}"


def reset_sequence_counter() -> None:
    """仅测试使用：重置自增计数器。"""
    with _seq_lock:
        _seq_counters.clear()
