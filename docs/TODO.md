# AICryptoMarket 完整 TODO List

> 状态说明：`[x]` 表示已实现**且有测试覆盖**；`[ ]` 表示未做或未验证。
> 最近一次核对（2026-11-25）：P0 / P1 / P2 / P3 主体已完成，
> 后端测试 **933 passed / 0 skipped**（本地 Postgres 16 + Redis 7 跑通全部集成测试）。
> P4 已完成「Prompt 外置 + 模型调用计量 + 单价表接入 + 模型分层 +
> **超时部分结果**」，多 Agent 拆分待做。
>
> **遗留问题（已确认存在但未修复）单独记录在 [`docs/遗留问题.md`](遗留问题.md)**，
> 本清单只列任务级待办。
>
> **下一步执行顺序与依据见 [`docs/下一步开发计划.md`](下一步开发计划.md)**——
> 那是基于**代码实际状态**（而非本清单的勾选状态）的盘点结论，
> 含「本清单标未做、实际已实现」的更正，以及待建表清单。

## P0：工程底座（先做）

- [x] 推送本地 `main` 到远端（已与 `origin/main` 同步）
- [x] 建立 CI（`.github/workflows/ci.yml`），包含 pytest、ruff、mypy、前端 typecheck/lint/test（PRD 36.4）
- [x] 运行并修复现有测试基线
- [x] 建立 `config/catalog.toml` 与 CI 校验，确认与 `Settings` 一致（PRD 32.5）
- [x] 补齐 `tests/fixtures/anomaly/`，生成异动规则回放基线 `baseline.json`（PRD 36.5、28.2 第 10 项）
- [x] 打通集成测试环境：本地已用 `docker compose` 启动 Postgres/Redis，
      设置 `ANALYSIS_TEST_DATABASE_URL` / `ANOMALY_TEST_DATABASE_URL` 后 11 个集成测试全部执行
- [x] **推送本地提交触发 CI**（L-10）：已推送 `73fdd19..c05b2f8` 并实际跑通，
      Backend（ruff / mypy / pytest）与 Frontend（typecheck / lint / test）双 job 全绿。
      P2 代码首次在 Ubuntu + Postgres 16 + Redis 7 容器环境得到验证
- [ ] 按 PRD 36.3 必测清单，逐项映射到测试用例（已有大量单元测试，但尚未形成逐条映射表，部分不变量如补数、LLM、Freqtrade 对账仍缺测试）

## P1：行情采集层（Phase 1，M1 数据可信）

- [x] Binance WebSocket 采集器（现货与 USDⓈ-M），`market_collector.py`
- [x] WS 断线重连与恢复顺序（PRD 29.3）
- [x] 缺口三通路检测与主动补数（PRD 29.4）
- [x] 仅已收盘 K 线落库并参与计算，落库层物理隔离未收盘数据（PRD 29.5）
- [x] Spot 与 Futures 对齐（PRD 29.6）
- [x] 时间戳语义（received_at、processed_at）统一（PRD 29.8）
- [x] 写入幂等与重复推送去重（PRD 29.7）
- [x] 采集层降级矩阵落地（PRD 29.9）
- [x] 限流统一 RateLimiter 与退避（PRD 27.2）
- [x] 验收：断线补数后 K 线无缺口，重复推送不产生重复记录

## P2：衍生品与盘口数据

- [x] 采集 Open Interest 并写入 `open_interest_history`（REST 轮询，60s，`derivatives_collector.py`）
- [x] 采集 Funding Rate 并写入 `funding_history`（REST 轮询，`MARKET_FUNDING_POLL_SECONDS`）
- [x] 采集盘口快照（top20 + 低频）写入 `orderbook_snapshots`，并控制磁盘占用（PRD 17、27.3）
- [x] 采集成交流写入 `market_trades`（WS `aggTrade`）
- [x] 快照构建接入衍生品与盘口数据，移除 `data_quality="DEGRADED"` 的临时标记（`analysis_worker.py`）
- [x] 补齐 Derivatives、Liquidity 所需的特征计算（`domain/market/derivatives.py`）
- [x] 真实 API 与真实库端到端验证：9 项 REST 解析、spot WS 实时消息解析、
      四张表落库、幂等语义、迁移漂移（详见 `docs/遗留问题.md` 附录）
