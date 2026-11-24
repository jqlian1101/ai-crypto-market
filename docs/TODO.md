# AICryptoMarket 完整 TODO List

> 状态说明：`[x]` 表示已实现**且有测试覆盖**；`[ ]` 表示未做或未验证。
> 最近一次核对：P0 / P1 / P2 的主体已完成，后端测试 **551 passed / 0 skipped**
> （已启动本地 Postgres 跑通全部集成测试；此前为 443 passed / 11 skipped）。
>
> **遗留问题（已确认存在但未修复）单独记录在 [`docs/遗留问题.md`](遗留问题.md)**，
> 本清单只列任务级待办。

## P0：工程底座（先做）

- [x] 推送本地 `main` 到远端（已与 `origin/main` 同步）
- [x] 建立 CI（`.github/workflows/ci.yml`），包含 pytest、ruff、mypy、前端 typecheck/lint/test（PRD 36.4）
- [x] 运行并修复现有测试基线
- [x] 建立 `config/catalog.toml` 与 CI 校验，确认与 `Settings` 一致（PRD 32.5）
- [x] 补齐 `tests/fixtures/anomaly/`，生成异动规则回放基线 `baseline.json`（PRD 36.5、28.2 第 10 项）
- [x] 打通集成测试环境：本地已用 `docker compose` 启动 Postgres/Redis，
      设置 `ANALYSIS_TEST_DATABASE_URL` / `ANOMALY_TEST_DATABASE_URL` 后 11 个集成测试全部执行
- [ ] **推送本地提交触发 CI**（L-10）：本地领先 `origin/main` 2 个提交（P2 + 验证修复），
      CI 从未运行过 P2 代码。CI 配置本身完备（已含 Postgres/Redis service container
      与两个测试库环境变量），**不是配置缺失，是没推上去**。
      注意 CI 还跑 `ruff format --check`，本次仅跑了 `ruff check`
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
- [ ] **SPOT 市场分流**（L-01）：现货 collector 会去拉 fapi 的 OI / funding，
      端点路径与数据语义都不适用；现货只需盘口 + 成交（基差用）
- [ ] **资金费率陈旧判定落地**（L-02）：`FUNDING_STALE_THRESHOLD_SECONDS` 等常量
      已定义但从未使用，资金费率陈旧不产生 `DATA_STALE`
- [ ] **数据保留与清理**（L-03、L-04）：`MARKET_TRADE_RETENTION_DAYS`、
      `RETENTION_ENABLED` 均只声明未消费；`market_trades` / `orderbook_snapshots`
      无限增长，与 PRD 27.3 冲突
- [ ] **接入真实价格变化率**（L-06）：`long_short_skew()` 的 `price_change_pct`
      恒传 `None`，`price_oi_divergence()` 无调用点，导致多空偏向与价格/OI 背离
      永远无法产出（静默失效）
- [ ] **futures WS 实盘验证**（L-08）：本机 `fstream.binance.com` 连得上但收不到消息，
      `markPrice` 流与 futures `aggTrade` 落库路径未跑通；需换网络环境补验
- [ ] 第二数据源交叉校验：OI / Funding / 盘口目前只有 Binance 单源（PRD 28.2 第 8 项）
- [ ] 接入 P3 衍生品异动规则：`DER_OI_SURGE` / `DER_OI_FLUSH` / `DER_FUNDING_EXTREME` /
      `DER_FUNDING_FLIP` / `DER_BASIS_EXTREME` / `DER_LS_SKEW` / `DER_PRICE_OI_DIVERGENCE`
      （阈值已在 `config/base.toml` 备好，但无代码读取，见 L-07）

## P3：异动检测（Phase 2.5）

- [ ] 核对 PRD 22.2 约 40 条规则，补齐 MVP 要求的价格、成交量、衍生品核心规则
- [ ] 衍生品类规则接入 P2 数据后启用（阈值已在 `config/base.toml` 的
      `[anomaly.rules.derivatives]` 备好，但当前无代码读取，见 L-07）
