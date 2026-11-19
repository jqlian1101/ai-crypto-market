"""ORM 模型注册入口；迁移显式导入此包以填充 Base.metadata。"""

from aicrypto.infrastructure.database.models import analysis, market, report, system

__all__ = ["analysis", "market", "report", "system"]