- [x] 修复真库验证暴露的 4 个缺陷：`rowcount` 误判、`as_of` 水位线缺失（前视偏差）、
      陈旧年龄负数、基差缺失原因不精确
- [x] **SPOT 市场分流**（L-01）：新增 `MARKET_CAPABILITIES` 能力表 + `supports()`，
      client 层 `_get_supported()` 在发请求前拦截并抛 `UnsupportedMarketError`，
      collector 按能力不排期衍生品轮询、现货不订阅 `markPrice` 流。
      实测现货请求数从 4 降到 1（只剩盘口深度）
- [x] **资金费率陈旧判定落地**（L-02）：按 PRD 29.4.3 实现两条判定——
      `_check_funding_history_staleness()`（REST，阈值 8h）与
      `_check_funding_current_staleness()`（WS markPrice，阈值 2min），
      `detected_by` 区分 `REST` / `WS`
- [ ] **数据保留与清理**（L-03、L-04）：`MARKET_TRADE_RETENTION_DAYS`、
      `RETENTION_ENABLED` 均只声明未消费；`market_trades` / `orderbook_snapshots`
      无限增长，与 PRD 27.3 冲突
- [x] **接入真实价格变化率**（L-06）：新增 `market.price_change_pct()`，
      快照的 `detected` 改用领域层 `price_oi_divergence()` 真实计算，
      `long_short_skew()` 不再恒传 `None`。
      价格与 OI 同源（期货侧）同窗口（1h），按根数取 K 线避免边界丢数
- [ ] **futures WS 实盘验证**（L-08）：本机 `fstream.binance.com` 连得上但收不到消息，
      `markPrice` 流与 futures `aggTrade` 落库路径未跑通；需换网络环境补验
- [ ] 第二数据源交叉校验：OI / Funding / 盘口目前只有 Binance 单源（PRD 28.2 第 8 项）
- [x] 接入 P3 衍生品异动规则：`DER_OI_SURGE` / `DER_OI_FLUSH` / `DER_FUNDING_EXTREME` /
      `DER_FUNDING_FLIP` / `DER_BASIS_EXTREME` / `DER_LS_SKEW` / `DER_PRICE_OI_DIVERGENCE`
      （见 P3 章节与 L-07）

## P3：异动检测（Phase 2.5）

- [ ] 核对 PRD 22.2 约 40 条规则，补齐 MVP 要求的价格、成交量、衍生品核心规则
- [x] **异动规则配置链路**（L-07）：新增 `config/anomaly_rules.py`，
      `config/base.toml` 的 `[anomaly.rules.*]` 各段**首次被真正读取**
      （此前改配置不改变行为）。类型错误必须报错，不静默回落 0
- [x] **衍生品类规则族**（L-07）：新增 `domain/anomaly/derivatives_rules.py`，
      实现 PRD 22.2.4 的 8 条 `DER_*` 规则；
      引擎新增 `DerivativesInputs`，`anomaly_scan` 新增 `load_derivatives_inputs()`。
      **缺失数据一律 skipped，绝不当作 0**
- [x] `DER_FUNDING_EXTREME` 的 WS 预估费率通道（L-05）：
      新增 `services/funding_current.py`，采集器把 `markPrice` 的当前预估费率
      写入 Redis（键 `market:funding_current:{market_type}:{symbol}`，
      TTL 360s > 陈旧阈值 120s），异动引擎可读取。
      **不写 `funding_history`**——已结算事实不可改写；
      键缺失 = 数据缺失（标记 skipped），不得当作费率为 0
- [x] `DER_LS_SKEW` / `DER_PRICE_OI_DIVERGENCE` 的规则实现（L-07）：
      多空比用「资金费率符号 + OI 变化」的联合标量做保守推断
      （Binance `globalLongShortAccountRatio` 未采集，不可直接观测）
- [x] 显式禁用依赖 1m 的规则，并在配置中留痕
      （`base.toml` 的 `disabled_rules` 现已被读取并生效）
