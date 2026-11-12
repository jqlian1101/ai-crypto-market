"""领域层：与数据库表结构一一对应的领域模型与纯领域逻辑。

按 PRD 第 19 章的目录结构划分：

- :mod:`aicrypto.domain.market`      —— 行情数据与数据质量
- :mod:`aicrypto.domain.market_intelligence` —— 指标/结构/Regime 的**唯一实现**（38.2.1）
- :mod:`aicrypto.domain.analysis`    —— Agent 输出、快照、提示词版本
- :mod:`aicrypto.domain.signals`     —— 信号与预测生命周期
- :mod:`aicrypto.domain.evaluation`  —— 评估口径（33 章）
- :mod:`aicrypto.domain.execution`   —— 模拟交易（Phase 7–8）
"""
