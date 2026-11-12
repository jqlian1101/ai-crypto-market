"""Shared kernel primitives shared by every layer.

This package holds the cross-cutting primitives that PRD 第 30/31/32/36/37 章
specifies as mandatory contracts:

- :mod:`aicrypto.common.errors`     — 权威错误码字典 (31.6)
- :mod:`aicrypto.common.responses`  — 统一响应封装 (31.2)
- :mod:`aicrypto.common.pagination` — 分页语义 (31.3)
- :mod:`aicrypto.common.ids`        — ULID 前缀 ID 生成 (30.1)
- :mod:`aicrypto.common.clock`      — 可注入时钟 (36.1.3)
- :mod:`aicrypto.common.idempotency`— 幂等键语义 (31.4)
"""