- [x] **取数层静默失效修复**（2026-11-25 二次验证）：4 个「规则永不触发」的缺陷
      ——基差分位取错样本（用最旧样本当当前值）、多空分位历史样本套用当前 OI 变化、
      OI 变化率单位不匹配（小数 vs 百分数）、阈值配置在调用方忘记传参时回落到硬编码默认值。
      新增 `tests/integration/test_derivatives_anomaly_inputs.py`（10 项），
      每项都经「回退修复 → 确认测试失败」验证有效性。
      **教训：读历史序列算分位/变化率的逻辑必须有端到端造数验证兜底**
- [ ] 异动阈值用 2–4 周历史数据回放校准（PRD 28.2 第 4 项）
- [ ] 每条规则的历史触发频率与后续方向性统计（PRD 28.2 第 5 项）
- [ ] 异动后表现评估与规则版本化、降权机制（PRD 17）

## P4：LLM 与 AI 分析（Phase 3，M2）

**成本分析（仅记录与分析，暂不做成本优化）**
- [x] 实现 `llm_cost_records` 写入：模型、Agent、输入输出 token、估算费用、耗时
      ——`analysis_worker.py` 在**收到响应的当下**捕获用量并落库；
      失败调用同样记录（失败也消耗 token，漏记会让成本系统性低估）。
      单价缺失时 `estimated_cost_usd` 记 0 且 `price_snapshot.priced=false`
      **显式标记「未定价」**，绝不静默按 0 计费。
      回归测试：`tests/integration/test_llm_metering.py`（7 项，真库）
- [x] 建立 `model_pricing` 单价表，记录 `pricing_version`
      ——新增迁移 `0006_model_pricing`；单价主键为 `(model_id, pricing_version)`，
      改价新增版本行而非原地覆盖。
      **发现并接入**：``base.toml`` 的 ``[llm.model_pricing]`` 段**早已存在**
      （claude-haiku / sonnet / opus 真实单价）却**从未被任何代码读取**——
      与 L-03 同源的「声明未消费」。现已由 `config/model_pricing.py` 读取，
      经 `scripts/sync_model_pricing.py` 同步进表（幂等；同版本号下单价变了
      必须报错并递增版本号）。
      回归测试：`tests/unit/test_model_pricing_config.py`（17 项）
      + `tests/integration/test_model_pricing_sync.py`（9 项，真库）
- [x] 校验单次输入 token 上限 `LLM_MAX_INPUT_TOKENS`（计量校验，非预算控制）
      ——新增 `services/llm_retry.py` 的护栏；**发请求前**用
      「请求体字符数 / 4」粗估并拦截，超限抛 `LLM_BUDGET_EXCEEDED`。
      事前拦截是必须的：输入阶梯计价（>100K 时单价 ×5，成本优化 O11），
      等模型回包才知道就晚了——钱已经花了。
      回归测试：`tests/unit/test_llm_retry.py` + `tests/integration/test_llm_retry.py`
- [x] 写入 `llm_daily_cost_usd` 指标数据，仅用于观测
      ——**发现并接入**：6 个 LLM 指标（`llm_requests_total` /
      `llm_latency_ms` / `llm_tokens_total` / `llm_cost_usd_total` /
      `llm_daily_cost_usd` / `llm_schema_invalid_total`）**定义了却无人写入**，
      与 L-03 同源的「声明未消费」。现由 `record_model_run` 统一发布。
      指标写失败**绝不影响分析任务**——观测通道不能影响业务通道。
      回归测试：`tests/integration/test_llm_metrics.py`（4 项，真库）
- [ ] 成本分析报表：按模型、Agent、Prompt 版本聚合 token 与费用
      ——**数据源已就绪**（`model_runs` / `llm_cost_records` 已有真实数据）

