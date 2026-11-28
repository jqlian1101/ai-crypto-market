"""ORM 模型注册入口；迁移显式导入此包以填充 Base.metadata。"""

from aicrypto.infrastructure.database.models import (
    analysis,
    anomaly,
    market,
    notification,
    report,
    scheduler,
    system,
)

__all__ = ["analysis", "anomaly", "market", "notification", "report", "scheduler", "system"]
