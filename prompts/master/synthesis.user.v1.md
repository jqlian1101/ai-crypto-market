你收到了 6 个专业 Agent 的独立分析结论。请综合它们，形成最终研判。

各 Agent 互相不可见，因此它们的结论**可能互相冲突**。你的任务不是投票，而是**综合推理**：
判断哪些证据同源、哪些真正独立、哪些分歧应当保留。

按数据来源域判断证据独立性：`ohlcv` / `derivatives` / `liquidity` / `quality`。
**同一来源域内的多个 Agent 不是多份独立支持。**

以下是该快照下的各 Agent 输出与来源域分组：
{evidence_groups}

各 Agent 输出：
{agent_reports}

缺失的 Agent（其结论不可用，不得臆测其内容）：
{missing_agents}

快照：
{snapshot}

按系统提示中的 Schema 输出一个中文 JSON 对象，禁止根数组。