**重试与修复**
- [x] 实现 `LLM_MAX_RETRIES` 重试循环（退避，仅对可重试错误）
      ——新增 `services/llm_retry.py`；`retry_count` 不再恒为 0。
      **只重试可重试错误**：429 / 5xx / 传输层错误可重试，
      4xx 与 Schema 失败**不重试**（重试只是白烧钱）。
      退避为指数退避（1s→2s→4s，封顶 30s），
      且**必须落在 `LLM_ANALYSIS_TOTAL_BUDGET_SECONDS` 之内**——
      总预算不够覆盖退避时直接放弃，否则一个卡住的任务会吃光 worker 预算。
      **每一次实际发出的请求都写一行 `model_runs` + 一条 `llm_cost_records`**
      （失败尝试同样消耗 token，漏记会让成本系统性低估）。
      回归测试：`tests/unit/test_llm_retry.py`（46 项）
      + `tests/integration/test_llm_retry.py`（14 项，真库）
      + 真实 HTTP 端到端验证（16/16）
- [x] 实现 `LLM_SCHEMA_REPAIR_ATTEMPTS`：校验失败时将错误回灌模型修复一次
      ——**只回灌「模型上一次的输出 + 错误摘要」，不重传原始快照上下文**
      （PRD v2.3 / 成本优化 O12）：重传快照会让每次 Schema 失败的成本
      翻倍以上，且模型可能「重新分析一遍」给出不同结论，破坏可复现性。
      提示词明确要求「只修正，不要重新分析、不要改变已有结论」。
- [x] 修复仍失败时标记该 Agent 缺失，禁止伪造输出（PRD 32.4）
      ——新增 `record_missing_agent`：写 `agent_runs`
      （`status=FAILED`、`degraded=true`、`degraded_reason` 说明原因），
      **不写 `agent_outputs`**——没有输出就没有输出行，
      不能塞占位空对象假装有输出。一个编出来的输出会被当成真实结论
      进入汇总、报告与预测追踪，后面所有环节都无法再区分它是编的。

**Prompt 与版本**
- [x] 将 Prompt 从 `analysis_worker.py` 外置到 `prompts/<agent>/<template>.v<n}.md`
      ——新增 `config/prompts.py` 加载器；版本号从**文件名**解析
      （不写在内容里，避免「改了内容忘了改版本号」的漂移）；
      哈希基于**原始字节**；缺占位符**必须报错**，绝不让 `{schema}` 字面量发给模型；
      指定版本不存在时**报错**，不回落「最大版本」（静默回落会让「v2 不存在」
      变成「用 v1 跑」，与 L-07a 同类）。
      回归测试：`tests/unit/test_prompt_loading.py`（23 项）
- [x] 写入 `prompt_versions`（版本、哈希、变更说明）
      ——`template_hash` 与磁盘文件实际哈希一致，登记幂等（重复调用不触发唯一约束冲突）
      ——**二次验证修复 L-23**：主键补上 `template_name`
      （`analysis.v1.md` 与 `analysis.user.v1.md` 都是 `(master, v1)`，
      二元组主键会让 **user 模板的登记被静默丢弃**——
      而它承载了「快照怎么渲染给模型」的全部指令，
      PRD 24.5 的「改一个字的 Prompt 无法追溯」对它完全失效）。
      新增迁移 `0007_prompt_versions_template_name`（主键改三元组）。
      回归测试：`tests/integration/test_llm_metering.py` 新增
      `test_both_prompt_templates_are_registered`
      + `tests/integration/test_llm_retry_concurrency.py` 新增
      `test_concurrent_retry_does_not_duplicate_prompt_versions`
      + `tests/integration/test_prompt_versions_backfill.py`（3 项，新增）
      + 真实 HTTP 端到端 10/10（两个模板哈希与磁盘逐一比对一致）
      + 迁移 up/down/全新库 全链可逆
      ——**二次验证再修复 1 个缺陷**：回填漏剥 `.j2` 后缀，
      会回填出 `analysis.v1.j2` 这种**带版本号的模板名**，
      主键语义直接错了（同模板的 v1/v2 会被当成两个模板）。
      PRD 24.5 明确允许 `.j2`（或 `.md`）两种形态。
      **回填分支只在「表里已有数据」时才走到**——
      全新库迁移正常**不代表**迁移正确。
      为此把回填表达式**提取成迁移模块的公开常量**，
      测试**直接引用实现**而非抄 SQL 副本。
