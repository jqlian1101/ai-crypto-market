"""基础设施层：数据库、日志、指标、缓存。

- :mod:`aicrypto.infrastructure.database` —— SQLAlchemy 基座与 session 管理
- :mod:`aicrypto.infrastructure.logging`   —— structlog 结构化日志（含脱敏）
- :mod:`aicrypto.infrastructure.metrics`   —— Prometheus 指标（第 37.2 章清单）
- :mod:`aicrypto.infrastructure.cache`     —— Redis 封装
"""
