"""LLM 成本分析报表（PRD 24.2 / 24.4）。

**这个模块解决什么问题**：成本数据早就落库了（``llm_cost_records``），
但**没有任何东西读它**——又一个「有表无读方」（L-30 那一族）。
没有报表就没有基线，而「多 Agent 拆分是否值得」这个问题
**只能靠成本报表回答**：拆分会让调用量成倍上升，
没有成本数据就无法判断收益。

**核心纪律：未定价 ≠ 免费**。

``llm_cost_records.estimated_cost_usd`` 是 ``NOT NULL``，
单价缺失时只能写 0（见 ``services/analysis_worker.py::record_model_run``）。
若报表直接 ``SUM(estimated_cost_usd)``，
那些「没登记单价」的调用就会被当成**真的不花钱**——
成本被**系统性低估**，而且**低得没有任何提示**。

这与 L-05「键缺失 ≠ 费率为 0」是同一条原则。因此本模块：

1. 费用**只**累加 ``price_snapshot->'priced' == true`` 的行；
2. 未定价行**单独计数**，并报告其 token 量与涉及的模型；
3. 存在未定价行时，明确声明**总额是下界**（lower bound），不是实际支出。

**注意**：本地模型（``provider='local'``）零边际成本，
但若 ``model_pricing`` 里**没有**为它登记单价行，
它同样会被算作「未定价」。
正确做法是**为它登记一条 0 价行**，而不是让报表把它当免费。

**设计分层**：取数（:func:`fetch_cost_rows`）与聚合（:func:`build_cost_report`）
分开，聚合是**纯函数**——这样「未定价不等于免费」这条纪律
可以在**不碰数据库**的情况下被完整测试。
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.analysis import LLMCostRecord, ModelRun

#: 分组键为空时的显示占位
UNKNOWN = "(未知)"

#: 报表里费用的展示精度（美元小数位）
MONEY_DISPLAY_DP = 4

#: 报表默认回看天数
DEFAULT_WINDOW_DAYS = 30


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostRow:
    """一条成本记录的**规范化**形态（与数据库解耦）。

    刻意把「是否已定价」显式成一个布尔字段，而不是让下游去翻 JSONB——
    这样聚合逻辑不必懂 JSONB 的细节，
    「未定价 ≠ 免费」这条纪律就落在一个**可测试的纯函数**里。
    """

    created_at: dt.datetime
    model_id: str
    agent_name: str | None
    tier: str | None
    prompt_version: str | None
    status: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    priced: bool


@dataclass(frozen=True, slots=True)
class CostBucket:
    """一个分组维度的聚合结果。

    ``input_tokens`` / ``output_tokens`` 是**全部**行的 token 合计
    （含未定价行）——这是真实的资源消耗。
    ``cost_usd`` 则**只**累加已定价行——这是**已知**的费用。
    两者分开，正是为了不让「没登记单价」被读成「不花钱」。
    """

    key: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    unpriced_calls: int = 0
    unpriced_input_tokens: int = 0
    unpriced_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """合计 token（输入 + 输出）。"""
        return self.input_tokens + self.output_tokens

    @property
    def has_unpriced(self) -> bool:
        """该分组里是否存在未定价的调用。"""
        return self.unpriced_calls > 0


@dataclass(frozen=True, slots=True)
class UnpricedModel:
    """一个**未定价**模型的情况汇总。

    存在的意义是**可执行**：报表要能直接告诉运维
    「哪些模型的调用没有价格」，运维才能去补 ``model_pricing``。
    """

    model_id: str
    calls: int
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class CostReport:
    """一份完整的成本报表。"""

    since: dt.datetime
    until: dt.datetime
    total: CostBucket
    by_model: list[CostBucket] = field(default_factory=list)
    by_tier: list[CostBucket] = field(default_factory=list)
    by_agent: list[CostBucket] = field(default_factory=list)
    by_prompt_version: list[CostBucket] = field(default_factory=list)
    by_status: list[CostBucket] = field(default_factory=list)
    by_day: list[CostBucket] = field(default_factory=list)
    unpriced_models: list[UnpricedModel] = field(default_factory=list)

    @property
    def has_unpriced(self) -> bool:
        """是否存在未定价的调用。

        为 ``True`` 时，``total.cost_usd`` 是**下界**而非实际支出。
        """
        return self.total.unpriced_calls > 0

    @property
    def is_lower_bound(self) -> bool:
        """费用是否只是**下界**。"""
        return self.has_unpriced


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------


def snapshot_is_priced(snapshot: object) -> bool:
    """判断单价快照是否标记为「已定价」。

    ``price_snapshot.priced`` 由写入方显式落库
    （``estimate.is_priced``）。**缺失即视为未定价**——
    保守方向：宁可少报费用，也不把「没登记单价」误报成「免费」。

    字符串 ``"true"`` 也接受：JSONB 里的值理论上总是布尔，
    但快照是**历史数据**，不排除有人手工改过。
    """
    if not isinstance(snapshot, dict):
        return False
    value = snapshot.get("priced")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def fetch_cost_rows(
    db: Session,
    *,
    since: dt.datetime,
    until: dt.datetime,
) -> list[CostRow]:
    """取区间内的成本记录（规范化成 :class:`CostRow`）。

    **左连接 ``model_runs``** 拿 ``prompt_version`` 与 ``status``：
    ``llm_cost_records`` 本身**没有**这两列（PRD 30.4），
    而「哪版 Prompt 最烧 token」「失败调用烧了多少钱」
    正是报表要回答的问题。

    用 **LEFT JOIN** 而非 INNER JOIN：
    成本行与调用行同事务写入，理论上总是成对出现；
    但一旦出现孤儿行（人工改库、数据迁移遗漏），
    INNER JOIN 会**悄悄把它从报表里抹掉**——
    而成本行的存在本身就是一个**必须被看见的事实**。
    """
    statement = (
        select(
            LLMCostRecord.created_at,
            LLMCostRecord.model_id,
            LLMCostRecord.agent_name,
            LLMCostRecord.tier,
            LLMCostRecord.input_tokens,
            LLMCostRecord.output_tokens,
            LLMCostRecord.estimated_cost_usd,
            LLMCostRecord.price_snapshot,
            ModelRun.prompt_version,
            ModelRun.status,
        )
        .select_from(LLMCostRecord)
        .outerjoin(ModelRun, ModelRun.model_run_id == LLMCostRecord.model_run_id)
        .where(LLMCostRecord.created_at >= since)
        .where(LLMCostRecord.created_at <= until)
        .order_by(LLMCostRecord.created_at)
    )
    return [_to_cost_row(row) for row in db.execute(statement).mappings().all()]


def _to_cost_row(row: Any) -> CostRow:
    """把数据库行转成 :class:`CostRow`。"""
    return CostRow(
        created_at=row["created_at"],
        model_id=row["model_id"],
        agent_name=row["agent_name"],
        tier=row["tier"],
        prompt_version=row["prompt_version"],
        status=row["status"],
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cost_usd=Decimal(row["estimated_cost_usd"]),
        priced=snapshot_is_priced(row["price_snapshot"]),
    )


# --------------------------------------------------------------------------
# 聚合（纯函数，不碰数据库）
# --------------------------------------------------------------------------


def _aggregate(rows: Iterable[CostRow], key_of: Callable[[CostRow], str]) -> list[CostBucket]:
    """按某个维度聚合。

    **未定价行的费用一律不累加**，只单独计数与累计 token。
    """
    slots: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = key_of(row)
        slot = slots.get(key)
        if slot is None:
            slot = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": Decimal(0),
                "unpriced_calls": 0,
                "unpriced_input_tokens": 0,
                "unpriced_output_tokens": 0,
            }
            slots[key] = slot
        slot["calls"] += 1
        slot["input_tokens"] += row.input_tokens
        slot["output_tokens"] += row.output_tokens
        if row.priced:
            slot["cost_usd"] += row.cost_usd
        else:
            slot["unpriced_calls"] += 1
            slot["unpriced_input_tokens"] += row.input_tokens
            slot["unpriced_output_tokens"] += row.output_tokens
    return [
        CostBucket(
            key=key,
            calls=slot["calls"],
            input_tokens=slot["input_tokens"],
            output_tokens=slot["output_tokens"],
            cost_usd=slot["cost_usd"],
            unpriced_calls=slot["unpriced_calls"],
            unpriced_input_tokens=slot["unpriced_input_tokens"],
            unpriced_output_tokens=slot["unpriced_output_tokens"],
        )
        for key, slot in slots.items()
    ]


def _sort_by_cost(buckets: list[CostBucket]) -> list[CostBucket]:
    """按费用降序；费用相同时按 token 降序。

    token 作为次级排序键：**全是未定价**的分组费用都是 0，
    若只按费用排它们会挤在一起且顺序不确定——
    而它们之间的 token 差异恰恰是运维最需要先看到的。
    """
    return sorted(buckets, key=lambda b: (b.cost_usd, b.total_tokens), reverse=True)


def build_cost_report(
    rows: Iterable[CostRow],
    *,
    since: dt.datetime,
    until: dt.datetime,
) -> CostReport:
    """把原始成本行聚合成一份报表。

    纯函数——**不碰数据库**，因此「未定价不等于免费」这条纪律
    可以被完整地单元测试。
    """
    materialized = list(rows)
    total = (
        _aggregate(materialized, lambda row: "总计")[0] if materialized else CostBucket(key="总计")
    )

    unpriced_slots: dict[str, dict[str, int]] = {}
    for row in materialized:
        if row.priced:
            continue
        slot = unpriced_slots.setdefault(
            row.model_id,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        )
        slot["calls"] += 1
        slot["input_tokens"] += row.input_tokens
        slot["output_tokens"] += row.output_tokens

    unpriced_models = sorted(
        (
            UnpricedModel(
                model_id=model_id,
                calls=slot["calls"],
                input_tokens=slot["input_tokens"],
                output_tokens=slot["output_tokens"],
            )
            for model_id, slot in unpriced_slots.items()
        ),
        key=lambda m: (m.total_tokens, m.calls),
        reverse=True,
    )

    return CostReport(
        since=since,
        until=until,
        total=total,
        by_model=_sort_by_cost(_aggregate(materialized, lambda row: row.model_id or UNKNOWN)),
        by_tier=_sort_by_cost(_aggregate(materialized, lambda row: row.tier or UNKNOWN)),
        by_agent=_sort_by_cost(_aggregate(materialized, lambda row: row.agent_name or UNKNOWN)),
        by_prompt_version=_sort_by_cost(
            _aggregate(materialized, lambda row: row.prompt_version or UNKNOWN)
        ),
        by_status=_sort_by_cost(_aggregate(materialized, lambda row: row.status or UNKNOWN)),
        by_day=sorted(
            _aggregate(materialized, lambda row: row.created_at.date().isoformat()),
            key=lambda b: b.key,
        ),
        unpriced_models=unpriced_models,
    )


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def _display_width(text: str) -> int:
    """终端显示宽度（CJK 字符占 2 列）。

    用 ``len()`` 对齐中文表格会**错位**——
    报表全是中文表头，宽度算错会直接毁掉可读性。
    """
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int, *, align: str = "left") -> str:
    """按**显示宽度**补齐。"""
    padding = " " * max(0, width - _display_width(text))
    return padding + text if align == "right" else text + padding


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.{MONEY_DISPLAY_DP}f}"


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """渲染一张定宽表格。"""
    widths = [_display_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _display_width(cell))
    lines = [
        "  ".join(
            _pad(h, widths[i], align="right" if i > 0 else "left") for i, h in enumerate(headers)
        )
    ]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append(
            "  ".join(
                _pad(cell, widths[index], align="right" if index > 0 else "left")
                for index, cell in enumerate(row)
            )
        )
    return lines


def _render_bucket_table(buckets: Sequence[CostBucket], *, key_header: str) -> list[str]:
    """渲染一个分组维度。"""
    headers = (key_header, "调用", "输入 token", "输出 token", "费用", "未定价")
    rows = [
        (
            bucket.key,
            _fmt_int(bucket.calls),
            _fmt_int(bucket.input_tokens),
            _fmt_int(bucket.output_tokens),
            _fmt_money(bucket.cost_usd),
            _fmt_int(bucket.unpriced_calls),
        )
        for bucket in buckets
    ]
    return _render_table(headers, rows)


def render_cost_report(report: CostReport) -> str:
    """把报表渲染成**可读文本**。

    刻意**不做**前端页面：这份报表的第一读者是**开发者本人**，
    而它最重要的用途是「在拆分多 Agent 之前，先看清成本基线」。

    **未定价警告必须显眼**：费用是下界时，读者必须立刻知道，
    否则这份报表会变成一个**看起来精确、实际系统性低估**的数字。
    """
    lines: list[str] = []
    lines.append("LLM 成本报表")
    lines.append("=" * 78)
    lines.append(f"区间       {report.since.isoformat()} ~ {report.until.isoformat()}")
    priced_calls = report.total.calls - report.total.unpriced_calls
    lines.append(
        f"调用       {_fmt_int(report.total.calls)} 次"
        f"（已定价 {_fmt_int(priced_calls)} / 未定价 {_fmt_int(report.total.unpriced_calls)}）"
    )
    lines.append(
        f"token      输入 {_fmt_int(report.total.input_tokens)}"
        f" / 输出 {_fmt_int(report.total.output_tokens)}"
        f" / 合计 {_fmt_int(report.total.total_tokens)}"
    )
    lines.append(f"费用       {_fmt_money(report.total.cost_usd)}")
    lines.append("")

    if report.has_unpriced:
        lines.append("!! 警告：存在未定价调用")
        lines.append(
            f"   未定价调用 {report.total.unpriced_calls} 次，"
            f"合计 {_fmt_int(report.total.unpriced_input_tokens + report.total.unpriced_output_tokens)} token。"
        )
        lines.append("   上述费用是【下界】，不是实际支出——未定价不等于免费。")
        if report.unpriced_models:
            models = "、".join(
                f"{m.model_id}（{_fmt_int(m.calls)} 次）" for m in report.unpriced_models
            )
            lines.append(f"   未定价模型：{models}")
            lines.append("   处置：为这些模型在 model_pricing 登记单价行（本地模型可登记 0 价）。")
        lines.append("")

    if report.total.calls == 0:
        lines.append("（区间内没有任何调用记录）")
        return "\n".join(lines)

    lines.append("按模型")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_model, key_header="模型"))
    lines.append("")

    lines.append("按 Agent")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_agent, key_header="Agent"))
    lines.append("")

    lines.append("按档位")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_tier, key_header="档位"))
    lines.append("")

    lines.append("按 Prompt 版本")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_prompt_version, key_header="Prompt 版本"))
    lines.append("")

    lines.append("按调用状态")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_status, key_header="状态"))
    lines.append("")

    lines.append("按日")
    lines.append("-" * 78)
    lines.extend(_render_bucket_table(report.by_day, key_header="日期"))
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "MONEY_DISPLAY_DP",
    "UNKNOWN",
    "CostBucket",
    "CostReport",
    "CostRow",
    "UnpricedModel",
    "build_cost_report",
    "fetch_cost_rows",
    "render_cost_report",
    "snapshot_is_priced",
]