- [x] 输出携带 `model_id`、`prompt_version`、`agent_version`
      ——`analysis_reports.versions` 不再是 `{}`，现写入
      `model_id` / `prompt_version` / `feature_version`。
      `agent_version` 待多 Agent 拆分后补齐

**多 Agent 编排（PRD 4.7、24.4）**
- [ ] 拆分 Technical、Market Structure、Market Regime、Derivatives、Liquidity、Event/Risk 等 Agent
- [ ] 并行 fan-out，Agent 之间互不可见
- [ ] Evidence Aggregator 合并证据并标注冲突
- [ ] Master Agent 综合，区分事实、推断与待验证假设
- [ ] 基于 `langgraph` 编排（依赖已声明但未使用）
      ——`pyproject.toml` 声明了 `langgraph>=0.2`，**全仓零 import**。
      **先决定再动手**：PRD 24.4 描述的是「LangGraph 状态图」，
      但用**显式 fan-out/fan-in 代码**落地更简单可控。
      若最终不用它，就**从 `pyproject.toml` 删掉**——
      留一个永远不用的依赖，正是「声明未消费」的第 9 次。
- [ ] 每次 Agent 调用写 `agent_runs` 与 `agent_outputs`，记录输入快照引用、耗时、token
      ——**表已就位且 `model_runs` 已接通**，`agent_runs` / `agent_outputs`
      仍待多 Agent 拆分后写入
- [ ] Reflection：最多 1 轮，仅针对具体争议点

**模型分层（PRD 24.1、24.3）**
- [x] 启用 `TIER_LIGHT` 与 `TIER_DEEP` 调用路径（当前只用 STANDARD）
      ——新增 `services/tier_routing.py`；此前 `LLM_TIER_LIGHT_MODEL` /
      `LLM_TIER_DEEP_MODEL` **定义了却无人读取**，调用路径硬编码
      `TIER_STANDARD`。现按档位解析模型，并把**实际生效**的档位写入
      `llm_cost_records.tier`（成本报表按档位分组的唯一依据）。
      **DEEP 绝不降级到 LIGHT**（PRD 24.1「不要降级这一档」）——
      拿 LIGHT 档的模型冒充 DEEP 的结果比直接失败更糟。
      回归测试：`tests/unit/test_tier_routing.py`（29 项）
      + `tests/integration/test_tier_routing.py`（9 项，真库）
      + 真实 HTTP 端到端验证（21/21）
- [x] 日报与分歧复盘使用 DEEP
      ——`tier_for_trigger()`：`RETROSPECTIVE` / `DAILY` / `WEEKLY` /
      `REVIEW` / `DEEP` 走 DEEP，其余（`MANUAL` / `SCHEDULED` / `ANOMALY`）
      走 STANDARD。**显式的 `retrospective=True` 优先于从 trigger 猜**——
      显式意图比字符串匹配可靠。
- [x] 本地模型不可用时回退云端，并在输出中标注
      ——本地传输层错误时回退云端**一次**（再回退也没用）；
      降级写入 `degraded` / `degraded_reason`（原因区分
      `LOCAL_UNAVAILABLE` 与 `TIER_MODEL_UNCONFIGURED`）。
      **静默降级最危险**——读者会以为拿到的是原档位的结论。
      云端未配置时**不做回退**（只会把「本地连不上」换成「云端也连不上」）。
- [x] 超时时输出「已完成部分」的部分结果，而非整体失败（PRD 24.4）
      ——新增 `services/partial_result.py`：从**已算好的快照**里提取
      确定性事实（各周期 regime / 结构 / 支撑阻力位），
      产出 `status="PARTIAL"` + `degraded=True` 的报告。
      **不伪造红线**（PRD 32.4）：`market_bias="UNAVAILABLE"`（**不是 `NEUTRAL`**）、
      `scenarios=[]`、`scenario_probabilities=None`、`dominant_factors=[]`。
      **Schema 修复失败是例外**——那时模型跑完了只是输出不合法，
      拿不合规输出拼部分报告等于把不可信内容洗成「部分可信」，
      故仍落 `FAILED` 并如实登记 `record_missing_agent`。
      回归测试：`tests/unit/test_partial_result.py`（18 项）
      + `tests/integration/test_partial_result.py`（4 项，真库）
      + 真实 socket 端到端 `scripts/verify_partial_result_timeout.py`
