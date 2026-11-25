"""模型单价的读取与同步（PRD 24.2 / 30.4 / 32.2）。

**为什么需要这一层**��``config/base.toml`` 的 ``[llm.model_pricing]``
段**早已存在**（含 claude-haiku / claude-sonnet / claude-opus 的真实单价），
但**没有任何代码读取它**——与 L-03（保留期配置只声明未消费）同源：
配置项写在那里，看起来「已配置」，实际不生效。

本模块建立完整链路::

    base.toml [llm.model_pricing]  ->  model_pricing 表  ->  llm_cost_records

**为什么单价要落库而不是每次读 TOML**：

1. ``llm_cost_records.price_snapshot`` 需要记录**当时生效**的单价。
   若单价只存在 TOML 里，改了 TOML 后历史成本就无法复算；
2. 落库后可以用 ``pricing_version`` 表达「这行单价从哪天起生效」，
   同一模型可以有多行（改价产生新版本），成本报表可按版本分组对比；
3. 审计时可以回答「这条成本是按哪一版单价算的」。

**同步是幂等的**：同一 ``(model_id, pricing_version)`` 重复同步不产生新行，
单价内容变化时按新版本号新增行。
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from aicrypto.config.settings import CONFIG_DIR

BASE_CONFIG_FILENAME = "base.toml"

#: 单价版本的默认版本号。改单价时**必须**改这个版本号，
#: 否则同步会认为「内容没变」而跳过，历史成本口径就乱了。
DEFAULT_PRICING_VERSION = "v1"


class PricingConfigError(ValueError):
    """单价配置非法。"""


@dataclass(frozen=True, slots=True)
class ModelPricingConfig:
    """一个模型的单价配置（来自 ``base.toml``）。"""

    model_id: str
    pricing_version: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    cached_input_usd_per_mtok: Decimal | None
    effective_from: dt.datetime
    provider: str
    source_url: str | None


def _require_decimal(table: dict[str, Any], key: str, model_id: str) -> Decimal:
    """读取一个单价字段。类型不对必须报错。

    **只接受 TOML 数值**（int / float），不接受字符串。
    字符串 ``"0.10"`` 看似合理，但它意味着「单价是手写的文本」——
    一旦有人写成 ``"0.1O"``（字母 O）就会变成解析失败或被误当 0。
    静默把字符串当 0 会让成本变成 0，比崩溃危险得多。

    float 会先转成 ``str`` 再进 ``Decimal``，避免二进制浮点误差
    （``0.1`` 的 float 值是 ``0.1000000000000000055511151231257827``）。
    """
    if key not in table:
        raise PricingConfigError(f"模型 {model_id!r} 的单价缺少 {key!r}")
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise PricingConfigError(
            f"模型 {model_id!r} 的 {key!r} 必须是数值，实际是 {type(value).__name__}"
        )
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise PricingConfigError(f"模型 {model_id!r} 的 {key!r} 不是合法数值: {value!r}") from exc


def _parse_effective_from(value: Any, model_id: str) -> dt.datetime:
    """解析生效时间。

    支持 ``YYYY-MM-DD`` 与 ISO 8601 带时区两种形式。
    **不带时区的日期按 UTC 处理**——数据库统一存 UTC（30.1）。
    """
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if not isinstance(value, str):
        raise PricingConfigError(
            f"模型 {model_id!r} 的 effective_from 必须是日期字符串，实际是 {type(value).__name__}"
        )
    text = value.strip()
    try:
        if len(text) == 10:
            return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    except ValueError as exc:
        raise PricingConfigError(
            f"模型 {model_id!r} 的 effective_from 不是合法日期: {value!r}"
        ) from exc


def load_model_pricing(
    path: Path | None = None,
    *,
    pricing_version: str = DEFAULT_PRICING_VERSION,
) -> list[ModelPricingConfig]:
    """读取 ``base.toml`` 的 ``[llm.model_pricing]`` 段。

    文件不存在或该段缺失时返回**空列表**——不报错。
    单价表可以先从数据库维护（由运维写入），base.toml 只是可选来源。

    但**单个模型的字段类型错误必须报错**：让半个模型的价格静默为 0，
    会让成本报表出现「有的模型免费、有的模型很贵」这种无法解释的分布。
    """
    target = path or (CONFIG_DIR / BASE_CONFIG_FILENAME)
    if not target.exists():
        return []
    with target.open("rb") as fh:
        data = tomllib.load(fh)

    llm = data.get("llm", {})
    if not isinstance(llm, dict):
        raise PricingConfigError(f"{target} 的 [llm] 必须是表")
    pricing_table = llm.get("model_pricing", {})
    if not isinstance(pricing_table, dict):
        raise PricingConfigError(f"{target} 的 [llm.model_pricing] 必须是表")

    result: list[ModelPricingConfig] = []
    for model_id, entry in pricing_table.items():
        if not isinstance(entry, dict):
            raise PricingConfigError(f"模型 {model_id!r} 的单价段必须是表")
        cached_raw = entry.get("cached_input_per_mtok")
        cached = (
            _require_decimal(entry, "cached_input_per_mtok", model_id)
            if cached_raw is not None
            else None
        )
        source_url = entry.get("source_url")
        if "effective_from" not in entry:
            # 缺生效日期必须报错：否则无法判断哪一版单价在什么时候生效，
            # 「取 as_of 时点最新的单价」就退化成「取文件里的顺序」。
            raise PricingConfigError(f"模型 {model_id!r} 的单价缺少 'effective_from'")
        result.append(
            ModelPricingConfig(
                model_id=model_id,
                pricing_version=pricing_version,
                input_usd_per_mtok=_require_decimal(entry, "input_per_mtok", model_id),
                output_usd_per_mtok=_require_decimal(entry, "output_per_mtok", model_id),
                cached_input_usd_per_mtok=cached,
                effective_from=_parse_effective_from(entry["effective_from"], model_id),
                provider=str(entry.get("provider", "anthropic")),
                source_url=str(source_url) if source_url is not None else None,
            )
        )
    return result


def sync_model_pricing(
    db: Any,
    *,
    path: Path | None = None,
    pricing_version: str = DEFAULT_PRICING_VERSION,
    now: dt.datetime | None = None,
) -> dict[str, int]:
    """把 ``base.toml`` 的单价同步进 ``model_pricing`` 表。

    **幂等**：同一 ``(model_id, pricing_version)`` 已存在且单价内容一致时
    跳过；内容变了则**报错**——因为内容变了应该递增 ``pricing_version``，
    而不是原地覆盖（原地覆盖会让历史成本记录的版本号指向新价格）。

    返回 ``{"inserted": n, "skipped": m, "updated": k}``。
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from aicrypto.infrastructure.database.models.analysis import ModelPricing

    entries = load_model_pricing(path, pricing_version=pricing_version)
    created_at = now or dt.datetime.now(dt.UTC)
    stats = {"inserted": 0, "skipped": 0, "updated": 0}

    for entry in entries:
        # 用**原子 upsert** 而不是「先查再插」：后者是 TOCTOU 竞态，
        # 两个同步进程并发跑时会一起查到「不存在」，然后一起插入，
        # 第二个开始就撞主键。
        # DO NOTHING + RETURNING 能原子地告诉我们「这行是不是我插进去的」。
        inserted = db.execute(
            pg_insert(ModelPricing)
            .values(
                model_id=entry.model_id,
                pricing_version=entry.pricing_version,
                provider=entry.provider,
                input_usd_per_mtok=entry.input_usd_per_mtok,
                output_usd_per_mtok=entry.output_usd_per_mtok,
                currency="USD",
                effective_from=entry.effective_from,
                created_at=created_at,
            )
            .on_conflict_do_nothing(index_elements=["model_id", "pricing_version"])
            .returning(ModelPricing.model_id)
        ).scalar_one_or_none()
        if inserted is not None:
            stats["inserted"] += 1
            continue

        # 冲突 = 这行已存在：比对单价，判断是「没变」还是「变了却没升版本」。
        existing = db.execute(
            select(ModelPricing).where(
                ModelPricing.model_id == entry.model_id,
                ModelPricing.pricing_version == entry.pricing_version,
            )
        ).scalar_one()
        same = (
            existing.input_usd_per_mtok == entry.input_usd_per_mtok
            and existing.output_usd_per_mtok == entry.output_usd_per_mtok
            and existing.provider == entry.provider
        )
        if same:
            stats["skipped"] += 1
            continue
        # 单价变了但版本号没变：这是配置错误。
        # 原地覆盖会让历史 llm_cost_records 的 pricing_version 指向新价格，
        # 审计时无法回答「当时按哪个价算的」。
        raise PricingConfigError(
            f"模型 {entry.model_id!r} 的版本 {entry.pricing_version!r} 单价已变化；"
            "请递增 pricing_version 后重新同步"
        )
    db.flush()
    return stats


__all__ = [
    "BASE_CONFIG_FILENAME",
    "DEFAULT_PRICING_VERSION",
    "ModelPricingConfig",
    "PricingConfigError",
    "load_model_pricing",
    "sync_model_pricing",
]