- [ ] `DER_FUNDING_EXTREME` 用 WS `markPrice` 预估费率做低延迟触发源（L-05）；
      注意只写 `anomaly_events`，**不回写** `funding_history`（已结算事实不可改写）
- [ ] `DER_LS_SKEW` / `DER_PRICE_OI_DIVERGENCE` 需先补真实价格变化率输入（L-06）
- [ ] 显式禁用依赖 1m 的规则，并在配置中留痕
- [ ] 异动阈值用 2–4 周历史数据回放校准（PRD 28.2 第 4 项）
- [ ] 每条规则的历史触发频率与后续方向性统计（PRD 28.2 第 5 项）
- [ ] 异动后表现评估与规则版本化、降权机制（PRD 17）

## P4：LLM 与 AI 分析（Phase 3，M2）

**成本分析（仅记录与分析，暂不做成本优化）**
- [ ] 实现 `llm_cost_records` 写入：模型、Agent、输入输出 token、估算费用、耗时
- [ ] 建立 `model_pricing` 单价表，记录 `pricing_version`
- [ ] 校验单次输入 token 上限 `LLM_MAX_INPUT_TOKENS`（计量校验，非预算控制）
- [ ] 写入 `llm_daily_cost_usd` 指标数据，仅用于观测
- [ ] 成本分析报表：按模型、Agent、Prompt 版本聚合 token 与费用

**重试与修复**
- [ ] 实现 `LLM_MAX_RETRIES` 重试循环（退避，仅对可重试错误）
- [ ] 实现 `LLM_SCHEMA_REPAIR_ATTEMPTS`：校验失败时将错误回灌模型修复一次
- [ ] 修复仍失败时标记该 Agent 缺失，禁止伪造输出（PRD 32.4）

**Prompt 与版本**
- [ ] 将 Prompt 从 `analysis_worker.py` 外置到 `prompts/<agent>/<template>.v{n}.md`
- [ ] 写入 `prompt_versions`（版本、哈希、变更说明）
- [ ] 输出携带 `model_id`、`prompt_version`、`agent_version`

**多 Agent 编排（PRD 4.7、24.4）**
- [ ] 拆分 Technical、Market Structure、Market Regime、Derivatives、Liquidity、Event/Risk 等 Agent
- [ ] 并行 fan-out，Agent 之间互不可见
- [ ] Evidence Aggregator 合并证据并标注冲突
- [ ] Master Agent 综合，区分事实、推断与待验证假设
- [ ] 基于 `langgraph` 编排（依赖已声明但未使用）
- [ ] 每次 Agent 调用写 `agent_runs` 与 `agent_outputs`，记录输入快照引用、耗时、token
- [ ] Reflection：最多 1 轮，仅针对具体争议点

**模型分层（PRD 24.1、24.3）**
- [ ] 启用 `TIER_LIGHT` 与 `TIER_DEEP` 调用路径（当前只用 STANDARD）
- [ ] 日报与分歧复盘使用 DEEP
- [ ] 本地模型不可用时回退云端，并在输出中标注
- [ ] 超时时输出「已完成部分」的部分结果，而非整体失败（PRD 24.4）
- [ ] 报告写入 `degraded`、`degraded_reason`

**证据与校验（PRD 4.7.3）**
- [ ] 程序校验 `source_path` 指向的值与快照一致
- [ ] 校验引用是否指向本次 `snapshot_id`
- [ ] 情景概率互斥与归一化校验
- [ ] 数值、时间、结构化条件的程序校验

**验收**
- [ ] 建立固定场景内容验收集：趋势一致、震荡、多周期冲突、衍生品背离、数据缺失、Agent 超时、无明确机会
- [ ] 规则基线、单模型、多 Agent 的配对对照实验
- [ ] 记录 LLM 评审模型与版本（仅辅助）

## P5：调度与运行层（Phase 3A，M3）

- [ ] 接入 APScheduler（依赖已声明但未使用）
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
- [ ] 待建表：`scheduled_jobs`、`job_runs`、`job_locks`、`signals`、`predictions`、`anomaly_evaluations`、`model_pricing`、`notification_templates`、`notification_channel_state`
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