- [x] **计量独立于报告事务**（2026-11-25，L-24）
      ——**计量与报告同事务**会让模型调用失败时的**整个事务回滚**，
      **已烧掉的 token 与费用一起被销毁**，成本报表**系统性低估**
      （且模型越不稳定低估越多）。改为计量走独立事务，
      **每一次 HTTP 尝试立即提交**。
      代价：可能出现「有调用记录但没报告」——但那**恰恰是真实发生过的事**，
      比悄悄抹掉更诚实。
      回归测试：`tests/integration/test_partial_result.py::test_metering_survives_transaction_rollback`
      （让异常穿过真实 `session_scope`，断言 2 次尝试的计量存活）
- [x] **超时必须如实标为 `TIMEOUT`**（2026-11-25，L-25）
      ——超时被包成 `AICryptoError` 后**类型检查失配**，
      `degraded_reason` 会写成 `MODEL_CALL_FAILED`，
      **降级原因与真实原因不符**，排查会被引向错误方向。
      改为**同时按异常类型与错误码判定**。
      回归测试：`tests/integration/test_partial_result.py::test_timeout_is_classified_as_timeout_not_call_failure`
- [x] 报告写入 `degraded`、`degraded_reason`
      ——`create_report` 新增 `degraded_reason` 参数；
      降级时报告标记 degraded 并附原因。
      注意：`model_runs` 表**没有** `tier` 列——按档位分组是成本报表的需求，
      档位落在 `llm_cost_records.tier`（通过 `model_run_id` 关联）。

**证据与校验（PRD 4.7.3）**
> **2026-11-25 盘点更正**：以下 4 项此前标为未做，
> 但**代码里早已实现**——TODO 与实现脱节了（记为 L-26）。
> 教训：**清单是给人看的，代码才是事实**。
> 更新状态前必须 grep 实现，不能凭印象改。
- [x] 程序校验 `source_path` 指向的值与快照一致
      ——已在 `services/analysis.py::create_report`：
      按 `source_path` 逐段解析快照 `content`，
      路径解析不了抛 `LLM_SCHEMA_INVALID`，
      值不一致抛 `EVAL_LOOKAHEAD_VIOLATION`
- [x] 校验引用是否指向本次 `snapshot_id`
      ——同处：`evidence.snapshot_id != market_snapshot_id` 即拒绝
- [x] 情景概率互斥与归一化校验
      ——`domain/analysis/report.py::ScenarioProbability`：
      `up/down/flat` 各在 `[0,1]`，总和与 1 的偏差 > `1e-6` 即报错
- [x] 数值、时间、结构化条件的程序校验
      ——同处：`observed_at` / `available_at` 超出快照时间边界
      即抛 `EVAL_LOOKAHEAD_VIOLATION`（前视偏差防护）

**验收**
- [ ] 建立固定场景内容验收集：趋势一致、震荡、多周期冲突、衍生品背离、数据缺失、Agent 超时、无明确机会
- [ ] 规则基线、单模型、多 Agent 的配对对照实验
- [ ] 记录 LLM 评审模型与版本（仅辅助）

## P5：调度与运行层（Phase 3A，M3）

- [ ] 接入 APScheduler（依赖已声明但未使用）
      ——`pyproject.toml` 声明了 `apscheduler>=3.10`，**全仓零 import**
      （与 L-19 / L-20 / L-21 同型的「声明未消费」，第 8 次）。
      计划并入 P5 调度层落地
- [ ] **依赖声明 vs 实际 import 对账进 CI**（L-26 的机制化对策）
      ——「声明未消费」已累计出现 **9–10 次**
      （L-03 / L-07a / L-16 / L-18 / L-19 / L-20 / L-21 / L-23 / L-26），
      **不能再靠人记得查**。加一条 CI 检查：
      `pyproject.toml` 里声明的依赖必须至少被一处 import，
      零消费的依赖直接报错（或显式列入白名单并说明原因）。
- [ ] 建立 `scheduled_jobs`、`job_runs`、`job_locks` 表与迁移（PRD 23.3）
- [ ] 定时综合分析（1h）与日报复盘
- [ ] 异动触发完整分析流程（PRD 22.6）
- [ ] 异动扫描定时化（当前靠 `run_anomaly_scan.py` 手动执行）
- [ ] 补跑机制：错过任务记录为 `MISSED`，补跑标记 `BACKFILL`，不占实时配额
- [ ] 关机恢复：优先补数据，再生成当前分析（PRD 20.2 第 12 条）
- [ ] 幂等与任务锁
- [ ] 验收：连续 3 天定时分析按时产出，展示结论变化与昨日复盘

## P6：通知渠道（Phase 3A/3B，飞书优先）

- [ ] 飞书渠道连通性验证，确认是否需要签名校验（PRD 28.2 第 1 项）
- [ ] 将 Redis 去重、冷却、配额接入真实运行链路（`anomaly_scan.py` 目前由调用方注入）
- [ ] 三级配额模型：账户级、规则级、渠道级（PRD 22.4.4.2）
- [ ] 去重键 `(fingerprint, revision)` 与 AI 解读升级投递（PRD 22.4.4.1）
- [ ] 合并等待窗口 30s
- [ ] 免打扰（按用户时区）
- [ ] 通知模板（`notification_templates`）与 `notification_channel_state`
- [ ] 死信重试由调度中心驱动
- [ ] 推送失败有重试与降级，记录 `notification_events`
- [ ] 测试接口 `channel` 必填，测试消息不向真实接收人广播（PRD 22.8、31.9）
## P7：预测追踪与评估（Phase 4–5，M4、M5）

**口径先行（必须早于追踪代码）**
- [ ] 按 PRD 第 33 章定稿评估口径：基准价 P0、方向阈值 θ=0.30%、Brier、区间与覆盖率、MFE/MAE、失效判定
- [ ] 确定方向标签阈值 θ（PRD 28.2 第 9 项）
- [ ] 口径版本化（`evaluator_version`）
- [ ] 最小样本量 `N_min` 与 `INSUFFICIENT_SAMPLE` 行为
- [ ] 重叠预测窗口去重（重叠 > 50%）

**追踪与生命周期**
- [ ] 建立 `signals`、`predictions` 表与迁移（PRD 第 30.5 节）
- [ ] 分析报告登记 `prediction_ids`
- [ ] 到期自动判定对错
- [ ] 全量 Shadow Tracking，避免只评估成交信号（PRD 17）

**评估**
- [ ] 方向准确率、区间命中率、MFE/MAE 实现端到端流程
- [ ] 内容质量检查（PRD 5.2.1）
- [ ] 按 Agent、模型、Prompt、市场状态维度分组
- [ ] 回测与实盘特征一致性：单一实现源 + `feature_version` 逐值测试（PRD 第 38 章，Phase 5）

## P8：前端 Dashboard（Phase 3、6）

**已有**：首页、分析详情页、分享页 `/s/[id]`

**占位页待实现（约 16 个）**
- [ ] `/jobs` 任务运行历史（优先）
- [ ] `/system` 系统健康（优先）
- [ ] `/anomalies` 与 `/anomalies/[id]` 异动列表与详情
- [ ] `/notifications` 与 `/notifications/prefs` 通知记录与偏好
- [ ] `/markets/[symbol]` 市场详情，K 线与指标叠加
- [ ] `/predictions` 与 `/predictions/[id]` 预测列表与详情
- [ ] `/performance` 评估面板
- [ ] `/agents` Agent 表现
- [ ] `/opportunities` 机会列表
- [ ] `/research`、`/experiments`、`/experiments/[id]`（P1）
- [ ] `/paper`（Phase 7–8，暂缓）

**横向要求**
- [ ] 每个页面实现六种视图态（PRD 35.4）
- [ ] 数据质量前端表达（PRD 35.5）
- [ ] 评估页强制规则（PRD 35.7）
- [ ] 移动端阅读与鉴权流程实测（PRD 35.9，移动端为主要入口时必须验收）
- [ ] 图表约定（PRD 35.8）
- [ ] 分析阅读完善：历史复盘、相较上次变化（Phase 6）

## P9：安全与运维

- [ ] 确认 `docker-compose` 端口绑定 `127.0.0.1`（PRD 34.5）
- [ ] 家用宽带固定出口方案（PRD 28.2 第 2 项）
- [ ] **换网络环境补验 futures WS 与现货 REST**（L-08、L-09）：
      本机 `fstream.binance.com` 与 `api.binance.com` 的 REST/WS 均不通，
      衍生品实时流与基差双侧采集未经实盘验证
- [ ] 密钥轮换流程（PRD 34.9）
- [ ] 工具权限矩阵与 Tool Gateway（PRD 10.5、34.8）
- [ ] 备份策略与保留策略（PRD 27.3、37.7）
      —— 保留策略的**执行部分**见 L-03 / L-04
- [ ] 核对 `.env` 不入库（`.gitignore`）
- [ ] 指标埋点覆盖业务侧调用（PRD 37.2）
- [ ] 日志脱敏校验（`common/redaction.py`）
- [ ] 结构化日志与告警（PRD 37.5、37.6）

## P10：数据库迁移补齐

- [ ] 核对 PRD 第 30.9 节完整表清单，补齐缺失表
- [ ] 待建表：`scheduled_jobs`、`job_runs`、`job_locks`、`signals`、`predictions`、`anomaly_evaluations`、`notification_templates`、`notification_channel_state`
      （`model_pricing` 已于 `0006_model_pricing` 建立）
- [ ] 核对已有表与 PRD 第 30 章字段、索引、CHECK 约束一致

## 待实验校准的参数（不阻断开发，需数据支撑）

- [ ] 异动阈值（PRD 22.2）
- [ ] 冷却窗口与每日配额（初值 60）
- [ ] 方向阈值 θ（初值 0.30%，满 200 条样本后按资产校准）
- [ ] 各预测时间窗口与 K 线回看长度（PRD 18.1）
- [ ] 置信度阈值与价格区间宽度
- [ ] 模型档位映射（PRD 28.2 第 6 项；日预算上限暂缓，见延后清单）
- [ ] 全链路分析 SLO（p95 < 90s，硬上限 300s）

## 明确延后（不阻断 MVP）

- [ ] Freqtrade Dry-run PoC（Phase 7，PRD 28.2 第 7 项）
- [ ] 三账户隔离与执行评估（Phase 8）
- [ ] 经济性轨道与完整回测（Phase 6A，P1）
- [ ] 企业微信渠道（P1，暂不做）：自建应用与微信插件（PRD 22.5、28.2 第 3 项）、群机器人 Webhook、企微一次性公网回调入口（PRD 34.6）
- [ ] LLM 成本优化（暂不做，仅保留成本分析）：强制执行 `LLM_DAILY_TOKEN_BUDGET` 与 `LLM_DAILY_COST_BUDGET_USD` 硬上限、超限降级顺序（停 DEEP → STANDARD 降 LIGHT → 关闭非关键 Agent → 仅确定性结论，PRD 24.2）、模型档位成本调优
- [ ] 现货信号接入（PRD 18）
- [ ] 相似行情检索、研究实验工作台（P1）

## 建议执行顺序

1. **P0 工程底座**：CI 与测试基线，后续改动才有保护
2. **P1 + P2 数据层**：WebSocket、缺口检测、衍生品与盘口，这是所有上游的前置条件
3. **P3 异动**：数据到位后补规则并回放校准
4. **P4 LLM**：先做成本记录与分析，再拆多 Agent（成本优化暂缓）
5. **P5 调度 + P6 通知**：达成「每天有结论」（M3）
6. **P7 口径 → 追踪 → 评估**：严格按 PRD 第 33 章先定口径，避免不可逆的数据债（M4、M5）
7. **P8 前端**：随各阶段同步交付，不要留到最后
8. **P9、P10** 穿插进行

