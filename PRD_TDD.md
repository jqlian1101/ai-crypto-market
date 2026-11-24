# AI Crypto Market Intelligence & Analysis Platform
## 产品需求文档（PRD）与技术设计文档（TDD）

- 文档版本：2.4
- 文档状态：研发基线草案
- 产品范围：个人自用、本地部署、BTC/ETH 加密资产行情研究与 AI 分析
- 核心定位：**行情研究和 AI 分析为主；历史追踪、回测和模拟交易用于验证分析质量**
- 首选市场数据源：Binance
- 通知渠道：飞书机器人 Webhook（首期渠道）+ 企业微信自建应用/微信插件（触达个人微信，首选落地路径），预留邮件、Discord、Telegram 扩展位
- 模拟执行后端：Freqtrade Dry-run（辅助模块，最简实现路径）
- 部署方式：Local Docker Compose

## 0. 文档版本变更记录

| 版本 | 主要变更 |
|---|---|
| 2.0 | 研究基线：多 Agent 分析、快照冻结、Shadow Tracking、评估体系、Freqtrade 辅助验证 |
| 2.1 | 补齐三大缺口：① 实时异动检测与飞书推送规格（第 22 章）② 定时分析与调度中心规格（第 23 章）③ LLM 分层、成本预算与本地模型（第 24 章）；新增双轨评估（第 25 章）；修正 MVP 与路线图使推送/定时进入早期交付 |
| 2.2 | 新增企业微信渠道规格（第 22.5 节）：自建应用 + 微信插件（触达个人微信）与群机器人 Webhook 两条路径、鉴权/可信 IP/频率限制/消息类型降级规则、多渠道渲染与限流模型、配置项与验收标准；第 22 章整体重构为多渠道通知网关 |
| 2.3 | **工程化补齐**：新增第 29–38 章，覆盖直接决定可开发性的缺口——行情采集层规格、数据契约、API 契约、配置体系、评估指标数学口径、鉴权与公网边界、前端规格、测试与工程约定、可观测性目标、回测与实盘一致性；修正 9 处内部不一致（指纹去重 vs 事件升级、通知级别定义、配额三级模型、测试接口语义、章节交叉引用、路线图编号、表格清单回填等） |

> v2.1 的补齐原则：v2.0 的分析内核、快照冻结、评估体系、安全边界全部保留不变，只补「让系统真正能每天用起来」的运行层规格，并把「定时 + 推送 + 异常行情」从隐含能力提升为 P0 显式需求。
>
> v2.2 的补齐原则：通知网关从「飞书单渠道」升级为「多渠道」，核心不变（渠道抽象 + 治理机制 + 模板版本化）。新增企业微信规格的核心目的是解决一个真实约束：**飞书机器人无法触达个人微信**，而多数个人用户的通知入口在微信。本章新增内容只回答「怎么合规地推到微信」，不改动任何异动规则、分析流程或评估逻辑。
>
> v2.3 的补齐原则：v2.0–v2.2 定义的**产品行为**全部保留不变，只补**实现契约**。v2.0–v2.2 是一份「做什么」写得很好的文档，但把它变成能开工的代码还缺三层地基：**数据从哪来、以什么形状落地、对外长什么样**。v2.3 补齐这三层，并把散落在各章的隐含约定显式化。
>
> **v2.3 新增章节均为「契约层」内容，不改变任何业务规则、阈值或评估口径的行为定义。凡与既有章节冲突处，以 v2.3 新章为准，并在原章节标注交叉引用。**

## 0.0 v2.4 修订说明

- **AI 分析是主要功能**：行情、规则、调度、通知和评估均服务于分析质量与用户理解；主要产品对象为分析报告，预测、机会和交易计划为可选产物。
- 新增 4.7.1–4.7.3：分析报告契约、Agent 分工、综合判断与质量验收；补充第 5 章内容质量评价。
- 重写第 16、20 章：首版保留多 Agent 分析深度，收敛通知渠道、研究实验和模拟交易等外围范围。企业微信为 P1；首版可按个人实际入口选择一个通知渠道。
- 修正第 33 章的发布时间与基准价、概率事件定义、重叠样本处理和评估数据边界；补充前向预测与事后历史分析的隔离。
- 本次修改涉及业务定义与数据契约，不属于 v2.3 的纯工程补齐。**冲突时以本次明确修订的章节及第 20 章 MVP 范围为准**；其他章节的全量能力清单是目标范围，不表示全部阻断首版交付。

## 0.1 规格索引（开发导航）

按「实现某功能时该看哪章」组织，便于并行开发分工。

| 我要实现… | 必读章节 |
|---|---|
| 行情采集、WebSocket、补数 | **29**、27.2、4.2、4.3 |
| 数据库表结构与迁移 | **30**、11、22.7、23.3 |
| 后端 API | **31**、12、22.8、34 |
| 配置项与环境变量 | **32**、22.4.6、24.3 |
| 指标计算与评估结果 | **33**、5、4.4、4.6 |
| 前端页面 | **35**、8 |
| 测试、CI、代码规范 | **36**、15 |
| 监控、日志、性能目标 | **37**、13 |
| 回测引擎 | **38**、6、5.4 |
| 异动检测与推送 | 22、15.6、15.7 |
| 定时任务 | 23、15.8 |
| Agent 编排与 LLM 调用 | 24、10、4.7 |
| 模拟交易 | 7、26、15.4 |
| 安全边界 | 7.7、13.4、**34**、15.5 |

> 章节号变更说明：v2.0–v2.2 的章节号保持不变；v2.3 新增章节统一从第 29 章起（28 章之后），不重排既有编号，避免打断已有引用。原第 21 章「最终产品定义」为愿景性文字，已移至第 1.4 节。

---

# 1. 产品概述

## 1.1 产品定位

本项目定位为 **AI Crypto Market Intelligence & Analysis Platform**，是一套面向 BTC、ETH 的 AI 驱动加密资产行情研究与分析系统。

系统通过 Binance 行情数据建立多周期市场认知，结合技术指标、市场结构、衍生品、流动性和市场状态分析，由多个 AI Agent 协作完成行情解释、趋势判断、交易机会研究、风险识别和预测记录。

系统的首要目标不是自动下单或追求模拟账户收益，而是持续回答：

1. 当前市场处于什么状态？
2. 当前走势由哪些市场因素驱动？
3. 未来不同时间窗口可能出现什么情景？
4. 有哪些值得关注的交易机会，触发条件是什么？
5. AI 判断的证据、反证和不确定性是什么？
6. 后续真实行情是否支持当时的判断？
7. 哪些 Agent、模型、Prompt 和分析方法表现更好？

历史行情研究、回测、预测追踪和模拟交易用于验证分析质量。Freqtrade 仅作为可执行信号的模拟执行后端，不承担行情研究、AI 分析或策略研究的核心职责。

## 1.2 核心原则

1. 行情研究和 AI 分析是产品中心。
2. 市场数据质量是所有分析的前置条件。
3. 每次 AI 判断必须绑定当时的市场快照和数据时间戳。
4. AI 输出必须包含支持证据、反对证据、不确定性和失效条件。
5. 方向判断、价格预测、交易计划和模拟执行结果必须分开评估。
6. 不得仅用模拟交易 PnL 衡量 AI 分析准确性。
7. 未触发、未成交、被拒绝和未进入模拟交易的分析结果也必须持续追踪。
8. Python 确定性模块负责数据处理、指标、风险、账务和统计；LLM 负责理解、综合、解释和研究假设。
9. 模型不可用时，数据采集和确定性分析仍应运行；不得因 AI 故障放宽任何安全限制。
10. 策略或模型正式发布须经人工审批。
11. 第一阶段不接入真实交易账户，不允许真实下单、资金转移或提现。
12. 所有实验必须防止未来数据泄漏（look-ahead bias）。
13. 推送是产品的一等公民，不是附属功能：任何"值得用户知道"的事件都必须有明确的推送路径或明确的"不推送"理由。
14. 通知必须可静默：同一事件不得重复轰炸；无信息量的推送等于没有推送。
15. 异动检测以确定性规则为主，LLM 只负责解释和定性，不负责判断"是否异动"。
16. 定时任务与事件触发任务共用同一套幂等与补偿机制，错过的时间窗必须补跑并标注为「补跑」。

## 1.3 产品成功标准

- 能持续、可靠地采集并保存 BTC/ETH 多周期行情。
- 能生成可复现、可追溯的结构化市场分析。
- 能按预测时间窗口自动追踪实际行情。
- 能独立衡量方向判断、价格区间、目标触达、风险识别和概率校准。
- 能比较不同 Agent、模型、Prompt 和分析方法。
- 能在不依赖模拟交易的情况下评估 AI 分析质量。
- 可选地将通过风控的可执行信号送入隔离的 Freqtrade Dry-run 实例验证执行表现。
- 每个异动事件都在规定延迟内送达通知渠道（飞书/企业微信），且不产生重复轰炸。
- 每个定时分析任务按时产出结论，并在关机后能正确补跑。
- 用户能明确回答"这次分析提示是 AI 给的，还是规则给的，事后赚没赚"。

> 上述标准为定性描述。**可量化的验收口径（延迟目标、准确率基线、样本量门槛等）见第 37.4 节「量化成功指标」**，实现前须由 37.4 补齐后作为验收依据。

## 1.4 产品愿景

本项目是一套以 BTC、ETH 行情研究为基础、以 AI 多智能体市场分析为核心、以历史预测追踪和统计评估为主要验证手段、以模拟交易为辅助验证方式的个人加密资产研究平台。

系统的核心价值不在于自动产生订单，而在于持续产生可解释、可追溯、可验证的市场判断，并通过真实后续行情检验这些判断的质量。

**最终目标：建立一套能够持续研究市场、形成 AI 判断、记录预测、客观评估、发现分析缺陷并逐步改进的闭环系统。**

---

# 2. 产品范围

## 2.1 第一阶段范围

| 类别 | 范围 |
|---|---|
| 资产 | BTC、ETH |
| 数据源 | Binance |
| 市场数据 | Spot 与 USDⓈ-M Futures 行情数据；模拟执行优先 Futures |
| 周期 | 5m、15m、1h、4h；1m 可选 |
| AI 分析 | 多 Agent 协作、结构化报告、情景分析 |
| 定时分析 | 调度中心；定时任务 + 事件驱动混合触发 |
| 异常行情 | 异动检测规则引擎、分级告警 |
| 通知推送 | 多渠道：飞书机器人 Webhook、企业微信自建应用/微信插件、企业微信群机器人；事件分类、去重、冷却、配额 |
| 研究 | 历史行情检索、相似行情、预测追踪 |
| 评估 | 方向、区间、目标、风险、概率校准、经济性 |
| 回测 | 分析规则和交易策略的历史验证 |
| 模拟交易 | Freqtrade Dry-run，作为辅助验证 |
| 部署 | 本地 Docker Compose |
| LLM | 云端 API 为主 + 本地模型可选，统一 LLM Gateway 调用 |

## 2.2 非目标

第一阶段不建设：

- 真实交易执行系统。
- 交易所资金转移、提现或账户管理。
- 面向公众的 SaaS、多租户和用户权限平台。
- 高频交易、做市或低延迟执行系统。
- 全市场海量币种覆盖。
- 无人工审批的策略自动发布。
- 以模拟账户收益作为唯一产品 KPI。

## 2.3 交易模拟范围

模拟交易属于 P1 辅助能力：

- 首期验证 Binance USDⓈ-M USDT 本位永续。
- 标的 BTCUSDT、ETHUSDT。
- Freqtrade Dry-run。
- 三套独立研究账户用于 Rules-only、AI-only、Hybrid 对照。
- 每个账户初始模拟资金建议 500 USDT。
- 杠杆上限建议 3x。
- 单笔风险建议 0.5% 初始权益。
- 日亏损限制建议 1.5% UTC 日初权益。

以上资金和风险参数属于初始实验配置，需经 PoC、回测和风险审查确认，不应视为已证明安全或盈利的参数。现货模拟可在后续阶段接入。

---

# 3. 用户与核心使用场景

## 3.1 目标用户

个人研究者/开发者，使用本地系统研究 BTC、ETH 行情，观察 AI 分析质量，并通过历史数据和模拟执行验证研究结论。

## 3.2 核心使用场景

### 场景 A：查看当前市场状态

用户打开 Dashboard，查看 BTC、ETH 的多周期走势、市场状态、关键价位、成交量、衍生品和流动性摘要。

### 场景 B：生成 AI 综合分析

用户触发或等待事件驱动分析。系统调用相关 Agent，输出市场偏向、情景概率、支持证据、反对证据、关键触发条件和失效条件。

### 场景 C：研究交易机会

系统发现潜在机会，形成 Market Opportunity 或 Trade Setup。即使尚未满足入场条件，也保存并持续追踪。

### 场景 D：验证历史判断

系统按预测时间窗口获取后续行情，计算方向命中、价格区间误差、目标触达、MFE、MAE 等指标。

### 场景 E：比较 AI 分析方法

用户比较不同模型、Prompt、Agent 组合、规则方法和市场状态下的分析表现。

### 场景 F：模拟交易验证

仅将满足执行条件且通过风控审批的信号送入 Freqtrade Dry-run，检查入场、止损、止盈、手续费、Funding 和模拟盈亏。

---

# 4. 功能需求与优先级

## 4.1 模块优先级

| 模块 | 说明 | 优先级 |
|---|---|---|
| Binance Market Data Engine | 行情采集、标准化、历史补数 | P0 |
| Data Quality Engine | 完整性、时效性、异常检测 | P0 |
| Market Intelligence | 指标、结构、趋势、市场状态 | P0 |
| Derivatives Analysis | OI、Funding、基差等 | P0 |
| Liquidity Analysis | Order Book、成交、流动性线索 | P0 |
| Market Snapshot Builder | 构建可复现的分析上下文 | P0 |
| Multi-Agent Analysis | 多 Agent 行情研究与综合判断 | P0 |
| Anomaly Detection | 异动检测规则引擎、分级与去抖 | P0 |
| Scheduler | 定时任务编排、补偿、幂等 | P0 |
| Notification Gateway | 多渠道推送（飞书/企业微信）、模板、去重、冷却、配额 | P0 |
| Market Research | 历史行情和相似情景研究 | P0 |
| Signal Intelligence | 分析结果、机会和生命周期 | P0 |
| Prediction Tracking | 对所有预测持续追踪 | P0 |
| Analysis Evaluation | AI 分析质量评估 | P0 |
| Dashboard | 行情研究和分析展示 | P0 |
| Backtesting Engine | 历史验证 | P1 |
| Strategy Research Lab | 分析方法和策略实验 | P1 |
| Freqtrade Adapter | 模拟执行适配 | P1 |
| Paper Trading | Dry-run 验证 | P1 |
| Strategy Health Monitor | 质量退化监控 | P1 |
| Advanced Execution Simulation | 精细滑点、成交模拟 | P2 |
| Strategy Automation | 审批后的受控发布 | P2 |

## 4.2 行情数据需求

系统应采集和存储：

- OHLCV K 线。
- 成交额、成交笔数。
- 主动买入/卖出量（数据源支持时）。
- 最新成交和必要的逐笔聚合数据。
- Order Book 快照或增量数据（按资源预算选择）。
- Futures Open Interest。
- Funding Rate 与历史 Funding。
- Spot-Futures Basis。
- 合约规格、精度、最小下单量等元数据。
- 数据源时间、接收时间、处理时间。

必须记录交易所时间与本地接收时间，不能只依赖本地时间戳。

## 4.3 数据质量需求

> 检测的具体方法、时效阈值与降级矩阵见**第 29.4 节（缺口检测）与 29.9 节（故障降级矩阵）**；表结构见第 30.2 节。

Data Quality Engine 至少检查：

- K 线时间连续性和缺口。
- 重复记录。
- OHLC 合法性（High/Low 与 Open/Close 关系）。
- 异常成交量和价格跳变。
- 数据延迟。
- WebSocket 断线和恢复。
- REST 补数与 WebSocket 数据冲突。
- OI、Funding 等数据的更新时间差异。
- 数据源状态和字段缺失。

数据质量状态建议：

- `GOOD`
- `DEGRADED`
- `STALE`
- `MISSING`
- `INVALID`

当任务所需数据为 `STALE`、`MISSING` 或 `INVALID` 时，AI 必须收到明确的数据质量标记；禁止将缺失数据伪装成正常值。涉及模拟开仓时，数据质量不合格必须阻断新开仓。

## 4.4 Market Intelligence

确定性计算优先使用 Python，不要求 LLM 重新计算指标。

建议指标：

- EMA 20/50/200
- RSI
- MACD
- ADX
- ATR
- Bollinger Bands
- VWAP（数据条件允许时）
- 成交量均值和相对成交量
- Swing High / Swing Low
- 支撑阻力区域
- 趋势结构（HH/HL、LH/LL）
- 突破、回踩和区间识别
- 波动率状态
- 多周期方向一致性

每个指标或结构结果都应记录计算时间、输入数据范围、算法版本和数据质量。

## 4.5 Derivatives 与 Liquidity Analysis

衍生品分析包括：

- Open Interest 水平和变化率。
- Funding Rate 当前值、历史分位和变化。
- Spot-Futures Basis。
- 价格与 OI 联合变化。
- Funding 与价格背离。
- 可能的多空拥挤迹象。

流动性分析包括：

- Spread。
- Order Book 深度。
- 买卖盘不平衡。
- 大额成交。
- 主动买卖量变化。
- 关键价位附近流动性聚集线索。

Order Book 快照不是完整市场深度事实；采样频率、丢包和聚合方式必须在分析结果中说明。不得把有限盘口快照推断为确定的机构意图。

## 4.6 Market Regime

市场状态分类可从以下标签开始：

- `UPTREND`
- `DOWNTREND`
- `RANGE`
- `HIGH_VOLATILITY`
- `LOW_VOLATILITY`
- `BREAKOUT_ATTEMPT`
- `TREND_REVERSAL`
- `UNCERTAIN`

市场状态应由规则和统计特征初步计算，再由 AI 解释，而不是完全交给 LLM 自由分类。应支持多周期状态不一致，例如 4H 看涨而 15m 看跌。

## 4.7 Multi-Agent Analysis

### Trading Intelligence Agents

| Agent | 职责 |
|---|---|
| Technical Agent | 技术指标、趋势和关键价位 |
| Market Structure Agent | 结构、突破、回踩、支撑阻力 |
| Derivatives Agent | OI、Funding、基差与合约情绪 |
| Liquidity Agent | 成交、盘口、流动性和潜在扫单区域 |
| Market Regime Agent | 市场状态、多周期一致性和波动环境 |
| Event/Risk Agent | 数据异常、重大事件和风险提示 |
| Trading Master Agent | 汇总证据、冲突处理和最终研究结论 |

### Research Intelligence Agents

| Agent | 职责 |
|---|---|
| Historical Research Agent | 检索历史相似行情 |
| Strategy Research Agent | 提出可检验的分析假设 |
| Strategy Review Agent | 分析历史实验表现 |
| Experiment Analysis Agent | 比较模型、Prompt 和策略实验 |

### System Intelligence Agents

| Agent | 职责 |
|---|---|
| Report Agent | 生成日报、周报和研究报告 |
| System Diagnosis Agent | 分析数据管道和系统健康状态 |

### Agent 输出约束

每个 Agent 必须输出结构化结果，至少包含：

- `agent_name`
- `agent_version`
- `model_id`
- `prompt_version`
- `market_snapshot_id`
- `as_of`
- `time_horizon`
- `summary`
- `findings`
- `supporting_evidence`
- `contradicting_evidence`
- `uncertainties`
- `missing_data`
- `confidence`
- `invalidation_conditions`

`confidence` 是模型对本次判断的主观置信度，不等于统计胜率。只有经过历史校准后，才能将概率输出解释为经验概率。

### Agent 协作原则

1. 专业 Agent 独立分析，减少过早互相影响。
2. Trading Master 汇总支持和反对证据。
3. 对重大分歧保留分歧，不强制生成虚假的一致结论。
4. 数据缺失时明确降低结论确定性。
5. 输出 `WAIT` / `NO_CLEAR_SETUP` 是合法结果。
6. 每次运行保存完整输入引用和输出，便于复现。
7. 外部新闻、网页、Skill 和工具返回内容均为不可信数据，不能覆盖系统规则或工具权限。

### 4.7.1 分析报告：主要产品对象（v2.4）

核心流程为：**市场快照 → 专业 Agent 分析 → Master 综合报告 → 可选的预测/机会/计划 → 后续追踪与复盘**。分析报告独立存在，不以产生交易信号为成功条件；`WAIT`、`NO_CLEAR_SETUP` 和证据不足均为合法结论。

每份报告至少包含以下结构化字段，内容 schema 随 `schema_version` 管理：

| 字段 | 内容与约束 |
|---|---|
| `analysis_id` / `symbol` / `market_type` | 报告身份与分析市场 |
| `market_snapshot_id` / `snapshot_as_of` | 输入快照与信息截止时间 |
| `published_at` / `analysis_mode` | 首次对用户可见的时刻；`LIVE`、`BACKFILL` 或 `RETROSPECTIVE` |
| `status` / `degraded` / `missing_data` | 完整、部分、失败状态及缺失信息；规则兜底必须明确标记，不能冒充 AI 综合报告 |
| `summary` / `market_bias` | 简洁的主要判断与适用时间窗口 |
| `dominant_factors` | 当前主导因素、支持理由与解释边界；相关性不能直接表述为因果 |
| `timeframe_views` | 各周期判断、冲突及其对主要结论的影响 |
| `scenarios` | 主要与替代情景、时间窗口、触发条件、失效条件；概率如提供则定义互斥事件并归一化 |
| `evidence` / `counter_evidence` | 引用快照字段或工具结果的证据 ID、值、时间；禁止虚构引用 |
| `disagreements` | Agent 分歧、处理理由及尚未解决的问题 |
| `key_levels` / `watch_conditions` | 关键价位、下一观察条件；可自动追踪的条件采用结构化表达 |
| `changes_since_previous` | 前一可比报告 ID、结论变化及新证据；首次分析明确标记无前序 |
| `uncertainties` / `invalidation_conditions` | 信息局限与推翻判断的条件 |
| `agent_run_ids` / `versions` | Agent、模型、Prompt、特征和 schema 版本引用 |
| `prediction_ids` | 零到多个可检验预测；没有明确预测时为空数组并说明原因 |

分析报告中的情景说明不自动等同于可执行计划。只有满足四层对象各自契约的内容，才登记为第 4.9 节的对象。

### 4.7.2 Agent 的独立贡献与 Master 职责（v2.4）

| Agent | 必须回答的独立问题 | 职责边界 |
|---|---|---|
| Technical | 趋势、动量、波动是否一致，哪里背离？ | 使用确定性指标，不重复计算，不独占结构判断 |
| Market Structure | 关键结构与价位是什么，突破何时成立或失败？ | 区分已确认结构与待确认假设 |
| Derivatives | OI、Funding、Basis 如何印证或反驳价格走势？ | 不由 OI 单独推断多空方向或持仓主体 |
| Liquidity | 成交、盘口和深度是否支持当前走势？ | 标明采样范围，不把挂单推断为确定意图 |
| Market Regime | 当前环境如何影响其他信号的解释？ | 提供多周期背景，不重复罗列技术指标 |
| Event/Risk | 哪些异常、信息缺口或外部因素可能推翻结论？ | 无新闻/宏观来源时明确“未覆盖”，禁止编造事件归因 |
| Master | 如何权衡证据、处理冲突并形成情景判断？ | 必须综合推理，不能仅拼接子报告或多数投票 |

Master 必须区分事实、推断和待验证假设；同源指标或同一行情衍生出的相似观点不能重复计为独立支持。对子 Agent 的关键分歧，给出取舍理由或保留分歧。独立调用不代表统计独立，Agent 数量不能作为置信度依据。

### 4.7.3 AI 分析验收与架构验证（v2.4）

- 内容验收必须覆盖趋势一致、震荡、多周期冲突、衍生品背离、数据缺失、Agent 超时、无明确机会等固定场景。每个场景定义事实、必要证据、必须披露的局限与禁止结论，不要求逐字匹配。
- 程序校验数值、引用、时间、概率和结构化条件；人工抽查综合判断、反证处理、变化解释与可读性。LLM 评审只作辅助，记录评审模型与版本，不能单独决定发布。
- 在相同快照、相同预测时点与窗口上配对比较规则基线、单模型综合分析和多 Agent 分析。规则基线用于测量 AI 增益，不替代主要功能；比较预测表现、内容质量、成本、耗时和失败率。
- 多 Agent 为首版核心方案，各 Agent 是否常驻、何时触发和使用何种模型应由独立贡献与消融结果调整；不以增加调用次数作为功能完成标准。

## 4.8 Market Snapshot Builder

不应把未经整理的大量原始 K 线直接塞给 LLM。由 Python 根据任务生成快照。

建议包含：

- 当前价格和数据时间。
- 多周期 OHLCV 摘要。
- 技术指标。
- 市场结构和关键价位。
- 波动率和成交量状态。
- OI、Funding、Basis。
- 盘口和流动性摘要。
- 市场状态标签。
- 数据质量和缺失项。
- 历史相似情景摘要。
- 与任务相关的既有预测和分析结果。
- 账户和仓位信息（仅执行/风险任务需要）。

每份快照必须记录：

- `snapshot_id`
- `as_of`
- `created_at`
- `source_watermarks`
- `time_range`
- `data_quality`
- `feature_version`
- `schema_version`

禁止在历史预测快照中加入预测时点之后的数据。

## 4.9 Signal Intelligence 与预测生命周期

建议采用四层对象：

1. **Market Bias**：市场总体偏向，如 Bullish、Bearish、Neutral。
2. **Market Opportunity**：潜在机会，不代表已具备入场条件。
3. **Trade Setup**：具体交易计划，包含入场区域和触发条件。
4. **Executable Signal**：条件满足、有效期内且通过风控，可交给模拟执行。

### 信号字段

```json
{
  "signal_id": "sig_example",
  "symbol": "BTCUSDT",
  "market_regime": "UPTREND",
  "timeframe": "1h",
  "direction": "LONG",
  "setup_type": "BREAKOUT_RETEST",
  "confidence": 0.72,
  "supporting_evidence": [],
  "contradicting_evidence": [],
  "entry_zone": {"low": 0, "high": 0},
  "entry_trigger": "string",
  "stop_loss": 0,
  "take_profit_levels": [],
  "invalidation_condition": "string",
  "expected_holding_period": "4h",
  "risk_reward_ratio": 0,
  "market_snapshot_id": "snapshot_id",
  "model_version": "model_version",
  "prompt_version": "prompt_version",
  "created_at": "ISO-8601",
  "expires_at": "ISO-8601"
}
```

数值仅为字段示例，不代表交易建议。

### 生命周期状态

- `CREATED`
- `UNDER_OBSERVATION`
- `TRIGGERED`
- `EXPIRED`
- `INVALIDATED`
- `REJECTED_BY_RISK`
- `SUBMITTED_TO_PAPER`
- `PAPER_OPEN`
- `PAPER_CLOSED`
- `EVALUATED`

状态变化必须有事件记录，不能直接覆盖历史。

### Shadow Tracking

所有 Market Bias、Opportunity、Trade Setup 均进入预测追踪，不论是否触发或成交。评估系统应区分：

- 方向预测是否正确。
- 目标价是否触达。
- 价格区间是否覆盖实际价格。
- 入场条件是否触发。
- 是否通过风控。
- 是否模拟成交。
- 模拟交易最终盈亏。

这样可以避免只统计成交信号造成的选择偏差。

---

# 5. AI 分析质量评估体系

## 5.1 评估原则

AI 分析准确性必须独立于模拟交易盈亏。每个预测都要在预测创建时固定：

- 预测对象。
- 方向或价格区间。
- 预测时间窗口。
- 目标价和失效条件。
- 置信度或概率。
- 基准价格。
- 数据快照。
- 模型、Prompt 和 Agent 版本。

不能在看到后续行情后修改原预测内容。

## 5.2 核心评估指标

> **本表是指标清单。每个指标的数学口径、标签定义、最小样本量、重叠去重规则见第 33 章「评估指标数学口径」**——实现必须以第 33 章为准，本表仅作索引。`evaluator_version` 机制见 33.8。

| 维度 | 指标 | 说明 | 口径章节 |
|---|---|---|---|
| 方向 | Direction Accuracy | 指定窗口涨跌方向命中率 | 33.3 |
| 方向 | Balanced Accuracy | 缓解类别不均衡 | 33.3 |
| 趋势 | Regime Accuracy | 市场状态分类表现 | 33.5 |
| 价格 | MAE / RMSE | 预测价格误差 | 33.5.1 |
| 区间 | Prediction Interval Coverage | 实际价格落入预测区间的比例 | 33.5.2 |
| 目标 | Target Hit Rate | 目标价在期限内触达比例 | 33.5.4 |
| 风险 | Invalidation Accuracy | 失效条件识别表现 | 33.5.5 |
| 机会 | Opportunity Precision | 被标记机会中达到预定义条件的比例 | 33.5.6 |
| 提前量 | Lead Time | 机会出现至行情变化的时间 | 33.5.6 |
| 路径 | MFE / MAE | 最大有利/不利变动 | 33.5.3 |
| 概率 | Brier Score | 概率预测质量 | 33.4.2 |
| 概率 | Calibration | 置信度与实际频率的一致性 | 33.4.3 |
| 稳定性 | Regime-conditioned Metrics | 不同市场状态下的表现 | 33.3 |

方向预测的标签定义必须明确，例如以预测窗口末端收益率和预设阈值判定涨跌，避免把微小波动误判为有效方向。**具体阈值与判定规则见 33.3.1（初始 `θ = 0.30%`，可按资产覆盖，变更须升 `evaluator_version`）。**

## 5.2.1 分析内容质量（v2.4，P0）

预测指标评价可检验产物，以下维度评价分析报告本身；二者分开展示，不以单一总分掩盖事实错误。

| 维度 | 评价内容 | 方法 |
|---|---|---|
| 事实与证据 | 数值、引用、时间与快照一致；无虚构证据 | 确定性校验 + 人工抽查 |
| 推理一致性 | 结论、情景、概率和失效条件不自相矛盾 | 规则检查 + 场景化人工审阅 |
| 综合能力 | 能处理多周期冲突、反证和缺失数据 | 固定案例与人工量表 |
| 信息增量 | 超越指标复述；说明相较上一轮的新变化 | 相同快照的配对盲评；记录平局 |
| 可检验性 | 判断有时间窗口、观察与失败条件 | schema 校验 + 结构化条件检查 |
| 阅读价值 | 结论清晰，用户知道下一步观察什么 | 用户“有用/无用/原因”反馈与阅读任务验收 |

人工量表按维度采用 `0=缺失或错误、1=部分满足、2=充分满足`，保留理由、评审人及版本。事实错误单独计数；核心结论依赖错误证据时不得判为通过。统计事实校验通过率、关键错误数、人工各维度分布和反馈数量；没有反馈不视为有用。

第 15.2 节的 P0 验收须包含 4.7.3 的固定场景及上述检查；没有交易机会的报告同样参加内容质量评价。预测指标未积累足够样本时，不阻断内容与系统功能验收，但必须标记能力尚未被统计验证。

## 5.3 分层报告

至少提供：

- 按资产：BTC、ETH。
- 按周期：15m、1h、4h、24h。
- 按市场状态：趋势、震荡、高波动等。
- 按 Agent。
- 按模型和 Prompt 版本。
- 按预测置信度区间。
- 按分析类型：方向、价格区间、机会、风险。

## 5.4 评估偏差控制

- 防止未来数据泄漏。
- 保留失败、过期、未触发和被拒绝预测。
- 使用滚动时间窗口评估。
- 训练/调参/测试时间区间分离。
- 不得用同一测试区间反复调参后再宣称样本外有效。
- 对重叠预测窗口进行去重或采用适当统计方法。
- 记录手续费、Funding 和滑点假设（涉及交易回测时）。
- 对样本量不足的指标显示 `INSUFFICIENT_SAMPLE`，不输出误导性排名。

---

# 6. 历史研究与回测

## 6.1 历史研究

支持：

- 按时间、资产、市场状态筛选行情。
- 搜索相似技术结构和波动环境。
- 检索类似 OI/Funding 组合。
- 查询历史信号及其后续表现。
- 比较当前市场与历史样本的差异。

相似行情检索结果仅用于研究参考，不代表未来必然重复。

## 6.2 回测边界

回测分为两类：

1. **Analysis Backtest**：验证某种分析规则、方向判断或预测方法。
2. **Trading Strategy Backtest**：验证完整入场、退出、仓位和成本规则。

两类报告必须分开。

回测必须支持：

- 时间区间和数据版本固定。
- 交易费率和 Funding 假设。
- 滑点模型。
- 合约乘数、精度和最小下单量。
- 订单成交假设。
- 资金和风险约束。
- 样本外测试。
- 参数版本和实验记录。

`run_backtest` 只能创建隔离研究实验，不得修改正式模拟账户或发布策略。

---

# 7. 模拟交易与 Freqtrade 集成（辅助验证）

## 7.1 定位

Freqtrade Dry-run 是验证可执行信号的模拟执行后端。它不是 AI 分析引擎，也不是系统的核心数据或研究层。

## 7.2 执行流程

```text
Market Data
    |
Market Intelligence
    |
AI / Rule Analysis
    |
Market Bias / Opportunity / Trade Setup
    |
Signal Lifecycle + Shadow Tracking
    |
Executable Signal
    |
Risk Manager
    |
Paper Trading Adapter
    |
Freqtrade Dry-run
    |
Execution Events
    |
Internal Ledger + Reconciliation
    |
Execution Evaluation
```

未成为 Executable Signal 的分析结果仍然保留在 Analysis Evaluation 中。

## 7.3 组件职责

Freqtrade 负责：

- Dry-run 订单与交易状态。
- 模拟钱包和交易生命周期。
- 其版本支持范围内的订单管理。

本项目负责：

- 风险审批。
- 信号生命周期。
- Paper Trading Adapter。
- 账户隔离。
- 幂等。
- 标准化执行事件。
- 内部账务投影和对账。
- 执行质量评估。
- AI 分析质量评估。

## 7.4 账户隔离

建议设置三个独立 Dry-run 实例：

| 账户 | 用途 | 初始模拟资金 |
|---|---|---:|
| Rules-only | 规则分析信号对照 | 500 USDT |
| AI-only | AI 分析信号对照 | 500 USDT |
| Hybrid | 规则与 AI 综合信号 | 500 USDT |

三个实例应独立使用：

- 配置文件。
- 数据库或状态存储。
- 钱包。
- API 内部端口。
- 订单和仓位状态。

共享行情数据，但不共享账户、订单和持仓。保证软件版本、费率、执行假设尽可能一致。

## 7.5 初始风险参数（实验建议）

| 参数 | 初始建议 |
|---|---:|
| 初始模拟权益 | 500 USDT / 账户 |
| 最大杠杆 | 3x |
| 单笔风险 | 初始权益的 0.5% |
| 日亏损限制 | UTC 日初权益的 1.5% |
| 市场 | Binance USDⓈ-M Futures |
| 标的 | BTCUSDT、ETHUSDT |
| 保证金模式 | Isolated（需 PoC 验证） |

3x 杠杆只影响保证金和名义敞口，不改变单笔风险预算。500 USDT × 3 仅是理论敞口上限参考，不代表必须使用满仓。

基础仓位风险公式：

```text
risk_budget = equity * risk_fraction
quantity = risk_budget / abs(entry_price - stop_price)
```

实际下单前还必须考虑：

- 手续费。
- 预估滑点。
- Funding。
- 合约乘数。
- 数量和价格精度。
- 最小下单量。
- 保证金占用。
- 组合风险和 BTC/ETH 相关性。

## 7.6 Adapter API 草案

内部服务建议提供：

- `GET /api/v1/paper/accounts`
- `GET /api/v1/paper/accounts/{account_id}/status`
- `POST /api/v1/paper/accounts/{account_id}/orders`
- `GET /api/v1/paper/accounts/{account_id}/orders/{order_id}`
- `GET /api/v1/paper/accounts/{account_id}/positions`
- `POST /api/v1/paper/accounts/{account_id}/positions/{position_id}/close`
- `POST /api/v1/paper/accounts/{account_id}/pause`
- `POST /api/v1/paper/accounts/{account_id}/resume`
- `GET /api/v1/paper/accounts/{account_id}/events`
- `POST /api/v1/paper/reconciliation/run`

API 是本项目内部接口，不等同于 Freqtrade 原生 API。Freqtrade 端点和请求字段必须按固定版本的 OpenAPI 文档适配。

## 7.7 安全边界

- 仅 Paper Trading Adapter 可以访问 Freqtrade REST API。
- Agent、Skill、MCP 和 Dashboard 不得直接访问交易 API。
- Freqtrade API 只暴露在 Docker 内部网络。
- 不配置真实交易凭证。
- 启动时强制校验 `dry_run=true`，否则拒绝启动。
- 不允许真实下单、提现或资金转移。
- 固定 Freqtrade 镜像版本，不使用不受控的 `latest`。
- 风控服务不可用时拒绝新开仓。
- 数据陈旧、行情断线、账务不一致时拒绝新开仓。
- 暂停新开仓不应停止已有模拟仓位的退出管理。

## 7.8 幂等、恢复和对账

- 每个提交请求必须有唯一 `idempotency_key`。
- 重复请求返回原请求结果，不重复开仓。
- 请求超时后先查询 Freqtrade 状态，不得盲目重试。
- 启动恢复时先同步订单、交易和余额。
- 内部账务采用事件追加方式，不覆盖历史。
- 未解释的对账差异必须阻断新开仓。
- 本地休眠或关机后，先补齐行情、检查数据质量、同步账户状态和对账，再恢复新开仓。

## 7.9 模拟交易局限

Dry-run 不等于真实撮合，也不等于交易所 Demo 环境。必须通过 PoC 验证：

- 市价单模拟成交逻辑。
- 限价单成交假设。
- 滑点模型和上限。
- 部分成交支持情况。
- Funding 处理。
- 止损、止盈实际触发方式。
- Futures 合约与杠杆配置。
- 强制平仓和保证金逻辑。

若 Freqtrade 未覆盖某些 Funding 或执行成本，可在内部账务/评估层补算，但必须防止重复计费。

---

# 8. Dashboard 与交互设计

## 8.1 首页布局

首页以行情和 AI 分析为主，不以账户余额和 PnL 为主。

建议顺序：

1. **AI Market Analysis**：主要判断、相较上一轮的变化、主要/替代情景、下一观察条件及失效条件；证据和 Agent 观点可展开。
2. **Market Overview**：BTC/ETH 价格、多周期变化、市场状态，为 AI 结论提供上下文。
3. **Market Structure**：趋势结构、支撑阻力、突破和回踩。
4. **Trading Opportunities**：机会列表、条件、风险和失效状态。
5. **Prediction Tracking**：历史预测与真实行情对照。
6. **AI Performance**：Agent、模型和 Prompt 的评估结果。
7. **Research Lab**：历史研究和实验。
8. **Paper Trading**：模拟账户、订单、持仓和收益。

## 8.2 分析详情页

展示：

- 资产和时间周期。
- 市场快照时间。
- 当前市场状态。
- 综合结论。
- 相较前一可比报告的变化及原因。
- 主要/替代情景、下一观察条件与适用时间窗口。
- 各 Agent 分析及 Master 对关键分歧的处理。
- 可定位到快照字段或工具结果的支持证据和反对证据。
- 分析模式（实时/补跑/事后研究）、发布时间及数据截止时间。
- “有用/无用/原因”反馈入口，反馈不修改原始报告。
- 不确定性和数据缺失。
- 预测时间窗口。
- 关键价位和触发条件。
- 失效条件。
- 后续实际行情和评估结果。
- 模型、Prompt 和数据版本。

## 8.3 研究实验页

支持比较：

- Rules-only、AI-only、Hybrid。
- 不同模型。
- 不同 Prompt。
- 不同 Agent 组合。
- 不同市场周期和市场状态。
- 样本内与样本外结果。

## 8.4 模拟交易页

只展示辅助验证信息：

- Dry-run 账户权益。
- 模拟订单、成交和持仓。
- 手续费、Funding、滑点。
- 模拟 PnL 和回撤。
- 执行失败、未成交和对账差异。

---

# 9. 技术架构

## 9.1 架构原则

采用模块化单体（Modular Monolith）起步，不早期微服务化。各模块通过明确接口和领域事件解耦，后续根据实际负载拆分。

## 9.2 技术选型建议

| 层 | 技术 |
|---|---|
| Backend | Python、FastAPI |
| Frontend | Next.js、TypeScript |
| Charts | TradingView Lightweight Charts、Apache ECharts |
| Database | PostgreSQL |
| Cache / Queue | Redis |
| Scheduling | APScheduler（早期）；后续 Celery 或 RQ |
| AI Orchestration | LangGraph |
| LLM | 云端 API，经统一 Gateway |
| Schema Validation | Pydantic |
| Market Data | Binance REST / WebSocket |
| Paper Trading | Freqtrade Dry-run |
| Deployment | Docker Compose |
| Observability | Structured Logging、Metrics、Health Checks |

## 9.3 逻辑架构

```text
                  +-----------------------+
                  |      Next.js UI       |
                  +-----------+-----------+
                              |
                         FastAPI
                              |
       +----------------------+----------------------+
       |                      |                      |
 Market Research        AI Orchestration        Evaluation
       |                      |                      |
       +----------------------+----------------------+
                              |
                    Market Snapshot Builder
                              |
       +----------------------+----------------------+
       |                      |                      |
 Market Data Engine   Market Intelligence     Historical Research
       |                      |                      |
       +----------------------+----------------------+
                              |
                    PostgreSQL / Redis
                              |
                 Binance REST / WebSocket

Optional Validation Path:
Signal Lifecycle -> Risk Manager -> Paper Adapter
                 -> Freqtrade Dry-run
                 -> Execution Events / Ledger
                 -> Execution Evaluation
```

## 9.4 主要后端模块

- `market_data`
- `data_quality`
- `market_features`
- `market_structure`
- `market_regime`
- `derivatives`
- `liquidity`
- `snapshot`
- `ai_gateway`
- `agents`
- `anomaly`
- `scheduler`
- `research`
- `signals`
- `prediction_tracking`
- `analysis_evaluation`
- `backtesting`
- `strategy_lab`
- `risk`
- `paper_trading`
- `freqtrade_adapter`
- `ledger`
- `reconciliation`
- `reporting`
- `notifications`
- `monitoring`
- `audit`

---

# 10. AI Gateway、Skills 与 Tools

## 10.1 LLM Gateway

统一管理：

- 模型路由。
- Prompt 版本。
- 结构化输出。
- Token 和成本记录。
- 超时、重试和限流。
- 响应校验。
- 调用日志和追踪。
- 模型可用性和降级策略。

不应每根 1m K 线都调用 LLM。优先采用事件驱动：

- 每小时市场状态变化。
- 15m 关键结构变化。
- OI/Funding 异动。
- 新机会发现。
- 重要数据质量事件。
- 预测到期评估。
- 模拟仓位重大变化。
- 日报和研究复盘。

## 10.2 LLM 与 Python 职责边界

Python 负责：

- 指标计算。
- 数据清洗和质量检测。
- 市场结构特征。
- 仓位和风险计算。
- 订单状态和账务。
- 统计评估。
- 回测执行。

LLM 负责：

- 上下文理解。
- 多源证据综合。
- 市场情景解释。
- 研究假设提出。
- 机会描述。
- 分歧分析。
- 研究报告和复盘。

LLM 不得成为风控、账户账务或订单状态的权威来源。

## 10.3 Skills

建议初始 Skills：

- `market-overview`
- `technical-analysis`
- `market-structure`
- `derivatives-analysis`
- `liquidity-analysis`
- `market-regime-analysis`
- `trade-opportunity-research`
- `historical-research`
- `prediction-review`
- `strategy-research`
- `daily-market-report`

Skill 描述任务流程和输出规范；Tool 才是执行实际数据访问或计算的函数。

## 10.4 Tools

建议工具：

- `get_klines`
- `get_ticker`
- `get_orderbook`
- `get_recent_trades`
- `get_open_interest`
- `get_funding_rate`
- `get_market_indicators`
- `get_market_structure`
- `get_market_regime`
- `get_market_snapshot`
- `search_historical_patterns`
- `get_prediction_history`
- `get_agent_performance`
- `run_analysis_backtest`
- `run_strategy_backtest`

模拟交易工具（仅 Paper Adapter 内部服务可用）：

- `submit_paper_order`
- `get_paper_order`
- `get_paper_positions`
- `close_paper_position`
- `pause_paper_account`
- `run_paper_reconciliation`

## 10.5 Tool Gateway

统一负责：

- 权限检查。
- 参数校验。
- 数据范围限制。
- 限流和缓存。
- 超时与重试。
- 审计日志。
- 返回值标准化。
- 敏感操作隔离。

Agent 不应获得任意 Python 执行、任意 SQL 或任意网络访问权限。

---

# 11. 数据库设计

以下为逻辑表清单。**字段、索引和约束的规格见第 30 章「数据契约」**——本章是分类视图，30 章是唯一真源；第 30.9 节给出合并后的完整表清单。

## 11.1 市场数据

| 表 | 用途 |
|---|---|
| `market_candles` | OHLCV K 线 |
| `market_trades` | 成交数据或聚合成交 |
| `orderbook_snapshots` | 盘口快照 |
| `market_tickers` | 行情快照 |
| `funding_history` | Funding 历史 |
| `open_interest_history` | OI 历史 |
| `instrument_metadata` | 合约规格和精度 |
| `market_data_quality_events` | 数据质量事件 |
| `market_data_sync_runs` | 数据同步任务记录 |

建议 K 线唯一键：`(exchange, market_type, symbol, timeframe, open_time)`。

## 11.2 特征与快照

| 表 | 用途 |
|---|---|
| `market_metrics` | 指标计算结果 |
| `market_regimes` | 市场状态 |
| `market_structure_features` | 结构特征 |
| `sr_zones` | 支撑阻力区域 |
| `market_snapshots` | 分析快照 |
| `snapshot_data_refs` | 快照引用的数据范围 |

## 11.3 AI 与研究

| 表 | 用途 |
|---|---|
| `agent_runs` | Agent 运行记录 |
| `agent_outputs` | Agent 结构化输出 |
| `model_runs` | LLM 调用记录 |
| `prompt_versions` | Prompt 版本 |
| `agent_versions` | Agent 配置版本 |
| `research_reports` | 研究报告 |
| `historical_pattern_matches` | 历史相似情景 |
| `strategy_experiments` | 实验定义 |
| `experiment_results` | 实验结果 |

## 11.4 信号与预测

| 表 | 用途 |
|---|---|
| `signals` | Market Bias / Opportunity / Setup / Executable Signal |
| `signal_events` | 生命周期事件 |
| `signal_snapshots` | 信号创建时的证据快照 |
| `prediction_observations` | 后续价格观察 |
| `prediction_evaluations` | 预测评估结果 |
| `analysis_evaluation_runs` | 评估批次 |
| `evaluation_results` | 指标结果 |

## 11.5 模拟交易（P1）

| 表 | 用途 |
|---|---|
| `paper_platform_accounts` | Freqtrade 实例映射 |
| `paper_platform_configs` | 模拟账户配置版本 |
| `paper_submission_requests` | 提交请求和幂等键 |
| `paper_platform_orders` | 外部订单映射 |
| `paper_platform_trades` | 外部交易映射 |
| `execution_events` | 标准化执行事件 |
| `paper_ledger_entries` | 内部账务分录 |
| `paper_reconciliation_runs` | 对账批次 |
| `paper_reconciliation_diffs` | 对账差异 |
| `paper_instance_health` | 实例健康状态 |
| `paper_account_controls` | 暂停、恢复和风险控制 |

## 11.6 系统与审计

- `audit_logs`
- `system_events`
- `tool_invocations`
- `llm_cost_records`
- `config_versions`
- `notification_events`
- `scheduled_jobs`

## 11.7 数据建模原则

- 所有核心实体使用稳定 ID。
- 时间统一存 UTC，展示层转换本地时区。
- 价格、数量和资金使用 Decimal，避免浮点账务误差。
- 原始数据与派生特征分离。
- 重要分析输出不可变更；修订应产生新版本。
- 所有分析引用 `market_snapshot_id`。
- 所有模型输出引用模型和 Prompt 版本。
- 大体量行情表按时间分区可作为后续优化。
- 对关键查询建立复合索引。

---

# 12. API 设计草案

> **本章程节为端点概览。完整的 API 契约（响应结构、分页、幂等、错误码字典、鉴权要求、完整端点目录）见第 31 章。** 第 22.8 / 23.3 的新增端点已合并进 31.7 的目录，本章不再重复维护。

## 12.1 行情

- `GET /api/v1/markets`
- `GET /api/v1/markets/{symbol}/candles`
- `GET /api/v1/markets/{symbol}/overview`
- `GET /api/v1/markets/{symbol}/indicators`
- `GET /api/v1/markets/{symbol}/structure`
- `GET /api/v1/markets/{symbol}/derivatives`

## 12.2 AI 分析

- `POST /api/v1/analysis/runs`
- `GET /api/v1/analysis/runs/{run_id}`
- `GET /api/v1/analysis/latest`
- `GET /api/v1/analysis/{analysis_id}`
- `GET /api/v1/agents/performance`

## 12.3 研究与预测

- `GET /api/v1/research/patterns`
- `POST /api/v1/research/experiments`
- `GET /api/v1/predictions`
- `GET /api/v1/predictions/{prediction_id}`
- `GET /api/v1/evaluations/summary`
- `GET /api/v1/evaluations/by-agent`
- `POST /api/v1/evaluations/run`

## 12.4 回测

- `POST /api/v1/backtests`
- `GET /api/v1/backtests/{backtest_id}`
- `GET /api/v1/backtests/{backtest_id}/results`

## 12.5 API 约定

- 使用 Pydantic 校验请求和响应。
- 所有写操作记录审计日志。
- 长任务返回 `run_id`，通过查询或 WebSocket 获取状态。
- 支持分页、时间范围和明确的时区语义。
- 错误响应包含稳定错误码。
- 不将 LLM 自由文本作为内部 API 的唯一结构。

---

# 13. 非功能需求

## 13.1 可靠性

- 行情采集断线后自动重连。
- 使用 REST 补齐 WebSocket 缺口。
- 后台任务可恢复，避免重复写入。
- 数据库备份和恢复流程可验证。
- AI 服务故障不影响基础行情采集。

## 13.2 可观测性

至少监控：

- 数据采集延迟。
- K 线缺口数量。
- WebSocket 连接状态。
- 数据质量事件。
- Agent 成功率和耗时。
- LLM Token 和费用。
- 预测评估积压量。
- 数据库连接和任务队列。
- Freqtrade 实例健康状态（P1）。

## 13.3 性能

初期以个人本地使用为目标，不设不必要的高并发指标。优先保证：

- 常用 Dashboard 查询可接受。
- 历史数据查询有时间范围和分页。
- AI 任务异步执行。
- 大批量评估后台执行。
- 计算结果可缓存并标明版本。

## 13.4 安全

> **本节为原则清单。机制定义（威胁模型、鉴权方案、网络暴露边界、工具权限矩阵、密钥轮换）见第 34 章。**

- API 密钥通过环境变量或本地 Secret 管理，不写入仓库。
- 本地服务默认不暴露公网。（**机制见 34.5；企微可信 IP 验证的一次性公网入口见 34.6**）
- Dashboard 与内部服务使用鉴权。（**机制见 34.3–34.4**）
- LLM 输入不包含交易所密钥。
- Agent 工具权限最小化。（**权限矩阵见 34.8**）
- 所有模拟交易请求经过 Risk Manager。
- 禁止真实交易凭证和真实下单权限。（**启动时校验见 34.2**）

---

# 13.5 可观测性与性能（v2.3 交叉引用）

- 指标清单见第 37.2 节。
- 目标值与硬上限见第 37.3 节。
- 量化成功指标见第 37.4 节。

---

# 14. 部署方案

## 14.1 Docker Compose 服务

建议服务：

- `frontend`
- `backend`
- `market-data-worker`
- `analysis-worker`
- `evaluation-worker`
- `scheduler`
- `postgres`
- `redis`
- `freqtrade-rules`（P1）
- `freqtrade-ai`（P1）
- `freqtrade-hybrid`（P1）

早期可以将 Worker 合并到 Backend 进程，随着负载再拆分。

## 14.2 本地持久化

- PostgreSQL 数据卷。
- Freqtrade 各实例独立数据卷。
- 配置文件版本化。
- 日志按大小或时间轮转。
- 定期数据库备份。
- 提供一键启动、停止、升级和恢复说明。

## 14.3 本地运行限制

本地电脑关机、休眠或网络中断期间，系统无法持续采集和评估。恢复时必须执行数据补齐、质量检查和状态同步。不得把缺失时段默认为无事件。

---

# 15. 测试与验收标准

> **验收项到自动化测试的映射规则见第 36 章（36.3 必测清单、36.4 CI 流水线）。** 下列条目即测试用例的来源，每条都应能追溯到一个自动化测试或一次人工确认。

## 15.1 P0：行情与数据

- BTC、ETH 指定周期 K 线可持续采集。
- REST 与 WebSocket 数据能正确合并。
- 断线后能补齐缺口。
- 重复 K 线不会产生重复主记录。
- 异常数据会产生质量事件。
- 所有行情记录可追溯来源和时间。

## 15.2 P0：AI 分析

- 每次分析绑定快照、模型和 Prompt 版本。
- Agent 输出符合结构化 Schema。
- 支持证据、反对证据和不确定性字段完整。
- 数据缺失会在输出中显式呈现。
- Agent 分歧可被保留。
- 模型服务失败时任务进入可重试或失败状态，不伪造成功结果。

## 15.3 P0：预测追踪与评估

- 所有预测均可追踪到期。
- 未触发和未成交信号仍进入评估。
- 预测创建后不可被后续行情修改。
- 评估过程无未来数据泄漏。
- 指标可按资产、周期、Agent、模型和 Prompt 分组。
- 样本不足时明确标注，不产生误导性排名。

## 15.4 P1：Freqtrade PoC

在扩展到三个实例前，先完成单实例 PoC：

1. 固定 Freqtrade 版本。
2. 确认 `dry_run=true`。
3. 验证 Binance Futures 配置兼容。
4. 验证 BTC、ETH 合约标的。
5. 验证 Long 和 Short 模拟流程。
6. 验证杠杆限制。
7. 验证仓位和风险预算。
8. 验证止损、止盈真实触发行为。
9. 验证幂等请求不会重复开仓。
10. 验证订单状态映射。
11. 验证异常重启恢复。
12. 验证对账和差异阻断。
13. 验证日亏损限制。
14. 验证 API 超时不会盲目重试。
15. 验证数据中断时禁止新增仓位。

Freqtrade 的具体配置、REST API 字段、Futures 行为和止损/止盈能力必须按锁定版本官方文档与实际 PoC 确认，不得仅凭示例配置推断。

## 15.5 安全验收

- 不存在真实交易密钥。
- Freqtrade API 不暴露公网。
- Agent 和 Dashboard 无法直接调用 Freqtrade。
- 非 Dry-run 配置启动失败。
- 风控服务不可用时拒绝新开仓。
- 对账差异未解决时拒绝新开仓。
- 同一幂等键重复提交不会产生额外交易。

## 15.6 P0：异动检测（v2.1 新增）

- 人工构造的涨跌幅异动能被规则命中，规则 ID 与触发阈值被记录。
- 波动率、成交量、OI、Funding 异动均可被检出。
- 分级正确：同一价格变动在阈值内为 WARN、越界为 CRITICAL。
- 异动判定不调用 LLM（可通过断网/禁用 LLM 验证仍能检出）。
- 同一异动在冷却期内只产生一次通知。
- 异动后 1h / 4h 的实际市场表现被记录（`anomaly_evaluations`），可统计各规则有效率。

## 15.7 P0：通知推送（v2.1 新增）

- `POST /api/v1/notifications/test?channel=feishu` 能在 5 秒内送达一张测试卡片到飞书。
- `POST /api/v1/notifications/test` 缺少 `channel` 参数时返回 `400`，错误码 `NOTIFY_CHANNEL_REQUIRED`（v2.3 新增）。
- 测试消息绕过账户级配额与冷却，但写入 `notification_events` 且 `is_test=true`（v2.3 新增）。
- 启用签名校验时，带 `sign` 与 `timestamp` 的请求被飞书接受。
- 飞书返回非 0 业务码时判定为失败，进入重试；重试耗尽后进入死信队列并在 Dashboard 可见。
- 推送不阻塞采集与分析主链路（推送延迟不影响行情入库）。
- 卡片内容超长时按优先级截断，且详情链接始终保留。
- 账户级每日配额生效，超出后仅 CRITICAL 继续推送。
- 免打扰时段生效，CRITICAL 穿透行为符合配置。
- 同一异动的 `revision=0`（确定性快报）与 `revision=1`（AI 解读）**都能送达**，二者不被去重层拦截（v2.3 新增，回归 22.4.4.1）。
- 同一 `(fingerprint, revision)` 在 TTL 内只投递一次（v2.3 新增）。
- 所有推送在 `notification_events` 中有完整投递记录，含 `revision`、`revision_reason`、`channel`、`delivery_status`。

## 15.7.1 P1：企业微信渠道与微信可达（v2.2 新增）

- `POST /api/v1/notifications/test?channel=wecom_app` 能在 5 秒内送达一条消息到指定成员；`?recipient=userid` 可覆盖接收人（v2.3 新增）。
- **个人微信收到消息**：成员已用微信关注微信插件、关闭「仅在企业微信中接收消息」、微信内开启「接收应用消息」后，同一条消息在微信插件会话**可见完整正文**。这是路径 A 的**核心验收项**，不可用 API 自检替代。
- 企微应用渠道默认使用 `msgtype=text`，微信端与企微端**均**能读到完整正文；使用 `markdown` 时企微端正常、微信端显示「暂不支持此消息类型」，系统应在自检页明确警告并建议改回 `text`。
- 企微 `text` 消息中的 `<a href="短链">详情</a>` 在微信端**可点击跳转**到 Dashboard 详情页。
- 应用开启「在微信插件中始终进入主页」时，微信端仅能收到 20 字节；系统必须在自检页标注该风险项（无法自动探测）。
- `errcode 42001`（token 过期）时系统自动刷新 token 并重试成功，不产生死信。
- `errcode 60020` 判定为不可重试的**配置错误**，错误消息中包含企微返回的 `from ip`，Dashboard 明确提示「需配置企业可信 IP」。
- `errcode 81013` 判定为不可重试，提示「接收人不在应用可见范围内」。
- 部分接收人无效时返回 `PARTIAL` 状态，有效接收人确实收到消息，无效名单被记录。
- `errcode 43004` 给出可操作的提示：「成员需用微信扫码关注微信插件，并在微信内开启『接收应用消息』」。
- 企微群机器人通道：20 条/分钟限流生效，超限返回 `RATE_LIMITED` 并退避重试，不丢消息。
- 限流状态按渠道独立维护：企微限流不影响飞书投递，反之亦然。
- 用户级每日配额（60 条）在**所有渠道合计**生效，不是每渠道各 60 条。
- `NOTIFY_DELIVERY_MODE=first_success` 时，第二个渠道不会收到同一条消息。
- `access_token` 在多 worker 下只换取一次（共享缓存），不触发 `gettoken` 频率限制。
- 所有渠道的凭据（Webhook URL、CorpSecret、Bot Key）在 Dashboard 与日志中均已脱敏。
- `POST /api/v1/notifications/channels/wecom_app/precheck` 返回逐项自检结果，其中「成员是否关注微信插件」「微信端消息开关」「是否开启始终进入主页」明确标记为**需人工确认**。

## 15.8 P0：调度与定时分析（v2.1 新增）

- 各定时任务按 cron 触发，连续观察 ≥3 天无漏跑。
- 同一幂等键重复触发不产生重复分析记录。
- 关机后恢复，错过的分析任务被补跑并标记 `is_backfill=true`。
- 超出补跑上限的时间窗在日报中以 `MISSED` 列出，不被静默丢弃。
- `analysis_1h` 在结论无实质变化时不推送。
- 依赖任务失败时，依赖任务被跳过并记录 `SKIPPED_DEPENDENCY`。
- 连续失败达阈值的任务触发 `SYSTEM_ALERT` 推送。

## 15.9 P0：双轨评估（v2.1 新增）

- 每笔模拟成交可追溯到具体的 Agent 输出与证据条目。
- 轨道 A 指标与轨道 B 指标在报告中并排展示。
- 成本假设敏感度在滑点 ×0.5 / ×1 / ×2 三档下均有输出。
- Funding 补算不重复计费（同 `funding_accrual_id` 只入账一次）。
- 样本量不足的分组显示 `INSUFFICIENT_SAMPLE`。

## 15.10 P0：工程化底座（v2.3 新增）

验收明细见各新章末尾的「验收标准」小节（29.10、34.10、38.6、36.3）。此处列出进入 MVP 判定的关键项。

### 15.10.1 行情采集（第 29 章）

- [ ] WS 断开重连后先 REST 对账再写 WS 数据，缺口被完整补齐。
- [ ] 未收盘 K 线不落库（`market_candles` 中 `close_time > now()` 的行数恒为 0）。
- [ ] 已收盘 K 线被重复推送时主记录不增加。
- [ ] WS 与 REST 冲突时以 REST 为准并产生 `DATA_SOURCE_DIVERGENCE`。
- [ ] 半根 1m K 线上的极端波动不触发 `PRICE_PCT_1M`。
- [ ] 现货与永续数据互不覆盖；基差仅在两侧均有效时计算。

### 15.10.2 数据与 API 契约（第 30、31 章）

- [ ] 核心表按第 30 章落地，唯一键与索引齐备。
- [ ] `alembic upgrade head` → `downgrade -1` → `upgrade head` 往返成功。
- [ ] API 响应符合第 31 章的统一封装；错误响应携带已登记的错误码。
- [ ] 列表端点支持游标分页，翻页过程中不重复、不漏项。
- [ ] `Idempotency-Key` 重复提交返回首次结果，不产生第二次副作用。

### 15.10.3 配置（第 32 章）

- [ ] `.env.example` 覆盖全部配置项；CI 校验配置目录与 Settings 一致。
- [ ] 敏感项在日志、Dashboard、错误堆栈中均已脱敏。
- [ ] 缺少必需项时进程拒绝启动并指出缺失键名。

### 15.10.4 评估口径（第 33 章）

- [ ] 方向标签阈值 `θ` 已确定并写入 `evaluator_version`。
- [ ] 基准价 `P0` 取自信号所属 timeframe 的最后一根**已收盘** K 线，且落库后不可变。
- [ ] 样本不足时输出 `INSUFFICIENT_SAMPLE` 且**不输出比率数值**；排名排除样本不足组。
- [ ] 区间覆盖率与区间宽度并排输出。
- [ ] 无 `probability` 时 Brier 为 `NULL` 且报告 Brier 覆盖率。
- [ ] 方向与区间比率同时输出去重前后两个值。
- [ ] 评估过程无未来数据泄漏（自动测试覆盖）。

### 15.10.5 鉴权与边界（第 34 章）

- [ ] 非回环绑定且无令牌时进程拒绝启动。
- [ ] Docker Compose 所有端口显式绑定 `127.0.0.1:`。
- [ ] `/api/v1/**` 无令牌返回 401；探针与 `/metrics` 不鉴权但仅本机可达。
- [ ] Tool Gateway 拒绝 Agent 调用 L4/L5 工具并记录 `DENIED`。
- [ ] `public-callback` 默认不启动，启动后仅响应带 token 的路径，关闭后端口不再监听。

### 15.10.6 一致性（第 38 章）

- [ ] 同一输入下线上路径与回测路径的特征输出逐值相等。
- [ ] 回测结果记录 `feature_version`；版本不同时标注「口径不可比」。
- [ ] 预热窗口不足时回测拒绝启动并说明缺口。
- [ ] 回测不产生 `signals`、`prediction_evaluations` 或 `notification_events`。

### 15.10.7 前端与可观测性（第 35、37 章）

- [ ] 前端 6 种状态（LOADING/READY/EMPTY/STALE/DEGRADED/ERROR）均有渲染。
- [ ] `MISSING` 数据不渲染为 0 或旧值。
- [ ] 评估页双轨并排、样本不足组显示 `—`、口径版本可见。
- [ ] `/metrics` 暴露第 37.2 节的指标；关键 SLO 超出硬上限时触发 `SYSTEM_ALERT`。

---

# 16. 研发路线图

个人开发者的粗略估算，实际时间取决于数据范围、AI 分析深度和可用时间。以下为阶段顺序，不是交付承诺。

| 阶段 | 主要工作 | 预期产出 | 最小周数 |
|---|---|---|---:|
| Phase 0 | 项目骨架、Docker、数据库、配置 | 可运行基础环境 | 0.5 |
| Phase 1 | Binance 数据采集、历史补数、数据质量 | 可靠行情数据层 | 1 |
| Phase 2 | 指标、结构、衍生品、市场状态 | Market Intelligence | 1 |
| **Phase 2.5** | **异动检测规则引擎 + 分级** | **可识别异动的确定性层** | 0.5 |
| Phase 3 | Snapshot、LLM Gateway、专业 Agent 与 Master、内容质量场景验收 | 可手动生成的 AI 综合报告 | 1.5 |
| Phase 3A | 定时/事件触发、调度中心、一个主要通知渠道 | 每天自动产出且可触达的 AI 分析 | 1 |
| Phase 3B（P1） | 第二通知渠道、企业微信扩展 | 按实际需求增强触达 | 0.5 |
| Phase 4 | 信号生命周期、预测追踪 | 可持续追踪的预测记录 | 1 |
| Phase 5 | 内容质量、预测评估、规则/单模型/多 Agent 配对验证 | AI 分析质量与增量价值报告 | 1.5 |
| Phase 6 | 完善分析阅读、历史复盘和基础筛选 | 核心分析工作台 | 1 |
| Phase 6A（P1） | 完整实验比较、相似行情、回测、经济性轨道 | 研究与经济性验证扩展 | 2 |
| Phase 7 | Freqtrade 单实例 PoC、Adapter | 模拟执行验证 | 1 |
| Phase 8 | 三账户隔离、执行评估和健康监控 | 完整辅助验证环境 | 1 |

**研发优先级原则：Phase 1–6 是产品核心；Phase 7–8 是辅助验证。**

> **v2.4 修订**：阶段号表示依赖顺序。Phase 3 先完成可阅读的 AI 综合分析，Phase 3A 接入定时、异动与一个主要渠道；Phase 3B 为可选扩展。分析详情与基本阅读界面随 Phase 3 交付，Phase 6 负责完善，不能到最后才让用户看到分析。第 33 章评估契约在 Phase 3 登记首批预测前定稿。

**v2.1 调整说明：**

- 异动规则与通知适配不依赖 LLM，可以独立开发；首版产品验收以 AI 分析及其日常使用流程为中心。
- Phase 3A 将定时与重要异动接入同一分析流程；LLM 故障时规则快报保持可用，但必须标明 AI 分析未完成。
- Freqtrade 相关阶段（7–8）严格排在评估体系之后：没有可信的评估基准时，模拟盈亏没有解释价值。

**v2.3 补充：契约层章节落在哪个阶段**

v2.3 新增的第 29–38 章不是独立阶段，而是**嵌入既有阶段**：

| 契约章 | 主要落在 | 说明 |
|---|---|---|
| 29 行情采集层 | Phase 1 | 与采集器同步实现；29.5 未收盘 K 线约定是 Phase 1 必须先定的决策 |
| 30 数据契约 | Phase 0–1 | 核心表在 Phase 0 建，Phase 1 补齐 |
| 31 API 契约 | Phase 0–6 | 随各阶段端点增量落地；错误码字典 Phase 0 定稿 |
| 32 配置体系 | Phase 0 | 配置必须在骨架阶段就位，否则后续每个模块都会自带配置 |
| 33 评估口径 | Phase 3–5 | **必须在 Phase 3 登记首批预测前定稿**，Phase 4–5 实现追踪与统计 |
| 34 鉴权 | Phase 0 | 默认开启即需在骨架阶段实现 |
| 35 前端 | Phase 3、6 | Phase 3 交付分析详情与基本阅读，Phase 6 完善；35.5 影响 Phase 1 起的接口设计 |
| 36 测试与工程 | Phase 0 | CI 与测试骨架在 Phase 0 建立；36.3 的清单随各阶段补充 |
| 37 可观测性 | Phase 0–1 | 指标从第一天就要埋，否则 Phase 1 结束后无法回溯 |
| 38 回测一致性 | Phase 5 | 在回测引擎开工前落地 |

> **关键排序提醒**：第 33 章（评估口径）必须早于第 4 章（预测追踪）完成。若先写追踪、后定口径，早期积累的预测记录将无法用于评估——这是不可逆的数据债。

**里程碑式检查点：**

| 检查点 | 时点 | 判定标准 |
|---|---|---|
| M1 数据可信 | Phase 1 末 | 断线补数后 K 线无缺口，重复推送不产生重复记录 |
| M2 AI 分析可读可信 | Phase 3 末 | 手动生成完整报告，通过 4.7.3 的事实、反证、冲突和缺失数据场景验收 |
| M2.5 主要渠道可达 | Phase 3A 末 | 收到分析摘要并可打开对应详情；移动阅读启用时在手机实测 |
| M3 每天有结论 | Phase 3A 末 | 连续 3 天定时分析按时产出，展示结论变化与昨日复盘 |
| M4 判断可被检验 | Phase 4 末 | 每条结论都能在到期后被自动判定对错 |
| M5 能检验 AI 增量价值 | Phase 5 末 | 输出配对对照、内容质量和预测表现；样本不足时明确尚不能判定优劣 |
| M6 有模拟验证环境 | Phase 8 末 | 三账户独立运行，对账无未解释差异 |

---

# 17. 关键风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 行情数据缺失或延迟 | 分析错误 | Data Quality、补数、数据状态标记 |
| LLM 幻觉 | 错误解释 | 结构化输出、证据引用、确定性计算 |
| 未来数据泄漏 | 评估虚高 | 快照冻结、时间隔离、审计 |
| 只评估成交信号 | 选择偏差 | 全量 Shadow Tracking |
| 过度调参 | 样本外失效 | Walk-forward、独立测试集 |
| 模型或 Prompt 漂移 | 分析质量退化 | 版本记录、分组评估 |
| 过度依赖单一 Agent | 系统性偏差 | 多 Agent 独立分析和反证 |
| 模拟成交不真实 | 交易结论失真 | 明确 Dry-run 假设、单独评估执行质量 |
| 本地服务中断 | 数据和状态缺口 | 恢复补数、状态同步和对账 |
| 工具权限过大 | 安全风险 | Tool Gateway、最小权限、审计 |
| 通知疲劳，用户屏蔽推送 | 系统失去价值 | 去重、冷却、聚合、配额、免打扰、无变化不推送 |
| 异动规则过于敏感 | 大量误报 | 规则版本化、异动后表现评估、按有效率调参 |
| 异动规则过于迟钝 | 漏报关键行情 | 定期回溯评估、对 CRITICAL 规则做单独复盘 |
| LLM 不可用导致分析缺失 | 判断质量下降 | 异动检测不依赖 LLM、确定性降级卡片、多档模型降级 |
| LLM 成本超预期 | 长期不可持续 | 三档模型分层、日预算硬上限、降级顺序明确 |
| 定时任务因关机漏跑 | 分析断档 | 幂等、补跑、`MISSED` 显式记录 |
| 补跑触发重复推送 | 再次造成轰炸 | 补跑标记为 `backfill`，不占实时配额 |
| 双轨结论背离未被发现 | 归因错误 | 并排展示两轨，单独盯「判断对但亏钱」类 |
| 单数据源故障或异常 | 分析错误 | 双源交叉校验或多接口一致性检查 |
| Binance 限流触发 429 | 采集中断 | 统一 RateLimiter、分页补数、退避不并发重试 |
| 盘口原始数据占满磁盘 | 系统不可用 | top20 + 低频快照 + 派生特征长期保留 |
| **未收盘 K 线被误用（v2.3）** | 异动误报、重复推送 | 第 29.5 节「仅已收盘 K 线」硬约束；落库层物理隔离；自动化测试（36.3） |
| **回测与实盘特征实现漂移（v2.3）** | 评估结论不可迁移，且**无报错、无法察觉** | 第 38 章：单一实现源 + `feature_version` + 逐值相等测试 |
| **评估口径后定（v2.3）** | 早期预测记录无法用于评估（不可逆数据债） | 第 33 章必须早于第 4 章实现；口径版本化 |
| **Docker 端口未绑回环（v2.3）** | 服务意外暴露于局域网 | 第 34.5 节：Compose 中显式 `127.0.0.1:` 前缀；启动时校验 |
| **提示注入提升权限（v2.3）** | Agent 越权调用工具 | 第 34.8 节：Tool Gateway 依据服务端配置判定权限，不信任请求内容 |
| **配置项只存在于代码中（v2.3）** | 配置漂移、无法审计 | 第 32 章：配置目录 + CI 校验（32.5） |

---

# 18. 关键设计决策记录

| 决策 | 当前方案 | 备注 |
|---|---|---|
| 产品中心 | 行情研究与 AI 分析 | 核心定位 |
| 主要资产 | BTC、ETH | 首期范围 |
| 行情源 | Binance | 首期权威数据源 |
| AI 模式 | Rules + AI Hybrid | 规则和 AI 并行研究 |
| 交易风格 | Intraday 研究 | 预测周期需实验确定 |
| 部署 | 本地 Docker Compose | 单用户 |
| LLM | 云端 API | 统一 Gateway |
| AI 编排 | LangGraph | 可按复杂度调整 |
| 模拟执行 | Freqtrade Dry-run | 辅助验证，最简实现路径（第 26 章） |
| 通知渠道 | 多渠道：飞书机器人 Webhook（首期）+ 企业微信自建应用/微信插件（触达个人微信）+ 企业微信群机器人 | 首期飞书，企业微信为 P1 扩展 |
| 异动判定 | 确定性规则引擎 | LLM 只解释，不判定是否异动 |
| 调度 | APScheduler + 领域事件 | 定时与事件驱动混合，补跑机制 |
| 模型分层 | 三档 + 本地模型可选 | 成本可控，本地模型支持批量对比 |
| 评估框架 | 双轨（正确性 / 经济性） | 两轨结论允许背离 |
| 首期模拟市场 | USDⓈ-M Futures | 现货后续接入 |
| 策略发布 | 人工审批 | 不自动发布 |
| 真实交易 | 禁止 | 第一阶段明确排除 |
| **K 线可用性（v2.3）** | **仅已收盘 K 线落库并参与计算** | 未收盘仅入实时缓存供展示（第 29.5 节） |
| **异动触发源（v2.3）** | **USDM_FUTURES** | 现货完整采集但不直接触发告警（29.6.2） |
| **通知严重性模型（v2.3）** | **`severity` 与 `push_mode` 两个正交维度** | 解决「INFO 是否推送」的矛盾（22.4.3） |
| **事件去重语义（v2.3）** | **去重键 = `(fingerprint, revision)`** | 支持同一事件的 AI 解读升级投递（22.4.4.1） |
| **鉴权机制（v2.3）** | **静态 Bearer Token，默认开启** | 单用户本地系统，不引入用户体系（第 34.3 节） |
| **公网暴露（v2.3）** | **禁止；仅企微验证期开放一次性无状态回调入口** | 解决 13.4 与 28.2 的冲突（第 34.6 节） |
| **评估基准价（v2.4）** | **预测发布时已接收且有效的新鲜成交价** | 快照研究基准单存；与入场价严格区分（第 33.2 节） |
| **回测一致性（v2.3）** | **特征单一实现源 + `feature_version` 逐值校验** | 防「口径漂移」污染评估（第 38 章） |

## 18.1 尚待实验确定的参数

以下不应提前固化为不可变需求：

- 各预测时间窗口。
- 各周期 K 线回看长度。
- LLM 模型分层和调用预算。
- Agent 触发频率。
- 置信度阈值。
- 方向判定收益率阈值。
- 价格预测区间宽度。
- 机会有效期。
- 模拟交易最大持仓时间。
- 最大并发仓位。
- 连续亏损暂停阈值。
- 订单超时和撤单规则。
- 滑点和 Funding 假设。
- 各异动规则的触发阈值（需按实测校准，v2.1 第 22 章初始值）。
- 异动冷却窗口与每日推送配额。
- 定时任务的具体时间点与是否推送。
- 模型档位映射与日预算上限。
- 双轨评估中「实质变化」的判定阈值。

**v2.3 明确「已给出初始值、但仍需实测校准」的参数**（这些不是「待定」，而是「有初值待验证」）：

| 参数 | 初始值 | 校准依据 | 出处 |
|---|---|---|---|
| 方向判定收益率阈值 `θ` | `0.30%` | 样本满 200 条后按资产分别校准 | 33.3.1 |
| 最小样本量 `N_min` | 比率 30 / Brier 100 / 经济性 100 | 按实际指标的置信区间宽度反推 | 33.6.1 |
| 评估观察粒度 `Δt` | `15m` | 需与模拟执行的成交粒度一致 | 33.1 |
| 去重重叠比例 | 重叠 > 50%，按发布时间顺序保留非冗余窗口 | 不设跨整个分组的样本数量上限；阈值变更升口径版本 | 33.7.1 |
| 异动触发源市场 | `USDM_FUTURES` | 若主标的切换需同步改规则 | 29.6.2 |
| 通知合并等待窗口 | `30s` | 首投与 AI 解读的合并策略 | 22.4.4.1 |
| 账户级每日配额 | `60` | 按实际推送量与用户疲劳度调整 | 22.4.4.2 |
| 行情入库延迟 SLO | p95 < 500ms / p99 < 1s | 按本机实测网络与磁盘调整 | 37.3 |
| 全链路分析 SLO | p95 < 90s / 硬上限 300s | 按实测 Agent 耗时与模型档位调整 | 37.3 |
| 回测预热窗口 | EMA200 需 200 根等 | 按指标实际收敛速度调整 | 38.2.3 |

初始多周期研究窗口可从以下值开始实验，而非固定要求：

| 周期 | 初始回看数量 | 用途 |
|---|---:|---|
| 4H | 60 根 | 大周期趋势和市场状态 |
| 1H | 72 根 | 结构和方向 |
| 15m | 96 根 | 机会识别与确认 |
| 5m | 48 根 | 精细观察 |
| 1m | 可选 | 仅在确有必要时使用 |

---

# 19. 建议的项目目录结构

```text
ai-crypto-market-intelligence/
├── apps/
│   ├── api/                   # FastAPI 后端
│   └── web/                   # Next.js 前端
├── services/
│   ├── market_data/           # 采集层（第 29 章）
│   ├── data_quality/
│   ├── market_intelligence/   # 指标/结构/Regime 的唯一实现（第 38 章）
│   ├── anomaly/               # 异动检测规则引擎
│   ├── scheduler/             # 调度中心与定时任务
│   ├── notifications/         # 通知网关（channels/ templates/ rules/）
│   ├── ai_orchestrator/
│   ├── research/
│   ├── prediction_tracking/
│   ├── evaluation/            # 评估口径（第 33 章）
│   ├── backtesting/
│   ├── paper_trading/
│   └── monitoring/            # 可观测性（第 37 章）
├── agents/
│   ├── trading/
│   ├── research/
│   └── system/
├── prompts/                   # Prompt 模板与版本（prompts/<agent>/<template>.v{n}.md）
├── skills/
├── tools/
├── domain/
│   ├── market/
│   ├── analysis/
│   ├── signals/
│   ├── evaluation/
│   └── execution/
├── infrastructure/
│   ├── docker/
│   ├── database/
│   └── monitoring/
├── config/
│   ├── base.toml              # 非敏感结构化配置
│   └── catalog.toml           # 配置项目录（第 32.5 节，CI 校验）
├── migrations/                # Alembic
├── tests/                     # 第 36 章分层
│   ├── unit/  contract/  integration/  e2e/
│   └── fixtures/{market,responses,llm,anomaly}/
├── scripts/                   # 运维脚本（第 36.9 节）
├── docs/
├── docker-compose.yml
├── .env.example               # 全部配置项模板（第 32 章）
└── README.md
```

> **v2.3 补充说明**：
> - `config/` 与 `config/catalog.toml` 是第 32 章新增，用于配置项可枚举与 CI 校验；
> - `tests/fixtures/anomaly/` 存放异动规则回放基线（第 36.5 节），是规则的行为契约；
> - `services/market_intelligence/` 是**特征的唯一实现**，回测与线上必须共用（38.2.1）；
> - `public-callback` 为独立 Compose profile 下的可选服务（34.6），不在默认启动集合中。

---

# 20. 第一版 MVP 定义

第一版 MVP 的完成标志是**持续产出高质量 AI 综合分析，并能查看证据、比较变化和追踪结果**。手动、定时及重要异动触发共用分析流程；推送服务于分析触达，模拟交易为后续辅助验证。

**首版交付层级**：第 30 章按首版使用的表增量落地，P1 表不要求预建；第 31、35 章的端点与页面按同样范围交付，研究实验全套页面、模拟交易页面与多渠道运营页面不作为 MVP 阻断项。第 15 章的双轨经济性和企业微信验收按本章范围归入 P1，首版必需的内容质量、基本预测追踪与单渠道验收仍需通过。

**v2.4 范围原则：保留 AI 分析深度，收敛外围广度。** 首版必须交付可读、可追溯、可复盘的多 Agent 综合分析；多渠道、完整研究实验平台和模拟交易不阻断交付。第 4.1 节 P0 为核心目标能力清单，具体首版交付范围以本章为准。

建议以下列能力作为 MVP 验收：

### 20.1 行情与分析内核

1. Binance BTC/ETH 多周期行情持续采集（现货 + USDⓈ-M）。
2. 数据质量检查、断线恢复和历史补数。
3. 技术指标、市场结构、衍生品和市场状态分析。
4. 至少包含 Technical、Market Regime、Derivatives、Liquidity 和 Master Agent。
5. AI 输出结构化、包含证据、反证和不确定性。
6. 每次分析冻结市场快照和版本信息。

### 20.2 运行层（v2.1 新增，必需）

7. 提供手动分析、1h 定时综合分析、日报复盘及重要异动触发；15m/4h 独立 LLM 任务后续按价值与预算启用，多周期快照仍为首版必需。
8. 首版交付价格、成交量及衍生品重要异动规则，服务于分析触发；其余规则增量补齐。
9. 选择一个主要通知渠道完成分析摘要、详情入口、去重、冷却与配额；默认飞书，可按实际使用入口替换为企业微信。
10. 多渠道广播、企业微信作为第二渠道及其他渠道扩展为 P1，不阻断首版。
11. 推送失败有重试与降级，且记录 `notification_events`；移动端作为主要阅读入口时，必须验收手机打开详情、鉴权与阅读完整流程。
12. 关机后恢复优先补数据并生成当前分析；错过任务显式记录，历史分析按 `BACKFILL` 隔离，不逐条补发过时建议，不混入实时预测成绩。

### 20.3 追踪与评估

13. 预测到期后自动追踪实际行情。
14. 提供方向准确率、区间命中率、MFE/MAE 等基本评估。
15. 提供第 5.2.1 节的分析内容质量检查和反馈；首版积累预测正确性数据，经济性轨道在有完整计划与明确成本模型后作为 P1 接入，不为无交易计划的报告虚构收益。
16. Dashboard 以 AI 综合报告、相较上一轮的变化、关键观察条件和历史复盘为中心，市场图表与指标提供上下文；报告满足第 4.7.1 节。
17. 留存 Agent、模型、Prompt 和市场状态维度，提供基础筛选与规则/单模型/多 Agent 配对实验；完整分组排名、研究实验工作台、相似行情检索为 P1，样本不足时只显示计数与案例。

### 20.4 工程化底座（v2.3 新增，必需）

MVP 的可验收性依赖以下契约层已落地（详细规格见对应章节）：

18. 行情采集层遵循第 29 章：断线重连、缺口主动检测、**仅使用已收盘 K 线**触发异动规则。
19. 数据库表结构按第 30 章落地，含唯一键、索引与迁移策略。
20. API 按第 31 章实现，含统一错误码字典、分页语义与幂等约定。
21. 配置按第 32 章集中管理，环境变量全集可枚举、可校验。
22. 评估指标按第 33 章的数学口径实现，样本不足时输出 `INSUFFICIENT_SAMPLE`。
23. 服务具备鉴权（第 34 章），默认不对公网暴露；企微可信 IP 验证使用一次性临时入口。
24. 核心逻辑具备自动化测试（第 36 章），15 章验收项已映射到测试用例。
25. 关键指标可观测且有目标值（第 37 章）。

Freqtrade Dry-run 不属于 MVP 的阻断条件，可以在核心分析和评估链路稳定后接入。

---

# 21. 最终产品定义

> **本章已移至第 1.4 节「产品愿景」。** 保留编号仅为避免打断既有章节引用；阅读请直接前往 [1.4 产品愿景](#14-产品愿景原第-21-章)。

---

# 22. 异动检测与通知推送规格（v2.1 新增，P0；v2.2 扩展多渠道与微信可达）

本章是 v2.1 的核心补充。v2.0 对「分析」和「模拟交易」已有充分规格，但对「什么时候该提醒我」「提醒我什么」「怎么推」「推多少」几乎没有定义，导致系统无法形成日常使用闭环。

v2.2 在本章基础上把通知网关从「飞书单渠道」扩展为「多渠道」，并新增第 22.5 节回答「如何合规地把告警送到个人微信」。异动规则（22.2）、事件对象（22.3）、联动流程（22.6）**在 v2.2 中保持不变**。

## 22.1 分层职责

```text
Market Data + Features
        |
        v
[Deterministic Anomaly Engine]  <- 判断"是否异动"，纯规则，可测，可回归
        |  (产出 AnomalyEvent)
        v
[Anomaly Classifier / Dedupe]  <- 分级、去抖、指纹、冷却
        |
        +--> [LLM Interpreter]    <- 只做解释与定性，不决定是否推送
        |         |
        |         v
        +--> [Notification Gateway]  --> 飞书 / 企业微信(微信插件) / 群机器人
        |
        +--> [Analysis Run]       <- 触发一次完整多 Agent 分析
```

关键约束：

1. **异动判定不依赖 LLM**。LLM 不可用时异动仍然被检出并推送（推送确定性结论）。LLM 失败只降级为「降级卡片」。
2. **异动与定时分析共用推送通道**，但事件类型、模板、优先级不同。
3. 异动引擎的规则、阈值、版本必须入库并可复现（见 `anomaly_rules` / `anomaly_events`）。

## 22.2 异动规则表

以下阈值为**初始实验值**，必须可配置、可版本化，且需经至少 1 个月实测校准。分位数类阈值优先用滚动分位数而非固定值，以适应波动率状态切换。

> **v2.3 补充的三条通用约束（对下表所有规则生效）**：
>
> 1. **只使用已收盘 K 线**（第 29.5 节）。未收盘 K 线不落库、不参与计算，因此本表所有基于 K 线的规则天然只对已收盘数据生效。
> 2. **触发源为 `USDM_FUTURES`**（第 29.6.2 节）。现货数据仍完整采集，但**不直接触发**异动告警；现货仅用于基差计算与历史研究。
> 3. **规则阈值按版本管理**（第 30.6 节）。修改阈值产生新 `rule_version`，不修改旧行；历史事件的复现依赖 `anomaly_events.threshold` 快照。
>
> 另有三条规则**依赖 1m 数据**（`PRICE_PCT_1M`、`PRICE_PCT_1M_CRIT`、`PRICE_GAP`、`PRICE_FLASH_MOVE`）：当 `MARKET_ENABLED_INTERVALS_1M=false` 时，这些规则必须处于**显式禁用**状态，而不是静默失效（29.2.1）。

### 22.2.1 价格异动（Price Move）

| 规则 ID | 描述 | 周期 | 触发条件（初始值） | 级别 |
|---|---|---|---|---|
| `PRICE_PCT_1M` | 1 分钟绝对涨跌幅 | 1m | \|涨跌幅\| ≥ 0.35% | WARN |
| `PRICE_PCT_1M_CRIT` | 1 分钟绝对涨跌幅 | 1m | \|涨跌幅\| ≥ 0.80% | CRITICAL |
| `PRICE_PCT_5M` | 5 分钟绝对涨跌幅 | 5m | \|涨跌幅\| ≥ 0.60% | WARN |
| `PRICE_PCT_5M_CRIT` | 5 分钟绝对涨跌幅 | 5m | \|涨跌幅\| ≥ 1.20% | CRITICAL |
| `PRICE_PCT_15M` | 15 分钟绝对涨跌幅 | 15m | \|涨跌幅\| ≥ 1.20% | WARN |
| `PRICE_PCT_15M_CRIT` | 15 分钟绝对涨跌幅 | 15m | \|涨跌幅\| ≥ 2.50% | CRITICAL |
| `PRICE_PCT_1H` | 1 小时绝对涨跌幅 | 1h | \|涨跌幅\| ≥ 2.50% | WARN |
| `PRICE_PCT_1H_CRIT` | 1 小时绝对涨跌幅 | 1h | \|涨跌幅\| ≥ 4.50% | CRITICAL |
| `PRICE_GAP` | 跳空 | 1m | \|open − prev_close\| / prev_close ≥ 0.40% | CRITICAL |
| `PRICE_FLASH_MOVE` | 闪崩/插针 | 1m | 单根下影线 ≥ 2.0× ATR 且实体方向与影线相反 | CRITICAL |
| `PRICE_HIGH_BREAK` | 突破关键位 | 15m | 突破近 20 根 15m Swing High 且实体收于其上 | WARN |
| `PRICE_LOW_BREAK` | 跌破关键位 | 15m | 跌破近 20 根 15m Swing Low 且实体收于其下 | CRITICAL |

阈值需区分 BTC 与 ETH（ETH 波动通常更高），建议为每个资产单独配置，或使用「BTC 基准 × 资产波动系数」。

### 22.2.2 波动率异动（Volatility）

| 规则 ID | 描述 | 触发条件（初始值） | 级别 |
|---|---|---|---|
| `VOL_ATR_SPIKE` | ATR 突变 | ATR(14) 1h ÷ ATR(14) 96h 均值 ≥ 1.8 | WARN |
| `VOL_ATR_SPIKE_CRIT` | ATR 突变 | 比值 ≥ 2.5 | CRITICAL |
| `VOL_RVOL_ZSCORE` | 实际波动 z-score | 滚动 30 日 realized vol 的 \|z\| ≥ 3 | CRITICAL |
| `VOL_REGIME_FLIP` | 波动状态切换 | 波动率状态标签由 LOW → HIGH（或反向） | WARN |
| `VOL_COMPRESSION_BREAK` | 波动压缩后突破 | 带宽处于压缩区（Bollinger 带宽低于 20% 分位）后 1h 突破 | WARN |

### 22.2.3 成交量异动（Volume）

| 规则 ID | 描述 | 触发条件（初始值） | 级别 |
|---|---|---|---|
| `VOL_REL_VOLUME` | 相对成交量放大 | 相对成交量（对同周期历史同小时段均值） ≥ 3.0 | WARN |
| `VOL_REL_VOLUME_CRIT` | 相对成交量放大 | 比值 ≥ 6.0 | CRITICAL |
| `VOL_VOLUME_DRYUP` | 极度缩量 | 比值 ≤ 0.3 且持续 3 根 | INFO |
| `VOL_LARGE_TRADE` | 大额成交 | 单笔/聚合成交额 ≥ 该品种 5m 成交额 P99 | WARN |

### 22.2.4 衍生品异动（Derivatives）

| 规则 ID | 描述 | 触发条件（初始值） | 级别 |
|---|---|---|---|
| `DER_OI_SURGE` | OI 激增 | 1h OI 变化率 ≥ +3.0% | WARN |
| `DER_OI_SURGE_CRIT` | OI 激增 | 1h OI 变化率 ≥ +6.0% | CRITICAL |
| `DER_OI_FLUSH` | OI 崩塌 | 1h OI 变化率 ≤ −3.0%（疑似强平/去杠杆） | CRITICAL |
| `DER_FUNDING_EXTREME` | 资金费率极端 | \|funding\| ≥ 0.05%（8h 口径） | WARN |
| `DER_FUNDING_FLIP` | 资金费率翻号 | funding 穿越 0 零点 | INFO |
| `DER_BASIS_EXTREME` | 基差异常 | \|basis\| 处于历史 95 分位以上 | WARN |
| `DER_LS_SKEW` | 多空拥挤 | 多空比处于历史极端分位（≥P95 或 ≤P5） | WARN |
| `DER_PRICE_OI_DIVERGENCE` | 价格与持仓背离 | 价格与 OI 反向变动超过阈值 | WARN |

### 22.2.5 数据与系统异动（Data / System）

| 规则 ID | 描述 | 级别 |
|---|---|---|
| `DATA_STALE` | 行情数据陈旧（超过 2 倍采集周期未更新） | CRITICAL |
| `DATA_GAP` | K 线缺口 | CRITICAL |
| `DATA_QUALITY_DEGRADED` | 数据质量降级 | WARN |
| `DATA_WS_DISCONNECT` | WebSocket 断线 | WARN |
| `DATA_SOURCE_DIVERGENCE` | 双数据源价格差异超阈值 | WARN |
| `SYS_LLM_UNAVAILABLE` | LLM 服务不可用（影响分析质量） | WARN |
| `SYS_FREQTRADE_DOWN` | Freqtrade 实例异常（P1） | WARN |
| `SYS_RECON_DIFF` | 对账存在未解释差异（P1） | CRITICAL |
| `SYS_RISK_HALT` | 风控触发，暂停开仓 | CRITICAL |

### 22.2.6 规则分级与推送门禁

| `severity` | 含义 | 推送行为 | LLM 参与 |
|---|---|---|---|
| `INFO` | 背景信息 | `push_mode=AGGREGATE`，不单独推送，汇总进日报 | 否 |
| `WARN` | 值得注意 | `push_mode=PUSH`，推送简化卡片，可合并聚合 | 可选 |
| `CRITICAL` | 必须马上知道 | `push_mode=PUSH`，立即推送完整卡片 | 必须参与（不可用时降级） |

> **v2.3 澄清**：「不单独推送」是**异动规则**自身的 `push_mode` 选择，不代表「`INFO` 级别的事件一律不推」。`severity` 与 `push_mode` 是两个正交维度，完整定义见 22.4.3。异动引擎产出的事件级别与 `push_mode` 绑定固定（INFO→AGGREGATE、WARN/CRITICAL→PUSH）；而定时分析、日报等非异动事件即使 `severity=INFO`，其 `push_mode` 仍为 `PUSH`。

**降级规则**：CRITICAL 事件且 LLM 不可用时，推送「确定性卡片」，明确标注「AI 分析暂不可用，以下为规则引擎判定结果」，不得伪造 AI 结论。

## 22.3 异动事件对象

```json
{
  "anomaly_event_id": "ae_20261109_0012",
  "symbol": "BTCUSDT",
  "market_type": "USDM_FUTURES",
  "rule_id": "PRICE_PCT_5M_CRIT",
  "rule_version": "v1",
  "severity": "CRITICAL",
  "detected_at": "2026-11-09T01:23:45Z",
  "window_start": "2026-11-09T01:18:00Z",
  "window_end": "2026-11-09T01:23:00Z",
  "market_snapshot_id": "snap_...",
  "metrics": {
    "price_change_pct": -1.83,
    "atr_ratio": 2.31,
    "relative_volume": 4.7
  },
  "threshold": {"price_change_pct": 1.2},
  "direction": "DOWN",
  "cooldown_key": "BTCUSDT:PRICE_PCT_5M_CRIT",
  "fingerprint": "ae_9f2c...",
  "revision": 0,
  "revision_reason": "INITIAL_DETERMINISTIC",
  "llm_interpretation_id": null,
  "notification_status": "PENDING"
}
```

- `fingerprint`：内容哈希，用于去重。**v2.3 修正**：去重键为 `(fingerprint, revision)`，`fingerprint` 只标识事件身份（见 22.4.4.1）。
- `cooldown_key`：`symbol + rule_id`（或更粗的粒度），用于冷却窗口。
- `threshold`：触发时的实际阈值快照，保证可复现。
- `revision` / `revision_reason`：投递轮次，见 22.4.4.1。`notification_events` 中必须携带同名字段。

## 22.4 通知网关与渠道抽象

### 22.4.1 渠道抽象

定义统一接口 `NotificationChannel`：

```python
class NotificationChannel(Protocol):
    name: str
    capabilities: ChannelCapabilities   # 支持的消息类型、长度上限、频率上限

    async def send(self, message: NotificationMessage) -> NotificationResult: ...
    async def health_check(self) -> bool: ...
```

`NotificationResult` 的状态枚举（多渠道下必须能表达「部分成功」与「限流」）：

```python
class DeliveryStatus(str, Enum):
    SUCCESS = "SUCCESS"        # 全部接收人送达
    PARTIAL = "PARTIAL"        # 部分送达（企微 invaliduser 等），只对失败者重试
    FAILED = "FAILED"          # 全部失败
    RATE_LIMITED = "RATE_LIMITED"  # 触发渠道频率上限，退避后重试
    SKIPPED = "SKIPPED"        # 被治理层拦截（去重/冷却/配额/免打扰），未发出
```

首期在 Phase 3A 完成一个主要渠道，默认 `FeishuChannel`，也可按实际入口选择 `WeComAppChannel`。第二渠道与其他企微适配于 Phase 3B（P1）接入；邮件、Discord、Telegram 后续按同一接口扩展。

**多渠道设计原则**：

| 原则 | 说明 |
|---|---|
| 上游无感 | 异动引擎与分析流程只产出 `NotificationMessage`，不知道渠道存在；渠道差异全部收敛在渲染层与 Channel 实现内 |
| 渲染与发送分离 | 模板负责「内容结构」，`ChannelRenderer` 负责「按渠道能力把结构渲染成该渠道的 payload」，`Channel` 只负责「发出去 + 处理错误 + 限流」 |
| 渠道独立降级 | 某渠道失败不影响其他渠道；失败只对该渠道重试与进死信 |
| 渠道独立限流 | 各渠道频率上限不同（飞书 100/分钟、企微应用 30/分钟/成员、企微群机器人 20/分钟），限流状态**按渠道独立维护**，不可共用一个全局令牌桶 |
| 显式投递语义 | 一次事件可配置「广播到所有启用渠道」或「按优先级投递到第一个成功渠道」，避免多渠道轰炸 |

**渠道能力矩阵**（渲染层据此选择消息类型与截断策略）：

| 渠道 | 首选消息类型 | 降级类型 | 内容长度上限 | 频率上限（初始） | 可触达个人微信 |
|---|---|---|---|---|---|
| 飞书机器人 | `interactive` 卡片 | `text` | ≤ 30 KB | 100 次/分钟 | 否 |
| 企业微信自建应用（微信插件） | `text`（微信端可读，默认） | `markdown`（仅企微端可读） | 2048 字节 | 30 次/分钟/成员、1000 次/小时 | 是（`text` 模式下） |
| 企业微信群机器人 | `template_card` / `markdown_v2` | `text` | text 2048 / markdown 4096 字节 | 20 次/分钟/机器人 | 否（仅群内） |

### 22.4.2 飞书渠道

飞书渠道要点：

| 项 | 规格 |
|---|---|
| 协议 | 自定义机器人 Webhook `https://open.feishu.cn/open-apis/bot/v2/hook/{token}` |
| 消息类型 | `interactive` 卡片（首选）或 `text`（降级） |
| 签名 | 若机器人开启「签名校验」，需计算 `timestamp + "\n" + secret` 的 HMAC-SHA256 并 base64，字段名 `sign` |
| 频率限制 | 单机器人 100 次/分钟；超限返回非 0 业务码 |
| 长度限制 | 卡片内容建议 ≤ 30 KB；超限必须截断而非丢弃 |
| 幂等 | 飞书接口本身不保证幂等；重复消息由本地 `fingerprint` 去重层拦截 |
| 可信 IP | 不需要（Webhook 无 IP 白名单），这是相对企业微信自建应用的优势 |

签名计算（必须与飞书官方一致）：

```python
import base64, hashlib, hmac, time

def feishu_sign(secret: str, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")
```

注意：部分飞书版本要求 `sign` 与 `timestamp` 同时携带，且 `timestamp` 与服务器时间偏差需在 1 小时内。

### 22.4.3 消息类型与模板

**必须区分两个正交概念（v2.3 修正）**：

- `severity`：**信息等级**，描述事件本身的严重性，取值 `INFO` / `WARN` / `CRITICAL`。它只决定「内容呈现的强调程度」和「在门禁中的豁免权」。
- `push_mode`：**投递策略**，描述该事件类型是否主动推送给用户，取值 `PUSH`（主动推送）/ `AGGREGATE`（不单独推，聚合进上级报告）/ `DIGEST`（只在日报/周报中体现）。

> **v2.3 修正说明**：v2.2 用单个「级别」列同时表达严重性与是否推送，导致 `TIMED_ANALYSIS`、`DAILY_REPORT` 标记为 `INFO`（按 22.2.6 规则 `INFO` 不单独推送），而 23.1.2 又要求它们推送，形成直接矛盾。现拆分为两列，彻底消除该冲突。**`INFO` 不再等价于「不推送」**。

| 事件类型 | 模板 | `severity` | `push_mode` | 是否含 LLM |
|---|---|---|---|---|
| `ANOMALY_CRITICAL` | 异动快报 | CRITICAL | PUSH | 是（或降级） |
| `ANOMALY_WARN` | 异动简报（可聚合多条） | WARN | PUSH（可合并为一条） | 否 |
| `ANOMALY_INFO` | 异动背景信息 | INFO | AGGREGATE | 否 |
| `TIMED_ANALYSIS` | 定时分析报告 | INFO | PUSH | 是 |
| `OPPORTUNITY_TRIGGERED` | 机会触发 | WARN | PUSH | 是 |
| `PREDICTION_RESOLVED` | 预测到期结果 | INFO | DIGEST | 否 |
| `DAILY_REPORT` | 日报 | INFO | PUSH | 是 |
| `WEEKLY_REPORT` | 周报 | INFO | PUSH | 是 |
| `PAPER_TRADE` | 模拟仓位变化 | INFO / WARN | PUSH（WARN 时） | 否 |
| `SYSTEM_ALERT` | 系统告警 | WARN / CRITICAL | PUSH | 否 |
| `RISK_HALT` | 风控暂停 | CRITICAL | PUSH | 否 |

**投递策略与严重性的交互规则**：

1. `push_mode = AGGREGATE` 的事件**永不单独推送**，其内容在下一个 `PUSH` 类报告中以「汇总条目」呈现。
2. `push_mode = DIGEST` 的事件只在日报/周报中体现，不进入任何即时通道。
3. `push_mode = PUSH` 的事件，无论 `severity` 高低都进入投递流程；`severity` 只影响**门禁豁免**（配额、免打扰、限流排队顺序）：
   - `CRITICAL`：豁免账户级配额与免打扰，插队到队列最前。
   - `WARN`：受账户级配额约束，但不受免打扰约束（默认）。
   - `INFO`：同时受配额与免打扰约束。
4. 因此 `TIMED_ANALYSIS` 与 `DAILY_REPORT` 的 `severity = INFO` 只意味着「它们在配额紧张时可以被牺牲」，**不影响它们被推送这一事实**。

**`push_mode` 与各任务的对应**（与 23.1.2 任务清单一致）：

| 任务 | 事件类型 | `push_mode` | 说明 |
|---|---|---|---|
| `analysis_15m` | `ANOMALY_INFO` / 结构升级事件 | AGGREGATE | 聚合进 `analysis_1h` 与日报；关键结构事件升级为 `ANOMALY_WARN` |
| `analysis_1h` | `TIMED_ANALYSIS` | PUSH | 无实质变化时抑制（见 23.2.1） |
| `analysis_4h` | — | AGGREGATE | 不推送，进日报 |
| `analysis_daily` | `DAILY_REPORT` | PUSH | — |
| `analysis_weekly` | `WEEKLY_REPORT` | PUSH | — |
| `eval_predictions` | `PREDICTION_RESOLVED` | DIGEST | 聚合进日报 |
| `signal_expiry` | `PREDICTION_RESOLVED` | DIGEST | 聚合进日报 |
| `paper_sync` / `reconcile` | `PAPER_TRADE` / `SYSTEM_ALERT` | PUSH | 仅在有差异时 |

卡片必须包含：事件类型、标的、时间戳、市场状态、AI 结论（方向 + 置信度）、支持证据摘要（≤3 条）、反对证据摘要（≤3 条）、关键触发/失效条件、数据质量标记，以及 Dashboard 详情链接。

**截断策略**：按「结论 → 关键数据 → 证据摘要 → 明细链接」的优先级裁剪，链接指向 Dashboard 详情页，永远保留链接。

**多渠道渲染规则**：模板定义的是「语义结构」（结论/关键数据/证据/链接），各渠道渲染器负责降级：

| 语义块 | 飞书 | 企微应用（微信插件，`text` 模式） | 企微应用（`markdown` 模式） | 企微群机器人 |
|---|---|---|---|---|
| 标题 + 级别 | 卡片标题 + 颜色标签 | `【CRITICAL】BTCUSDT 异动快报` | markdown 标题 + `<font color="warning">` | `template_card` 标题区 |
| AI 结论（方向+置信度） | 字段行 | `结论 看空 置信度 72%` | `> 结论：<font color="info">看空</font> 72%` | 卡片主文案 |
| 关键数据 | 字段表格 | `关键 5m 涨跌 -1.83% ｜ 相对成交量 4.7x` | `**K:V**` 换行（**不支持表格**） | 卡片字段列表 |
| 证据摘要（各 ≤3 条） | 折叠面板 | `证据 - …` 缩进块 | `> - 证据…` 引用块 | 卡片文本 |
| 明细链接 | 按钮 `url` | `<a href="短链">详情</a>` | `[查看详情](url)` | 卡片 `jump_list` |
| 截断触发点 | 30 KB | 2048 字节（开启「始终进入主页」时 20 字节） | 2048 字节 | 2048 / 4096 字节 |

> 关键约束 1：企微 markdown **只支持语法子集**（标题、加粗、链接、行内代码、引用、3 种字体颜色），**不支持表格**。渲染器不得输出表格语法，否则在客户端显示为原始文本。
>
> 关键约束 2：**微信插件端不支持 markdown**（显示「暂不支持此消息类型」）。因此企微应用渠道默认用 `text` 模式 + `<a href>` 链接，详见 22.5.4。

### 22.4.4 推送治理（防轰炸）

**治理管线（固定顺序，不可交换）**：

```text
Event
  -> [1] 指纹去重 (fingerprint + revision)     <- 22.4.4.1
  -> [2] 冷却窗口 (cooldown_key + severity)
  -> [3] 聚合合并 (window 内同类 WARN 合并)
  -> [4] 降噪 (连续同向同规则 -> 首+末)
  -> [5] 免打扰判定 (用户时区 + severity 豁免)
  -> [6] 账户级配额扣减 (三级配额模型)
  -> [7] 生成投递计划 (DeliveryPlan: 1 个 event -> N 个 channel 任务)
  -> [8] 按渠道限流/排队 -> 渲染 -> 发送
```

| 机制 | 规则 |
|---|---|
| 指纹去重 | 相同 `(fingerprint, revision)` 在 TTL 内只推一次（默认 TTL 24h）。**注意 `revision` 的存在，见 22.4.4.1** |
| 冷却窗口 | 同一 `cooldown_key` 在冷却期内只推一次（CRITICAL 默认 5 分钟，WARN 默认 30 分钟；`push_mode=AGGREGATE/DIGEST` 的事件不单独推，不适用冷却） |
| 聚合合并 | 同一窗口内多条 WARN 合并为一条「异动简报」 |
| 优先级抢占 | CRITICAL 不受账户级配额限制，优先于 INFO 队列 |
| 账户级配额 | 见 22.4.4.2 三级配额模型；超限后仅保留 CRITICAL，其余进日报 |
| 免打扰 | 可配置时间段（初始建议用户时区 00:00–07:00 不主动推 CRITICAL 以下的）；CRITICAL 是否穿透免打扰需用户显式配置 |
| 降噪 | 连续 3 次相同方向同规则异动，只推首次 + 末次摘要 |

#### 22.4.4.1 指纹去重与「事件升级」的关系（v2.3 修正）

**v2.2 存在直接矛盾**：

- 22.4.4「指纹去重」：相同 `fingerprint` 在 TTL（24h）内只推一次；
- 22.6「异动 → 分析 → 推送」：先推确定性快报，再推「异动快报 + AI 解读」增强卡片，并声明「同一 fingerprint 视为同一事件，升级而非新增」。

两者不可同时成立——第二条必然被第一条拦截掉，导致 AI 解读永远发不出去。

**v2.3 解法：引入 `revision`（投递轮次）**。

`fingerprint` 的语义从「这个事件推送过一次了吗」改为「**这个事件的这个版本**推送过一次了吗」。去重键变为 `(fingerprint, revision)`。

| 概念 | 定义 |
|---|---|
| `fingerprint` | 事件**身份**。同一异动在生命周期内的所有版本共享同一 fingerprint。由 `symbol + rule_id + window_start` 等内容哈希得到，不随分析结果变化 |
| `revision` | **投递轮次序号**，同一 fingerprint 下递增。用于区分「同一个事件的不同信息量版本」 |
| `revision_reason` | 产生该轮次的原因，可审计 |

**标准轮次序列**（对应 22.6 联动流程）：

| `revision` | `revision_reason` | 内容 | `severity` | `push_mode` | 是否受去重约束 |
|---:|---|---|---|---|---|
| 0 | `INITIAL_DETERMINISTIC` | 纯规则确定性快报（无 AI） | 按规则级别 | PUSH | 是 |
| 1 | `AI_INTERPRETATION_READY` | 叠加 AI 解读（方向/置信度/证据/反证） | 继承 | PUSH | 是（**允许同 fingerprint 二次投递**） |
| 2 | `SIGNAL_REGISTERED` | 叠加信号登记结果（已生成 Trade Setup / Prediction） | 继承 | PUSH | 是 |
| N | `MANUAL_RESEND` | 用户手动重发 | 继承 | PUSH | 是（`MANUAL_RESEND` 绕过 TTL 检查，但记录审计） |

**关键规则**：

1. `revision` 递增**只允许**由以下触发：AI 解读就绪、信号登记完成、用户手动重发。**不得**因为「重试」而递增——重试属于投递层重试（同一 `notification_event_id` 的重试次数），不产生新的投递轮次。
2. 同一 `(fingerprint, revision)` 的投递在 TTL 内只允许**成功发送一次**。投递失败进入重试/死信，**不**因失败而递增 revision。
3. 升级投递（revision 1、2）**不重复占用冷却窗口**——冷却窗口针对「同一规则的新事件」，而非同一事件的新版本。但升级投递**占用账户级配额**（它是一条用户实际收到的消息）。
4. **升级投递必须显著区别于首投**，否则构成轰炸：
   - 渲染模板标注「🔄 更新」前缀（渠道可降级为 `[更新]`）；
   - 企微 `text` 模式下强制包含版本标识行；
   - 飞书卡片使用与首投不同的标题（如 `BTCUSDT 异动快报（AI 解读已更新）`）。
5. 若 AI 解读在首投冷却窗口内就绪（常见），则首投与升级合并为**一条**最终消息发出（延迟等待窗口，最长等待 30 秒）——避免「先发一条没结论的，再发一条有结论的」两连击。等待超时则先发确定性版本，AI 版本作为 revision 1 稍后发出。
6. `revision` 字段必须出现在 `notification_events` 中，并可在 Dashboard 查看同一异动的全部投递轮次。

> **实现提示**：去重层的唯一键为 `(fingerprint, revision)`；冷却层的唯一键为 `cooldown_key`；二者的 TTL 与作用域独立。**不得**用同一个 Redis key 同时承担两种语义。

#### 22.4.4.2 三级配额模型（v2.3 修正）

v2.2 只有「每日 60 条」一个数字，但系统里实际存在三种不同来源的限额，无法用一个数字覆盖：

| 层级 | 作用域 | 默认值 | 超限行为 | 是否可由用户配置 |
|---|---|---|---|---|
| **账户级** | 全渠道合计的用户级预算 | 60 条 / 日（UTC 日） | 非 CRITICAL 进入日报；CRITICAL 豁免 | 可（`NOTIFY_DAILY_QUOTA`） |
| **规则级** | 单条 `rule_id` 的触发频率上限 | 由 22.2 校准后确定 | 超出部分抑制，转入日报汇总 | 可（`notification_preferences.rules[].quota`） |
| **渠道级** | 单渠道的频控（技术限制） | 飞书 100/min、企微应用 30/min/成员、企微群机器人 20/min | 排队退避；**不消耗账户级配额** | 平台限制，不可上调 |

**配额扣减规则（关键）**：

1. **只有账户级配额会被真实扣减**。扣减时机：**投递计划生成后、实际发送前**占用额度，占用即计入；发送失败进入重试时**不重复扣减**（同一 `notification_event_id` 只扣一次）。
2. **渠道级限流不消耗账户级配额**——因为消息最终会送达，只是延后。这样设计避免了「飞书限流导致企微配额被误扣」的错误。
3. **规则级限额同样在投递计划阶段扣减**，命中限额的事件进入日报「已抑制」清单，可事后查看（用户会想知道「是不是有条规则被限流了」）。
4. **CRITICAL 的豁免范围**：豁免账户级配额与免打扰；**不豁免**规则级限额（否则某条规则的刷屏会击穿整个系统）。
5. **配额重置时点**：UTC 日 00:00（与 `NOTIFY_QUIET_HOURS` 的时区相互独立，见下）。补跑（`backfill`）消息**不占用实时配额**，但**计入当日总额度**——此为 v2.2 的约定，v2.3 保留（避免补跑洪水击穿当日预算）。

**免打扰时区（v2.3 修正）**：v2.2 的 `NOTIFY_QUIET_HOURS=00:00-07:00` 未指明时区，而 23.1.1 规定 cron 按 UTC 解释。v2.3 规定：

- `NOTIFY_QUIET_HOURS` 按**用户时区**（`APP_TIMEZONE`，默认 `Asia/Shanghai`）解释；
- `NOTIFY_DAILY_QUOTA` 的重置时点固定为 **UTC 日 00:00**；
- 两者时区不同是**有意的**：配额是系统资源预算（与机器时钟对齐），免打扰是用户作息（与用户感知对齐）。

**渠道配额分配**：账户级 60 条在多渠道间的分配由投递模式决定：

- `broadcast`：一条事件在所有启用渠道各发一次，**按「用户收到的消息总数」计 1 条**（多渠道不叠加计数），因为对用户而言是同一条告警；
- `first_success`：只发一个渠道，计 1 条；
- `severity_routing`：按 `NOTIFY_SEVERITY_ROUTING` 映射到单一渠道，计 1 条。

> 若某条策略未来允许「同一事件在多渠道各计 1 条」，必须显式改 `NOTIFY_QUOTA_COUNTING_MODE`，默认 `per_event`。

**多渠道下的治理语义（重要）**：

- 治理在**投递前**统一执行（去重、冷却、聚合、配额、免打扰），产出**一份**投递计划；渲染与发送是之后的事。避免同一事件在 3 个渠道各推 3 次导致实际轰炸 9 次。
- 配额与免打扰是**全渠道共享**的账户级预算：每日 60 条指的是「用户一天收到的消息总量」，而不是每渠道 60 条。
- 限流（rate limit）是**渠道级**的技术约束：某渠道达到频率上限时，该渠道的投递进入等待队列或降级到备选渠道（若配置了备选），**不消耗**用户级配额。
- 渠道投递模式：
  - `broadcast`：投递到所有启用且通过 `health_check` 的渠道（默认推荐，便于多端查看）。
  - `first_success`：按 `NOTIFY_CHANNEL_PRIORITY` 顺序投递，**第一个成功后即停止**（适合「只想在手机上被吵醒」的场景）。
  - `severity_routing`：按级别映射渠道（如 CRITICAL → 企微应用/微信插件，INFO → 飞书卡片）。

### 22.4.5 可靠性

- 指数退避重试（建议 1s / 3s / 10s / 30s），最多 4 次。
- 渠道返回非 0 业务码时判定失败并记入 `notification_events`；飞书判 `code`，企微判 `errcode`。
- 失败 3 次后进入死信队列，在 Dashboard 可见，并可手动重发。
- 推送为**异步**，不阻塞分析与采集主链路。
- 每次推送记录：`notification_event_id`、事件类型、渠道、payload 摘要、响应码、耗时、重试次数、最终状态。
- 企微 `access_token` 过期（`errcode 42001`）时必须先刷新 token 再重试，**不得**直接重发。

### 22.4.6 配置

```env
# 飞书
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx
FEISHU_SECRET=            # 可选，签名校验时必填
FEISHU_ENABLED=true

# 企业微信 — 路径 A：自建应用 + 微信插件（可触达个人微信）
WECOM_CORP_ID=wwxxxxxxxxxxxxxxxx
WECOM_CORP_SECRET=xxxxxxxx
WECOM_AGENT_ID=1000002
WECOM_TO_USER=userid1|userid2   # 或用 WECOM_TO_PARTY / WECOM_TO_TAG
WECOM_TO_PARTY=
WECOM_TO_TAG=
WECOM_APP_MSG_TYPE=text        # text（微信端可读，默认）| markdown（仅企微端可读）
WECOM_APP_ENABLED=true

# 企业微信 — 路径 B：群机器人 Webhook（仅群内，无需可信 IP）
WECOM_BOT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx
WECOM_BOT_ENABLED=false

# 通知治理
NOTIFY_DAILY_QUOTA=60
NOTIFY_QUIET_HOURS=00:00-07:00
NOTIFY_CRITICAL_BYPASS_QUIET=false
NOTIFY_CHANNEL_PRIORITY=feishu,wecom_app
NOTIFY_DELIVERY_MODE=broadcast      # broadcast | first_success | severity_routing
NOTIFY_SEVERITY_ROUTING=CRITICAL:wecom_app,INFO:feishu
```

> **v2.3 说明**：完整的环境变量全集、默认值、校验规则与敏感信息处理见**第 32 章**。本节只列通知相关的核心项，其余以第 32 章为准。`NOTIFY_QUIET_HOURS` 按 `APP_TIMEZONE` 解释，`NOTIFY_DAILY_QUOTA` 按 UTC 日重置（22.4.4.2）。
Webhook URL、CorpSecret 只存于本地 `.env`（不入库、不入仓库），Dashboard 显示时需脱敏。

## 22.5 企业微信渠道规格（触达个人微信）

> 本节回答一个具体问题：**用户不装飞书、只开微信时，告警怎么送到眼前**。企业微信自建应用 + 微信插件是腾讯官方唯一支持「应用消息落到个人微信」的路径。

### 22.5.1 两条路径对比

| 维度 | 路径 A：自建应用 + 微信插件 | 路径 B：群机器人 Webhook |
|---|---|---|
| 凭据 | CorpID + CorpSecret + AgentID | 一个 webhook key |
| Token | 需 `gettoken` 换取，有效期 7200s，**必须缓存** | 不需要 |
| 可信 IP 白名单 | **必须配置**，否则报 `60020` | **不需要** |
| 公网可达 | 配「接收消息服务器 URL / 可信域名」时需要 | 不需要 |
| 接收方 | 指定成员 / 部门 / 标签，可 `@all` | **固定为机器人所在群** |
| 触达个人微信 | **是**（微信插件会话） | 否（仅企微群内） |
| 频率限制 | 每应用对同一成员 30 次/分钟、1000 次/小时；每应用每日 ≤ 账号人数 × 200 人次 | 每机器人 20 条/分钟 |
| 幂等 | 支持 `enable_duplicate_check`（默认 1800s） | 不保证 |
| 消息类型 | text / markdown / template_card / 图文 等（**微信端只认 text**） | text / markdown / markdown_v2 / template_card 等 |
| 配置成本 | 高（需可信 IP + 成员关注微信插件） | 极低（2 步） |

**选型结论**：
- 用户要求「消息进我的微信」→ **路径 A**（本项目目标形态）。
- 只想在某个企微群里看 → **路径 B**（作为路径 A 不可用时的兜底）。
- 两者可同时启用；默认投递顺序 `wecom_app → wecom_bot`。

### 22.5.2 路径 A 接入步骤与前置条件

1. **注册企业微信**：无需营业执照，企业名称可自填，但需微信扫码绑定一个微信账号；登录后从「我的企业」底部获取 **CorpID**。
2. **创建自建应用**：「应用管理 → 自建 → 创建应用」，得到 **AgentID** 与 **CorpSecret**；设置应用可见范围（可见范围外的成员会收到 `invaliduser` / `81013`）。
3. **配置企业可信 IP**（2022-06-28 起对新应用强制）：
   - 后台提示「配置可信 IP 前，请先设置可信域名 或 设置接收消息服务器 URL」。
   - **仅主动发消息**也需要先满足该前置条件。本项目是纯单向推送，不接收回调，但仍需要一个公网可验证的 URL 来通过前置校验（可用 Nginx + 一次性验证文件；验证通过后隧道可关闭）。
   - 查出调用方出口 IP：调用 `gettoken`，报错信息中的 `from ip: x.x.x.x` 即为企微看到的来源 IP。
   - 家用宽带/办公网出口 IP 会变，**变化后必须重新加白名单**；生产环境应使用固定出口 IP。
4. **开启微信插件接收**：
   - 「我的企业 → 允许成员在微信插件中接收和回复聊天消息」打开。
   - 成员用**微信**扫码关注「微信插件」→ 微信内进入该企业。
   - 微信内：右上角 `+ → 设置 → 接收应用消息` **必须打开**；右上角 `+ → 停用的应用` 中确认应用未被停用。
   - 企业微信客户端：「设置 → 新消息通知 → 仅在企业微信中接收消息」→ 关闭「会话消息」与「应用消息」。
   - 应用设置页：确认「**在微信插件中始终进入主页**」**未勾选**（勾选后微信端只剩 20 字节）。
   - 未做这些步骤时 **API 返回成功但微信收不到**，或微信端只显示一句提示——这是该路径最常见的失败原因，必须写进验收用例。
5. **调用发送接口**：`POST https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=ACCESS_TOKEN`。

### 22.5.3 接口与鉴权

```text
# 换取 access_token（有效期 7200s，需缓存并提前刷新）
GET https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CorpID}&corpsecret={CorpSecret}
→ {"errcode":0,"errmsg":"ok","access_token":"...","expires_in":7200}

# 发送应用消息
POST https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=ACCESS_TOKEN
Content-Type: application/json
```

`text` 请求体（本项目主力类型，因为微信端只认 text）：

```json
{
  "touser": "userid1|userid2",
  "msgtype": "text",
  "agentid": 1000002,
  "text": {"content": "【CRITICAL】BTCUSDT 异动快报\n结论 看空 置信度 72%\n详情 <a href=\"http://localhost:8080/s/a1b2\">异动详情</a>"},
  "safe": 0,
  "enable_id_trans": 0,
  "enable_duplicate_check": 1,
  "duplicate_check_interval": 1800
}
```

`markdown` 请求体（仅企微端可读，微信端会显示「暂不支持此消息类型」）：

```json
{
  "touser": "userid1|userid2",
  "msgtype": "markdown",
  "agentid": 1000002,
  "markdown": {"content": "**BTCUSDT 异动快报**\n> 结论：..."},
  "safe": 0,
  "enable_id_trans": 0,
  "enable_duplicate_check": 1,
  "duplicate_check_interval": 1800
}
```

- `touser` / `toparty` / `totag` 三者**不能同时为空**。
- `touser` 支持 `@all`（此时忽略 `toparty`）。
- `content` 上限 2048 字节，超出截断（**注意是字节不是字符**，中文 3 字节/字 → 实际约 680 字）。
- `text` 的 `content` 支持 `\n` 换行与 `<a href="URL">文字</a>` 链接标签，是本项目在微信端构造结构化纯文本的基础。
- `enable_duplicate_check=1` 开启服务端去重（默认 1800s 窗口），作为本地 `fingerprint` 去重之外的第二层保险。

### 22.5.4 微信端降级行为（关键约束）

这一节是整条路径最容易踩坑的地方，必须精确区分「企微客户端」和「个人微信」两个渲染端。

**事实一：微信插件端不支持 markdown。** 官方文档明确「微工作台（原企业号）不支持展示 markdown 消息」。发送 `markdown` 时：
- 企微客户端：正常渲染。
- 个人微信：显示「暂不支持此消息类型，请在企业微信中查看」——**用户几乎看不到内容**，等同于推送失败。

**事实二：「在微信插件中始终进入主页」会把微信端限制到 20 字节。** 该选项本质是把应用类型从 `APP_TYPE_MSG` 改为 `APP_TYPE_HOME`：
- 微信端**仅能接收 `text`**，其他类型全部降级为提示文本；
- `text` 内容**限制 20 字节**，超出即截断；
- 企微端 `text` 上限仍是 2048 字节。

**事实三：成员侧的开关同样会阻断。** 企业微信客户端「设置 → 新消息通知 → 仅在企业微信中接收消息」中的「会话消息」「应用消息」若开启，微信端收不到任何应用消息。

**本项目的应对（渲染策略）**：

由于企微 API 不支持「同一应用对不同端发不同内容」，必须在**消息类型**层面做选择：

| 模式 | `msgtype` | 企微端 | 微信端 | 适用场景 |
|---|---|---|---|---|
| `text`（**默认**） | `text` | 纯文本（可用 `<a href>` 链接） | ✅ 正常显示，纯文本 | **以微信触达为首要目标时必须用这个** |
| `markdown` | `markdown` | 完整渲染 | ❌ 「暂不支持此消息类型」 | 只在企微端查看，微信端放弃 |

- 配置项 `WECOM_APP_MSG_TYPE=text|markdown`，**默认 `text`**，并在本项目的语境下明确理由：用户的通知入口在微信，宁可在企微端牺牲排版，也不能让微信端只显示一句「请在企业微信中查看」。
- `text` 模式下用**企微文本消息的富文本能力**弥补排版：`content` 支持 `\n` 换行与 `<a href="URL">链接</a>`，因此可渲染出结构化纯文本，例如：

```text
【CRITICAL】BTCUSDT 异动快报
时间 2026-11-09 09:23:45 (UTC+8)
结论 看空 置信度 72%
关键 5m 涨跌 -1.83% | 相对成交量 4.7x | ATR 比 2.31
证据 - 跌破 5m 布林下轨
     - 未平仓量 1h +3.4%
详情 http://localhost:8080/anomaly/ae_20261109_0012
```

- **绝不开启**「在微信插件中始终进入主页」，否则微信端只剩 20 字节。系统应在 Dashboard 的渠道自检页中显式提示这一点。
- 渲染层仍须实现**长度自适应**：企微 `text` 上限 2048 **字节**（中文 3 字节/字，实际约 680 字），超限时按「结论 → 关键数据 → 证据 → 明细链接」优先级裁剪；若用户误开了「始终进入主页」导致 20 字节限制，则只保留「级别 + 标的 + 短链」三段，并在 Dashboard 明确告警「微信端仅能接收 20 字节摘要，请关闭『在微信插件中始终进入主页』或改用群机器人」。
- `text` 模式下的链接建议用**短链**（Dashboard 暴露 `/s/{short_id}` 形式的短地址），否则长 URL 会挤占 20 字节预算。

### 22.5.5 错误码处理

| errcode | 含义 | 处理方式 |
|---|---|---|
| `0` | 成功 | 记录 `notification_events` |
| `40001` | 不合法的凭证（access_token 无效或不存在） | 强制刷新 token 后重试一次；仍失败则提示「CorpSecret 错误或应用已停用」 |
| `42001` | access_token 已过期 | 强制刷新 token 后重试，**不计为投递失败** |
| `60020` | 不允许从当前 IP 访问 | 不可重试。写入 `notification_events` 并在 Dashboard 明确提示「需在企业微信后台配置企业可信 IP」 |
| `45009` | 接口调用超过限制 | 退避后重试（通道级限流） |
| `45002` | 消息内容大小超过限制 | 不可重试。渲染层应已按 2048 字节截断；若仍触发说明渲染器有 bug，记入死信 |
| `81013` | 全部接收人无效（`touser`/`toparty`/`totag` 全部无效） | 不可重试。提示「接收人不在应用可见范围内」 |
| `invaliduser` / `invalidparty` / `invalidtag` | 部分接收人无效 | **部分成功**：有效人已收到。记为「部分成功」而非失败，并记录无效名单 |
| `43004` | 指定 userid 未绑定微信或未关注微信插件 | 不可重试。提示成员需用微信扫码关注微信插件 |
| `40008` / 消息体非法 | 参数错误（如 `touser`/`toparty`/`totag` 全空、`agentid` 类型错误） | 不可重试。记入死信供排查 |

**「部分成功」必须被正确建模**：`NotificationResult` 需要区分 `SUCCESS` / `PARTIAL` / `FAILED` / `RATE_LIMITED` / `SKIPPED`（免打扰/配额/去重）。`PARTIAL` 不触发整条重试，只对失败接收人重试。

### 22.5.6 路径 B：群机器人 Webhook 规格

```text
POST https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={BOT_KEY}
Content-Type: application/json
```

```json
{"msgtype": "markdown_v2", "markdown_v2": {"content": "**BTCUSDT 异动快报**\n> 结论：..."}}
```

| 项 | 规格 |
|---|---|
| 签名 | **不需要**（URL 里的 `key=` 就是凭据） |
| 频率限制 | 每机器人 20 条/分钟，超限 `45009` |
| 长度限制 | text 2048 字节 / markdown 4096 字节 |
| 群类型 | **仅支持内部群**，外部群不支持 |
| 生效范围 | 一个 bot 只绑定一个群；要发到两个群就建两个 bot |
| 消息类型 | text / markdown / markdown_v2 / image / news / file / voice / template_card |

**安全要求**：webhook URL 本身即凭据，泄露等于被任意发消息。必须与 CorpSecret 同级保护（只存 `.env`、Dashboard 脱敏、日志中掩码 `key=` 之后的值、禁止出现在日志与错误堆栈里）。

### 22.5.7 企微渠道配置前置检查（自检项）

在 `POST /api/v1/notifications/test` 之前，先执行只读自检并在 Dashboard 展示结果：

| 检查项 | 方法 | 失败提示 |
|---|---|---|
| token 可获取 | 调 `gettoken` | 「CorpID/CorpSecret 错误或账号未验证主体」 |
| 出口 IP 可信 | 调 `gettoken`/`send` 是否返回 `60020` | 「需在企业微信后台配置企业可信 IP：{from ip}」 |
| 应用可见范围 | `send` 返回 `81013` / `invaliduser` | 「接收人不在应用可见范围内」 |
| 成员已关注微信插件 | `send` 返回 `43004` | 「成员需用微信扫码关注微信插件，并在微信内开启『接收应用消息』」 |
| 消息类型兼容微信端 | 读取本地 `WECOM_APP_MSG_TYPE` | 若为 `markdown` → 警告「微信端不支持 markdown，将显示『暂不支持此消息类型』，请改为 `text`」 |
| 「在微信插件中始终进入主页」 | 无法从 API 读取，**只能靠人工确认** | 引导用户关闭该选项，否则微信端仅 20 字节 |
| 成员侧「仅在企业微信中接收消息」 | 无法从 API 读取，**只能靠人工确认** | 引导用户在企微「设置 → 新消息通知」中关闭该开关 |
| 微信插件消息开关 | 无法从 API 读取，**只能靠人工确认** | 引导用户按 22.5.2 第 4 步自检 |

> **重要限制**：企微 API **不提供**「查询成员是否已关注微信插件」「查询应用消息开关状态」「查询应用主页配置」的接口。后四项只能靠实际发送 + 用户反馈验证。因此验收流程必须包含一次真实的人工确认步骤（**人工在微信里看到消息**），不能只靠 API 自检通过就宣告渠道可用。

## 22.6 异动 → 完整分析 → 推送的联动流程

```text
Anomaly Engine 检出 CRITICAL
    |
    v
创建 AnomalyEvent（持久化，revision=0, revision_reason=INITIAL_DETERMINISTIC）
    |
    +--> [立即] 推送确定性快报（不含 AI）  <-- revision 0
    |         (等待窗口 ≤30s，若 AI 已就绪则合并发送，见 22.4.4.1 规则 5)
    |
    v
触发 Analysis Run（快照冻结在检出时刻）
    |
    v
多 Agent 分析完成
    |
    v
推送「异动快报 + AI 解读」增强卡片   <-- revision 1 (revision_reason=AI_INTERPRETATION_READY)
    |                                    同一 fingerprint，不同 revision => 允许再次投递
    v
登记 Signal / Prediction（若产出方向判断）
    |
    v
若登记成功，追加推送信号摘要         <-- revision 2 (revision_reason=SIGNAL_REGISTERED)
```

**v2.4 投递门禁**：以上 revision 表示允许的更新阶段，不要求每阶段都新发消息。首条快报后，仅当 AI 解读或登记预测改变主要情景、风险等级或关键观察/行动条件时追加推送；普通解释补充更新详情并进入摘要。渠道支持原消息更新时优先更新。提供事件静默及“有用/噪声/原因”反馈（鉴权 `POST /api/v1/anomalies/events/{event_id}/feedback`；记录于 `anomaly_evaluations.detail.user_feedback` 的追加列表，不覆盖历史反馈），与技术指纹去重分别统计。反馈量小不自动改阈值。

这样保证即使 LLM 不可用，用户仍能及时收到确定性快报；AI 恢复后提供有信息增量的综合解读。

**v2.3 修正**：原流程注释「同一 fingerprint 视为同一事件，升级而非新增」与 22.4.4 的指纹去重规则冲突（同一 fingerprint 在 24h TTL 内会被拦截第二次投递）。修正为通过 `revision` 区分同一事件的不同投递轮次，详见 22.4.4.1。

**顺序与延迟约束**：

| 步骤 | 目标延迟（从检出起） | 硬上限 |
|---|---|---|
| 确定性快报发出 | ≤ 5 秒 | 30 秒 |
| 全链路分析完成 | ≤ 180 秒 | 300 秒（超时输出部分结果，见 24.4） |
| AI 解读增强卡片发出 | ≤ 200 秒 | 330 秒 |

延迟目标定义与测量方式见 37.3 节。

## 22.7 相关数据库表（新增）

> **字段级规格见第 30 章（30.6 异动与通知、30.7 调度与系统）。本章只列用途。**

| 表 | 用途 |
|---|---|
| `anomaly_rules` | 异动规则定义与版本（阈值可配置、可回溯） |
| `anomaly_events` | 异动事件记录 |
| `anomaly_evaluations` | 异动后的市场实际表现统计（如异动后 1h 涨跌幅、反转率） |
| `notification_templates` | 消息模板版本（**需增加 `channel` 维度**，同一语义结构按渠道渲染） |
| `notification_events` | 推送投递记录（需含 `channel`、`delivery_status`、`invalid_recipients`、`rendered_type`） |
| `notification_dead_letters` | 死信队列 |
| `notification_preferences` | 用户级推送偏好（配额、免打扰、阈值覆盖、渠道开关与投递模式） |
| `notification_channel_state` | 各渠道的令牌桶/限流状态、access_token 缓存与过期时间、连续失败计数（渠道级健康与限流状态） |

`anomaly_evaluations` 很重要：它能回答「这个异动规则有用吗」——高触发率但后续无显著方向性的规则应当被降权或关闭，避免通知疲劳。

`notification_channel_state` 是多渠道新增的必要表：企微 `access_token` 需跨进程共享缓存（否则多 worker 重复换 token 会触发频率限制），且各渠道的限流状态必须持久化，否则重启后会丢失已用配额并可能瞬时打爆限流。

## 22.8 相关 API（新增）

- `GET /api/v1/anomalies/rules`
- `PUT /api/v1/anomalies/rules/{rule_id}`
- `GET /api/v1/anomalies/events`
- `POST /api/v1/anomalies/scan`（手动触发一次扫描，便于验证）
- `GET /api/v1/notifications/events`
- `POST /api/v1/notifications/test`（发送测试消息，验收必需；**必须显式指定 `channel`**，见下方「v2.3 修正」）
- `GET /api/v1/notifications/preferences`
- `PUT /api/v1/notifications/preferences`
- `POST /api/v1/notifications/{notification_event_id}/retry`
- `GET /api/v1/notifications/channels`（列出各渠道的启用状态、健康检查结果、限流余量、最近错误）
- `POST /api/v1/notifications/channels/{channel}/precheck`（执行 22.5.7 的前置自检，返回逐项结果与人工确认项清单）

> **v2.3 修正：测试接口语义**。v2.2 规定「未指定 `channel` 时按投递模式投递到全部启用渠道」，在 `broadcast` 模式下点一次「测试」会向**所有真实接收人**发送测试消息——测试语义丢失，且可能触发真实告警的配额与冷却统计。
>
> v2.3 规定：
> 1. `POST /api/v1/notifications/test` **必须显式指定 `channel`**，缺失时返回 `400 BAD_REQUEST`（错误码 `NOTIFY_CHANNEL_REQUIRED`）。
> 2. 测试消息**必须绕过**账户级配额与冷却窗口（否则测试可能在配额耗尽时静默失败，验收无法进行），但**必须写入** `notification_events` 并标记 `is_test=true`。
> 3. 测试消息**不产生** `anomaly_events`、不占用 `cooldown_key`、不计入 `notification_preferences` 的规则级限额。
> 4. `channel=wecom_app` 时，测试消息默认发送给 `WECOM_TO_USER` 指定的成员；**支持 `?recipient=userid` 覆盖**，便于逐个成员验证。
> 5. 需要「按投递模式整体验证」时，使用独立的 `POST /api/v1/notifications/test/broadcast`（显式命名，避免误触），该接口才允许多渠道投递。
> 6. 测试消息的 `severity` 固定为 `INFO`、`push_mode=PUSH`、`fingerprint` 固定为 `test:{channel}:{uuid}`（不参与真实去重）。

---

# 23. 调度与定时分析规格（v2.1 新增，P0）

## 23.1 调度模型

采用 **定时 + 事件驱动混合**：

| 类型 | 触发方式 | 示例 |
|---|---|---|
| `CRON` | 固定时间表 | 每 15 分钟结构分析、每 1 小时综合分析、每日 08:00 日报 |
| `INTERVAL` | 距上次完成 N 分钟 | 预测到期评估（每 5 分钟） |
| `EVENT` | 由领域事件触发 | 异动检出触发分析、数据质量降级触发告警 |
| `MANUAL` | 人工/API 触发 | Dashboard 点「立即分析」 |

统一由调度中心负责调度，任务本身是幂等的领域服务。

### 23.1.1 时区与时间语义

- 所有 cron 按 **UTC** 解释并在内部以 UTC 存储。
- 面向用户的「日报 08:00」等语义，由用户配置时区转换后生成实际 cron。
- 每次任务运行记录 `scheduled_at`（计划时间）、`started_at`、`finished_at`、`is_backfill`（是否补跑）。

### 23.1.2 任务清单（初始）

| 任务 ID | 类型 | 触发时间（UTC） | 依赖 | 产出 | 推送 |
|---|---|---|---|---|---|
| `data_ingest` | INTERVAL | 持续 | — | 行情入库 + 质量事件 | 仅异常时 |
| `anomaly_scan` | INTERVAL | 每 1 分钟 | `data_ingest` | `AnomalyEvent` | 按级别 |
| `eval_predictions` | INTERVAL | 每 5 分钟 | 数据就绪 | 预测评估结果 | 聚合进日报 |
| `analysis_15m` | CRON | `*/15 * * * *` | 快照就绪 | 结构分析 | 否（聚合） |
| `analysis_1h` | CRON | `5 * * * *` | 快照就绪 | 多 Agent 综合分析 | 是（`TIMED_ANALYSIS`） |
| `analysis_4h` | CRON | `7 */4 * * *` | 快照就绪 | 慢周期状态分析 | 否 |
| `analysis_daily` | CRON | `0 8 * * *` | 前序完成 | 日报 | 是（`DAILY_REPORT`） |
| `analysis_weekly` | CRON | 周一 09:00 UTC | 数据就绪 | 周报复盘 + 模型对比 | 是 |
| `signal_expiry` | INTERVAL | 每 5 分钟 | — | 信号过期/失效判定 | 否 |
| `paper_sync` | INTERVAL | 每 1 分钟 | Freqtrade 在线（P1） | 订单/持仓同步 | 异常时 |
| `reconcile` | CRON | `30 0 * * *` | paper_sync 正常（P1） | 对账报告 | 有差异时 |
| `health_check` | INTERVAL | 每 5 分钟 | — | 服务健康快照 | 异常时 |
| `backup` | CRON | 每日 02:00 | — | 数据库备份 | 失败时 |

> 具体时间点为初始值，必须可配置。

### 23.1.3 幂等与补跑

- 每个任务实例的幂等键：`{job_id}:{scheduled_at}`。
- 同一幂等键重复触发直接返回已有结果，不重复执行。
- 本地关机/休眠恢复时，先恢复数据并执行当前分析。历史任务按类型处理：数据与到期评估按窗口补齐；历史 AI 分析默认合并为缺口复盘，显式要求逐窗补生成时使用 `BACKFILL`，不纳入实时预测指标。补跑任务遵循以下约束：
  - 补跑上限：默认只补最近 24h 内、且未超过任务 `max_backfill_window` 的时间窗。
  - 补跑任务标记 `is_backfill=true`，输出中显式标注「补跑」。
  - 补跑结果默认只进恢复摘要或日报，不逐条推送过时建议；确需推送时标记 `backfill`，不占实时配额但计入每日总配额。
  - 超出上限的时间窗标记为 `MISSED`，在日报中列出，不得静默丢弃。

### 23.1.4 任务执行保障

| 机制 | 规则 |
|---|---|
| 超时 | 每个任务配置独立超时（默认 10 分钟），超时标记 `TIMEOUT` |
| 重试 | 指数退避，默认 3 次；`data_ingest` 等任务使用持续重试不放弃 |
| 依赖 | 依赖任务未 `SUCCESS` 时跳过并记录 `SKIPPED_DEPENDENCY` |
| 并发 | 同一 `job_id` 不并发；不同任务可并发但受全局 worker 池限制 |
| 分布式锁 | 多实例部署时用 Postgres advisory lock 防止重复执行（单机可简化） |
| 失败告警 | 任务连续失败达阈值（如 3 次）→ 推送 `SYSTEM_ALERT` |
| 手动触发 | Dashboard/API 可手动触发任意任务，仍走同一幂等逻辑 |

## 23.2 定时分析任务规格

### 23.2.1 `analysis_1h`（综合分析，主力）

| 项 | 规格 |
|---|---|
| 触发 | 每小时第 5 分钟 |
| 输入 | 最新快照（多周期 5m/15m/1h/4h）+ 近 24h 异动事件 + 近 24h 未决预测 |
| Agent | Technical、Market Structure、Derivatives、Liquidity、Market Regime、Event/Risk → Master |
| 产出 | Market Bias + 可能的机会 + 情景概率 + 证据/反证 |
| 推送 | 是，`TIMED_ANALYSIS` |
| 抑制 | 若与上一轮结论无实质变化（方向、置信度、关键价位均未变），默认不推送，仅更新记录 |

「无实质变化不推送」是抑制噪声的关键，判定阈值必须明确（方向变化 / 置信度变化 ≥ 0.15 / 关键价位变化 ≥ 0.5% / 状态标签变化）。

### 23.2.2 `analysis_15m`（结构分析）

| 项 | 规格 |
|---|---|
| 触发 | 每 15 分钟 |
| Agent | Market Structure、Technical（轻量档模型） |
| 产出 | 结构变化、突破/回踩、关键价位更新 |
| 推送 | 否，聚合进 `analysis_1h` 与日报 |
| 例外 | 若检出 `PRICE_HIGH_BREAK` / `PRICE_LOW_BREAK` 等关键结构事件，升级为推送 |

### 23.2.3 `analysis_4h`（慢周期）

| 项 | 规格 |
|---|---|
| 触发 | 每 4 小时第 7 分钟 |
| Agent | Market Regime、Technical（强模型档）、Historical Research |
| 产出 | 慢周期状态、相似历史情景 |
| 推送 | 否 |

### 23.2.4 `analysis_daily`（日报）

| 项 | 规格 |
|---|---|
| 触发 | 每日 08:00（用户时区） |
| 内容 | 当日行情概览、状态变化、异动汇总（含每条异动的后续实际表现）、预测结算、评估指标更新、模拟账户摘要（若启用）、系统健康 |
| 推送 | 是，`DAILY_REPORT` |
| 要求 | 必须包含「昨日预测的对错回顾」——这是整个系统的闭环体现 |

### 23.2.5 `analysis_weekly`（周报）

| 项 | 规格 |
|---|---|
| 触发 | 周一 09:00 |
| 内容 | 本周准确率与 Brier 变化、模型/Prompt 对比、异动规则有效性排名、模拟经济性对比、下周改进建议 |

## 23.3 相关数据库表（新增）

> **字段级规格见第 30 章（30.7 调度与系统）。本章只列用途。**

| 表 | 用途 |
|---|---|
| `scheduled_jobs` | 任务定义（cron、依赖、超时、推送策略） |
| `job_runs` | 每次执行记录（幂等键、状态、耗时、是否补跑） |
| `job_locks` | 分布式锁 |

---

# 24. LLM 分层、成本预算与本地模型（v2.1 新增）

v2.0 规定了「LLM 不做计算」「不每根 1m 调 LLM」，但没有规定**哪些 Agent 用什么模型**、**花多少钱**、**本地模型如何接入**。这直接影响系统能否长期自用。

## 24.1 模型分层策略

定义三档：

| 档位 | 用途 | 模型类型（示例） | 相对成本 | 调用频率上限 |
|---|---|---|---|---|
| `TIER_LIGHT` | 结构摘要、分类、模板化提取 | 小模型 / 本地模型 | 1× | 每 15m × 资产数 |
| `TIER_STANDARD` | 常规多 Agent 分析 | 中等模型 | 5–10× | 每 1h × 资产数 |
| `TIER_DEEP` | 日报/周报、复杂分歧复盘、实验对比 | 强推理模型 | 30–50× | 每日 1–2 次 |

分配原则：

- **能由规则直接得出的结论不调用 LLM**（如「今日涨跌幅 3.2%」这类纯数值）。
- Event/Risk、Market Regime 等偏确定性的 Agent 可用 `TIER_LIGHT` 或直接规则实现。
- Master Agent 汇总多 Agent 证据需要综合推理，用 `TIER_STANDARD`；当日出现重大分歧时升级为 `TIER_DEEP` 复盘。
- Historical Research 的相似情景检索由 Python 做（向量/结构化匹配），LLM 只做差异解读。

## 24.2 成本预算与配额

| 层级 | 指标 | 初始建议 |
|---|---|---|
| 单次分析 | 输入 token 上限 | 24k |
| 单次分析 | 输出 token 上限 | 4k |
| 单个 Agent | 单次最大 token | 8k |
| 单日 | 全局 token 预算 | 依所选模型单价反推，建议先设硬上限再按实测调整 |
| 单日 | 全局费用上限（USD） | 显式配置，超限即降级 |
| 降级顺序 | 见下表 | 依次执行，不得跳级 |

**降级顺序（24.2）**：

1. 停 `TIER_DEEP`
2. `TIER_STANDARD` 降为 `TIER_LIGHT`
3. 关闭非关键 Agent
4. 仅推确定性结论

所有 LLM 调用记录 `llm_cost_records`：模型、Agent、输入/输出 token、估算费用、耗时、是否降级。

**降级必须是显式可见的**：分析输出中标注 `degraded: true` 与 `degraded_reason`，避免把「便宜模型的分析」和「完整分析」混在一起统计。

## 24.3 本地模型支持

由于系统本地部署且个人自用，本地模型具备三项独特价值：数据不出本地、零边际成本、可用于批量对比实验。

要求：

- `LLM Gateway` 支持 `cloud` 与 `local` 两类 provider。
- 本地 provider 统一通过 OpenAI 兼容接口接入（Ollama、llama.cpp server、vLLM 等），Gateway 侧只需一套适配。
- 本地模型配置示例：

```env
LLM_PROVIDER_DEFAULT=cloud
LLM_PROVIDER_LOCAL_BASE_URL=http://ollama:11434/v1
LLM_PROVIDER_LOCAL_MODEL=qwen2.5:14b
LLM_PROVIDER_LOCAL_API_KEY=ollama
```

- 本地模型在报告中必须显示为独立 `model_id`，以便与云端模型分组对比。
- 本地模型不适合作为 `TIER_DEEP`（推理深度不足），建议映射到 `TIER_LIGHT` / `TIER_STANDARD`。
- 本地模型服务不可用时，Gateway 按降级策略回退到云端模型，并在输出中标注。

## 24.4 Agent 编排拓扑

明确 LangGraph 状态图的形态：

```text
[Snapshot Builder]
      |
      v
[Parallel Fan-out]  Technical / Structure / Derivatives / Liquidity / Regime / Risk
      |                      (TIER_LIGHT, 并行, 各自独立快照视图)
      v
[Evidence Aggregator]  合并为证据包，标注冲突
      |
      v
[Master Agent]  汇总 + 分歧处理 + 情景概率
      |
      +--> [Reflection]  仅当 Master 判定证据不足/存在关键分歧
      |         -> 定向追问某 1–2 个 Agent（携带具体争议点）
      |         -> 最多 1 轮（防止无限循环）
      v
[Signal / Prediction Registration]
```

约束：

- 子 Agent 独立分析，互不可见，避免过早趋同（v2.0 原则 4.7）。
- Reflection 最多 1 轮，且必须针对具体争议点，不做泛化重述。
- 每次 Agent 调用记录输入快照引用、输出、耗时、token，形成完整调用链。
- 全流程超时预算（如 3 分钟），超时则输出「分析超时 + 已完成部分」的部分结果，而不是整体失败。

## 24.5 Prompt 版本管理实践

v2.0 有 `prompt_versions` 表但无实践约定，补充：

- Prompt 模板以文件形式存放于 `prompts/<agent>/<template>.v{n}.j2`（或 `.md`），与代码同源管理。
- 每次修改 Prompt **必须升版本号**，并在 `prompt_versions` 记录：版本、模板哈希、变更说明、作者、时间。
- Agent 输出必须携带 `prompt_version`（v2.0 已要求）。
- 评估报表按 `prompt_version` 分组，形成「哪个 Prompt 更好」的对比。
- 新 Prompt 先在 `TIER_LIGHT` 影子运行（只记录不生效），与现行版本对比一段时间后再切换。
- 切换必须留下记录，且**切换后旧版本的评估窗口不得与新版本混合统计**。

---

# 25. 双轨评估：正确性 ↔ 可盈利性（v2.1 新增）

v2.0 正确地坚持「不得仅用模拟交易 PnL 衡量 AI 分析准确性」，但你的目标是「验证分析是否正确、**可盈利**」。这两者需要显式桥接，否则「评估体系」和「模拟交易」会是两条互不相干的线。

## 25.1 两条评估轨道

| 轨道 | 名称 | 回答的问题 | 输入 | 是否依赖模拟成交 |
|---|---|---|---|---|
| 轨道 A | 分析正确性轨道 | AI 的判断对不对？ | 预测记录 + 实际行情 | 否 |
| 轨道 B | 执行经济性轨道 | 若按这个判断做，扣除成本后是否盈利？ | 信号 + 模拟/回测成交 + 成本模型 | 是 |

**两轨结论允许背离**，且这种背离本身是重要发现：

| 情形 | 轨道 A | 轨道 B | 解读 |
|---|---|---|---|
| 判断对且赚钱 | 命中 | 正收益 | 理想 |
| 判断对但亏钱 | 命中 | 负收益 | **入场时机/成本/风控有问题**，分析没错，执行错了 |
| 判断错但赚钱 | 未命中 | 正收益 | 方向随机但恰好吃到波动，或存在幸存者偏差，需检查评估窗口定义 |
| 判断错且亏钱 | 未命中 | 负收益 | 分析与执行都错 |

「判断对但亏钱」是最需要被单独盯住的一类——它说明问题不在 AI，而在策略工程。

## 25.2 归因链路

每笔模拟成交必须能向上追溯到分析决策：

```text
paper_trade
  <- paper_platform_trades (freqtrade)
  <- executable_signal (signals)
  <- signal_events (TRIGGERED)
  <- market_bias / market_opportunity / trade_setup
  <- agent_output (Master)
  <- supporting_evidence[]
  <- market_snapshot_id + model_id + prompt_version
```

由此可回答：

- 这个收益来自哪个 Agent、哪次运行、哪条证据？
- 如果 Master 没有采纳某个 Agent 的结论，事后该 Agent 的判断对了没有？（被忽略的证据是金矿）
- Rules-only / AI-only / Hybrid 三账户的差异，量化出 **AI 的增量贡献**。

## 25.3 经济性指标（轨道 B）

| 指标 | 说明 |
|---|---|
| 净收益率 | 扣除手续费、Funding、滑点后的收益率 |
| 盈亏比 | 平均盈利交易 / 平均亏损交易 |
| 期望值 | 每笔交易期望收益（胜率 × 平均盈利 − 败率 × 平均亏损） |
| 成本占比 | 总成本 / 总收益 |
| 最大回撤 | 权益曲线最大回撤 |
| Sharpe / Sortino | 风险调整后收益（样本量不足时标 `INSUFFICIENT_SAMPLE`） |
| 资金利用率 | 平均保证金占用 / 权益 |
| 成本假设敏感度 | 在滑点 ×0.5 / ×1 / ×2 三档下的收益变化 |

**成本假设敏感度**对个人系统特别重要：如果策略只在「零滑点零成本」下盈利，它其实不可用。

## 25.4 成本模型要求

| 成本项 | 要求 |
|---|---|
| 手续费 | 按 Freqtrade 实际使用的 maker/taker 费率，不使用乐观假设 |
| Funding | 按持仓方向与实际 funding 发生额逐笔累计；Freqtrade 未覆盖时由内部账务补算，**必须防重复计费**（以 `funding_accrual_id` 幂等） |
| 滑点 | 记录假设模型与实际成交价差异 |
| 精度 | 数量与价格按合约精度取整，取整后的实际风险偏差需记录 |

## 25.5 评估报告组织

Dashboard 的评估页应同时呈现两轨，并**默认并排**：

- 左：轨道 A（准确率、Brier、校准、覆盖度）
- 右：轨道 B（净收益、成本占比、回撤、AI 增量贡献）
- 下方：按 Agent / 模型 / Prompt / 市场状态 / 资产 / 周期的分组对比

同时展示 `sample_size` 与 `INSUFFICIENT_SAMPLE` 标记，禁止在样本不足时给出排名。

---

# 26. 模拟交易最简实现路径（v2.1 补充，对应 Freqtrade 决策）

已确定「Freqtrade Dry-run，最简方式实现」。本章把 v2.0 的 7.4 / 7.5 / 7.6 收敛为一条可执行的最小路径，并明确哪些复杂度**先不做**。

## 26.1 最简路径

| 阶段 | 做什么 | 不做什么 |
|---|---|---|
| S1 | 单实例 Freqtrade Dry-run，接 Binance Futures，手动下单验证 Long/Short、止损止盈、杠杆 | 不接本系统信号 |
| S2 | 实现 Paper Trading Adapter（提交订单 + 状态映射 + 幂等键） | 不做完整对账 |
| S3 | 把 Executable Signal 接入 Adapter，走通 Rules-only 一个账户 | 不做三账户 |
| S4 | 补对账、账务、Funding 补算、日亏损限制 | — |
| S5 | 复制到 AI-only / Hybrid 三账户 | 不做执行质量评分 |

**关键顺序**：先让 Freqtrade 自己跑通（手工下单），再接系统。跳过 S1 会导致大量时间浪费在排查「是我的适配器写错了还是 Freqtrade 本来就这样」。

## 26.2 首期明确不做的

以下在首期可接受，作为已知简化：

- 不做部分成交处理（Freqtrade dry-run 本身成交模型简单，按全成交假设）。
- 不做精细滑点模拟（用固定假设 + 敏感性分析代替）。
- 不做真正的 Funding 逐笔结算（用估算 + 标注，或完全依赖 Freqtrade）。
- 不做多账户并发下单的复杂编排（先串行）。
- 不做交易所 Demo 环境的真实流动性对照。

每一条都必须在报告中标注为「简化假设」，不允许默认为真实。

## 26.3 与评估体系的衔接

- Freqtrade 提供的成交与钱包状态是**执行事实**，本系统内部的 `paper_ledger_entries` 是**账务投影**。
- 二者不一致即为 `paper_reconciliation_diffs`，未解释差异阻断新开仓。
- 简化假设（如全成交）必须在评估报告中显示，避免把简化带来的偏差归因到 AI。

---

# 27. 补充的数据源健壮性与容量（v2.1 新增）

## 27.1 第二数据源

v2.0 只有 Binance。建议引入 **Bybit 或 OKX** 作为交叉校验源（仅用于校验，不用于主分析）：

- 用途：检测 Binance 单源异常（数据错误、插针、局部故障）。
- 频率：低频（如每 5 分钟拉一次最新价与 5m K 线）即可。
- 成本：极低（REST 即可）。
- 差异超阈值（如 > 0.15%）时产生 `DATA_SOURCE_DIVERGENCE` 事件，级别 WARN。

如果不想加第二数据源，至少保留「Binance 自身的多接口一致性检查」（K 线最新价 vs ticker vs 深度最优价），这不需要额外数据源。

## 27.2 Binance 限流

| 接口 | 权重量级 | 备注 |
|---|---|---|
| Klines | 随 limit 递增 | 单次最多 1000 根，补数必须分页 |
| 深度快照 | 较高 | 建议只做低频快照，不做高频轮询 |
| 成交明细 | 较高 | 优先使用聚合成交接口 |
| OI / Funding | 低 | 可较频繁拉取 |

要求：

- 实现统一 `RateLimiter`，按接口权重预算分配。
- 补数算法必须分页（`startTime` 递进，单页 ≤ 1000 根），并带整体重试。
- 出现 429 / 418 时进入退避，且**不得并发重试**。
- 每个采集 worker 有明确的调用配额。

## 27.3 存储容量规划

| 数据 | 估算 | 保留策略 |
|---|---|---|
| K 线（2 币种 × 4 周期） | 约 1.5 MB/日 | 永久（1m 可只留 90 天） |
| 聚合成交 | 约 5–15 MB/日 | 90 天，之后只留聚合特征 |
| 盘口快照 | 取决于频率 | **只存 top20 + 每分钟 1 次**，原始深度不长期保留 |
| OI / Funding | 极小 | 永久 |
| Agent 输出 | 约 1–5 MB/日 | 永久（评估需要） |
| 快照 | 约 5–20 MB/日 | 永久或保留 1 年（评估复现需要） |

原则：**原始高频数据短期保留 + 派生特征长期保留**。盘口原始深度若全量保留，磁盘占用会在几个月内失控。

## 27.4 其他补充

- **数据库备份**：每日自动 `pg_dump` 到本地目录 + 保留 14 天；每季度做一次**恢复演练**（恢复到临时库并校验行数），仅有备份文件而未验证恢复是不合格的。
- **数据保留与清理策略**：明确每类数据的保留期与清理任务（`scheduled_jobs` 中的 `retention` 任务）。
- **免责声明**：所有对外展示（Dashboard、推送卡片、报告）底部附「本内容为个人研究记录，非投资建议」。

---

# 28. 变更说明与后续待办

## 28.1 v2.1 补齐了什么

| 原缺口 | v2.0 状态 | v2.1 状态 |
|---|---|---|
| 异动判定标准 | 仅「异常成交量和价格跳变」一句，且混在数据质量里 | 第 22 章完整规则表（价格/波动率/成交量/衍生品/系统 五类，约 40 条规则，含分级与初始阈值） |
| 通知推送 | 只有 `notifications` 模块名和 `notification_events` 表名 | 第 22 章：渠道抽象、飞书签名实现、9 类消息模板、去重/冷却/聚合/配额/免打扰、重试与死信 |
| 定时分析 | 只有 `APScheduler` 一个词 | 第 23 章：四类触发模型、13 个任务清单、幂等与补跑、5 个定时分析任务规格 |
| LLM 分层与成本 | 只说「不每根 1m 调 LLM」 | 第 24 章：三档模型、成本配额与降级顺序、本地模型接入、Agent 编排拓扑与 Reflection、Prompt 版本实践 |
| 正确性与盈利性割裂 | 双轨缺失 | 第 25 章：双轨评估、归因链路、经济性指标、成本敏感度 |
| 模拟交易过重 | v2.0 规格完整但无收敛路径 | 第 26 章：S1–S5 最简路径 + 首期明确不做的简化项 |
| 数据源与容量 | 无 | 第 27 章：第二数据源、Binance 限流、容量规划、备份与免责 |

## 28.1.1 v2.2 补齐了什么

| 原缺口 | v2.1 状态 | v2.2 状态 |
|---|---|---|
| 触达个人微信 | 完全未覆盖；飞书机器人无法触达微信，但绝大多数个人用户的通知入口在微信 | 第 22.5 节：企微自建应用 + 微信插件（路径 A）与群机器人 Webhook（路径 B）的完整规格——鉴权、可信 IP、频率限制、错误码、**微信端只认 text 的渲染约束**、前置自检 |
| 多渠道模型 | 「渠道抽象」只有一个 `NotificationChannel` 占位，无能力矩阵、无渲染层、无渠道级限流 | 第 22.4 节：渠道能力矩阵、渲染/发送分离、渠道独立限流、三种投递模式、跨渠道配额语义 |
| 凭据与安全 | 只说 Webhook URL 与 Secret 存 `.env` | 补充企微 CorpSecret / Bot Key 同级保护、日志脱敏、webhook URL 即凭据的显式警告 |
| 验收标准 | 仅覆盖飞书 | 新增第 15.7.1 节企业微信验收项，含「个人微信收到消息」与「API 无法自检、需人工确认」的显式说明 |

## 28.1.2 v2.3 补齐了什么

v2.3 不新增业务功能，只补**实现契约**与**修正内部矛盾**。

### 新增章节

| 新章 | 补齐的缺口 | v2.2 状态 |
|---|---|---|
| **29** 行情采集层规格 | WS 重连与恢复顺序、缺口三通路检测、**未收盘 K 线约定**、Spot/Futures 对齐与触发源、时间戳语义、采集层降级矩阵 | 仅有「要采什么」（4.2）与限流权重表（27.2） |
| **30** 数据契约 | 核心表字段级设计、索引、CHECK 约束、迁移与版本策略、完整表清单回填 | 11 章自述「字段、索引和约束需在实施阶段细化」 |
| **31** API 契约与错误码字典 | 统一响应封装、分页与游标、幂等键约定、长任务模式、**70+ 错误码字典**、合并后的完整端点目录 | 只有端点路径清单；12.5 要求「稳定错误码」却未枚举 |
| **32** 配置体系 | 配置层级与优先级、**环境变量全集**、启动时校验、敏感信息处理、配置项可枚举性 | 只有 22.4.6 / 24.3 的零散 env |
| **33** 评估指标数学口径 | 基准价 `P0` 定义、方向标签阈值、Accuracy 公式、Brier/Calibration、区间与覆盖率成对、MFE/MAE、失效判定、**最小样本量与 `INSUFFICIENT_SAMPLE` 行为**、**重叠窗口去重**、口径版本化 | 5.2 只有指标名 + 一句话说明 |
| **34** 鉴权与公网边界 | 威胁模型、Bearer Token 方案、前端鉴权流程、**网络暴露矩阵**、**企微可信 IP 的一次性公网入口（解决 13.4 与 28.2 的冲突）**、工具权限矩阵 | 13.4 只说「使用鉴权」，无机制；28.2 要求临时公网入口与之冲突 |
| **35** 前端规格 | 路由表、刷新策略、**6 种页面状态**、数据质量的前端表达、组件约定、评估页强制规则、图表与移动端 | 8 章只有页面区块顺序 |
| **36** 测试与工程约定 | 测试分层、**必测不变量清单**、CI 流水线、异动规则回放基线、代码规范、迁移约定、脚本约定 | 15 章只有验收条目，无测试策略 |
| **37** 可观测性与性能 | 指标清单（60+）、**SLO 与硬上限**、**量化成功指标**、日志规范、健康检查、容量与保留策略 | 13.2 只列监控项，无指标名与目标值 |
| **38** 回测与实盘一致性 | 单一特征实现源、`feature_version` 一致性检查、预热窗口、回测隔离、差异声明 | 6 章区分了两类回测，但未防「口径漂移」 |

### 修正的内部矛盾

| # | 问题 | 修正位置 |
|---|---|---|
| 1 | 指纹去重（24h TTL）与「同 fingerprint 升级而非新增」直接冲突，AI 解读永远发不出去 | 22.4.4.1 引入 `revision`（投递轮次），去重键改为 `(fingerprint, revision)`；22.6、22.3、15.7 同步 |
| 2 | `TIMED_ANALYSIS`/`DAILY_REPORT` 级别为 `INFO`，按 22.2.6「INFO 不单独推送」与 23.1.2「要推送」矛盾 | 22.4.3 拆分 `severity` 与 `push_mode` 两个正交维度；22.2.6 加澄清 |
| 3 | 配额只有「每日 60 条」一个数字，无法覆盖账户级/规则级/渠道级三种限额 | 22.4.4.2 三级配额模型，明确扣减时机与豁免范围；免打扰时区明确为用户时区 |
| 4 | `/notifications/test` 未指定 channel 时按投递模式投递，会向所有真实接收人发测试消息 | 22.8 + 31.9：`channel` 必填，测试绕过配额但记 `is_test`，另设 `test/broadcast` |
| 5 | 变更记录的章节引用全部错位一位（21/22/23/24 → 22/23/24/25） | 第 0 章 |
| 6 | 路线图阶段号乱序（3.2 排在 3 之前） | 第 16 章：改为 Phase 3.1，里程碑同步 |
| 7 | 20.2 出现两个 `9.` 编号 | 第 20 章：重新编号，并新增 20.4 工程化底座 |
| 8 | 第 21 章「最终产品定义」是愿景文字，位置夹在 MVP 与规格之间 | 移至 1.4，原位置改为指向说明 |
| 9 | 表清单分散在 11、22.7、23.3 三处 | 30.9 合并为权威清单，11/22.7/23.3 改为交叉引用 |
| 10 | 5.2 指标表未标注口径来源，实现者易各行其是 | 5.2 增加「口径章节」列，逐项指向第 33 章 |
| 11 | 4.6 的 8 态市场状态与 22.2.2 依赖的 3 态波动状态关系未定义 | 30.3 明确两维度正交并列存储 |

## 28.2 下一步待办（按优先级）

1. **飞书渠道连通性验证**（半天）：拿到 webhook，确认是否需要签名校验，先用 curl 打通再写代码。
2. **企业微信账号与自建应用准备**（半天）：注册企业微信、创建自建应用、配置可信 IP、拿到 CorpID/CorpSecret/AgentID。
   - 验证阶段需要临时公网入口（可信 IP 配置的前置条件要求可验证 URL）。**v2.3 已给出方案**：使用第 34.6 节的一次性公网回调入口（独立容器、仅响应带 token 的路径、无状态、验证后立即关闭），主服务仍保持不暴露公网。
   - 家用宽带出口 IP 会变，需提前决定固定出口方案；未解决前企微渠道可用性低于飞书。
3. **微信插件链路人工验证**（1 小时，需用户配合）：成员用微信扫码关注微信插件 → 关闭「仅在企业微信中接收消息」→ 微信内开启「接收应用消息」→ 确认应用未勾选「在微信插件中始终进入主页」→ 用 curl 发一条 `text` 消息 → 确认**微信端**可见完整正文。**这一步无法用代码代替**，应在写 `WeComAppChannel` 之前完成，避免代码写完才发现链路不通。
4. **异动阈值首轮校准**（1–2 周）：先采集 2–4 周历史数据回放，用真实分布修正第 22 章的初始阈值，避免上线即误报。
5. **异动规则有效率基线**（1 周）：统计每条规则的历史触发频率与后续方向性，作为后续调参依据。
6. **成本预算试算**（半天）：选定模型后按第 24 章频率反推月度预算，设硬上限。
7. **Freqtrade S1 单实例 PoC**（2–3 天）：纯手工下单跑通 Long/Short/止损止盈/杠杆，验证 7.9 的未知项。
8. **补第二数据源决策**（半天）：是否引入 Bybit/OKX 做交叉校验。
   - 若不引入，至少启用第 29.4.2 节的「Binance 自身多接口一致性检查」（WS vs REST），这是最低保障。
9. **确定方向标签阈值 `θ`（半天）**：按第 33.3.1 节，先用统一 `θ = 0.30%` 上线，样本满 200 条后按资产分别校准；变更须升 `evaluator_version`。
10. **生成异动规则回放基线（1 天）**：按第 36.5 节录制一周行情，产出 `tests/fixtures/anomaly/baseline.json`，作为规则的行为契约。
11. **配置目录落地（半天）**：按第 32.5 节建立 `config/catalog.toml` 并接入 CI 校验，避免配置项只存在于代码里。

## 28.3 仍存在的已知不足

- 所有阈值为初始经验值，**未经实测**，上线首月预期会有误报，需要预留调参节奏。
- 回测与模拟交易的滑点/Funding 模型仍是简化的，经济性结论的置信度有限。
- 异动引擎只能识别「已经发生」的异动，不预测异动。
- 双轨评估在样本积累到数百条预测前，统计结论不具备显著性。
- 本地模型的推理深度不足以承担 `TIER_DEEP`，复杂复盘仍依赖云端模型。
- 单机部署的可用性上限受限于个人电脑的开机时间，长时间关机会造成数据与评估断档。
- **企微渠道的微信可达性无法被程序完全验证**：企微 API 不提供「成员是否关注微信插件」「微信端消息开关状态」的查询接口，只能靠一次真实发送 + 用户确认。这使得企微渠道的自动化测试覆盖率天然低于飞书。
- **企微可信 IP 与固定出口的耦合**：家用宽带/办公网出口 IP 变化会导致 `60020` 报错，需人工重新加白名单；未解决固定出口前，企微渠道的可用性低于飞书。
- **微信端消息形态受限**：企微在微信侧**不支持 markdown**（会显示「暂不支持此消息类型，请在企业微信中查看」），因此本项目在企微应用渠道默认使用 `text` 模式 + `<a href>` 链接，代价是**放弃企微端的富排版**，所有渠道无法共用同一套卡片模板。这是「触达微信」这一目标的必然取舍。
- **企微渠道的可见性不可自检**：企微 API 不提供成员微信插件关注状态、消息开关状态、应用主页配置的查询接口，验收必须依赖一次真实的人工确认，自动化测试无法覆盖这一段。
- **v2.3 补齐的是契约，不是实现**：第 29–38 章给出了完整规格，但**代码一行都还没写**。契约的价值在于消除「实现时各自发挥」的空间，不在于缩短编码时间本身。
- **契约本身仍需在实现中反哺**：第 36.3 节的必测清单与第 37.3 节的 SLO 需要在真实运行中校准；预计在 Phase 3–4 期间会有多轮修订。
- **评估口径的样本量问题短期内无解**：第 33.6 节的门槛意味着 Phase 5 早期几乎所有分组都会显示 `INSUFFICIENT_SAMPLE`。这是**正确行为**，但会让人感觉「评估做不出来」。需要接受前 3–6 个月的评估结论只能是描述性的，不能是结论性的。
- **未收录进来的领域**：多用户与权限体系、高可用与多机部署、监控告警的外部集成（Prometheus Alertmanager 等）、行情数据的商业级授权问题——这些在个人本地场景下暂不需要，但未来若扩大范围需要重新设计。

---

# 29. 行情采集层规格（v2.3 新增，P0）

本章补齐 v2.0–v2.2 中最大的空洞：v2.2 定义了「要采什么」（4.2）和「怎么限流」（27.2），但**没有定义数据从哪来、以什么顺序到达、缺了怎么发现、哪根 K 线才算数**。

这四件事直接决定 M1 里程碑「数据可信」能否达成，也决定第 22 章的异动规则是否会因半根 K 线而产生假信号。

## 29.1 数据流拓扑

```text
                    Binance
      +----------------+----------------+
      |                                 |
  REST (补数/权威)                  WebSocket (实时)
      |                                 |
      +---------> Normalizer <----------+
                       |
              +--------+--------+
              |                 |
        CandleWriter      FeatureWriter
              |                 |
        market_candles     market_metrics
```

| 通道 | 用途 | 是否权威 |
|---|---|---|
| REST | 历史补数、缺口修复、周期性对账、低频大字段（OI 历史、合约规格） | **是**。K 线的最终值以 REST 为准 |
| WebSocket | 实时增量推送，降低延迟 | 否。仅用于时效性与实时触发 |

**核心原则**：WebSocket 与 REST 冲突时**一律以 REST 为准**（4.3 已要求，本章定义裁决流程）。

## 29.2 数据源订阅矩阵

| 数据 | Spot 来源 | Futures 来源 | 频率 | 通道 |
|---|---|---|---|---|
| K 线 | `wss://stream.binance.com:9443/stream?streams=` | `wss://fstream.binance.com/stream?streams=` | 实时推送 | WS + REST 补数 |
| 24h Ticker | `!ticker@arr` | `!ticker@arr` | 1s | WS |
| 盘口 | `depth20@100ms` | `depth20@100ms` | 100ms | WS（仅 top20） |
| 聚合成交 | `aggTrade` | `aggTrade` | 实时 | WS |
| Mark Price / Funding | — | `markPrice@1s` | 1s | WS（含当前 funding rate） |
| Open Interest | — | **无 WS 流** | REST 轮询 | REST |
| Funding 历史 | — | REST | REST | REST |
| 合约规格 | — | REST（exchangeInfo） | 启动时 + 每日 | REST |

**关键事实（影响架构）**：

1. **Binance 的 Open Interest 没有 WebSocket 推送**，只能通过 REST 轮询。因此 22.2.4 的 `DER_OI_SURGE` 规则的最小检测延迟受轮询间隔约束，见 29.4.3。
2. 盘口 top20 在 27.3 已被定义为「只存 top20 + 每分钟 1 次」，本章 29.7 明确写入方式。
3. `markPrice` 流同时提供**当前资金费率**，可作为 `DER_FUNDING_EXTREME` 的低延迟触发源；`funding_history` 表仍以 REST 为权威。

### 29.2.1 订阅清单

每个 `symbol × market_type × interval` 组合一条 K 线流：

```text
btcusdt@kline_1m / btcusdt@kline_5m / btcusdt@kline_15m / btcusdt@kline_1h / btcusdt@kline_4h
ethusdt@kline_1m / ethusdt@kline_5m / ethusdt@kline_15m / ethusdt@1h / ethusdt@kline_4h
```

- 现货与永续为**两套独立连接**（不同域名、不同鉴权状态），互不影响。
- `1m` 周期为可选（1.1 范围），但**若启用则必须同时启用 `5m`**，因为 `PRICE_PCT_1M`、`PRICE_GAP`、`PRICE_FLASH_MOVE` 三条规则依赖 1m 数据（22.2.1）。未启用 1m 时，这三条规则必须处于 `disabled` 状态而非静默失效。

## 29.3 WebSocket 生命周期

### 29.3.1 连接与重连

| 参数 | 值 | 说明 |
|---|---|---|
| 单次连接最大存活 | 24 小时 | Binance 会在 24h 后主动断开，属于正常行为 |
| 心跳 | Binance 客户端无需自建 ping（服务端 24s 内无数据会断开）；本项目**仍发送** WebSocket ping/pong 以提前探测半开连接 | |
| 重连退避 | 指数退避 + 抖动：`min(2^attempt × 500ms, 30s) + random(0, 500ms)` | 避免多实例同时重连打爆 IP 权重 |
| 最大连续重试 | 无限（带退避上限） | 本地系统应持续尝试，不因短暂故障永久放弃 |
| 重连后必做 | **必须执行一次 REST 对账**（29.4.2），不能直接信任新连接的连续性 | |

**半开连接检测**：连接建立但 30 秒内无任何消息，判定为半开，主动关闭并重连。

### 29.3.2 重连后的恢复顺序（强制）

```text
重连成功
   |
   1. REST 拉取 latest N 根 K 线（N = max(interval 回看, 缺口长度 + 5)）
   2. 与本地已存数据比对，得出真实缺口列表
   3. 用 REST 补齐缺口
   4. 补齐后才开始写入 WS 数据（可能重复，用 upsert 幂等）
   5. 记录 DATA_WS_DISCONNECT -> DATA_GAP（若有）质量事件
```

**禁止**「重连后直接开始写 WS 数据」——重连瞬间的 WS 数据可能跨越了断线期间的缺口，先写会留下永久空洞。

## 29.4 缺口检测

缺口检测有三条独立通路，缺一不可。

### 29.4.1 通路 A：时间连续性检查（离线/批量）

对任意 `(symbol, market_type, timeframe)`，相邻两根 K 线的 `open_time` 差值必须恰好等于该周期的毫秒数：

```text
expected_delta = interval_ms
actual_delta   = open_time[i+1] - open_time[i]
gap            = actual_delta > expected_delta
```

- 出现 gap 时产生 `DATA_GAP` 质量事件（CRITICAL，22.2.5）。
- gap 的缺失根数 = `actual_delta / expected_delta - 1`。
- 该检查在**每次写入后增量执行**（只检查最后 N 根）+ 每日一次全量回扫。

### 29.4.2 通路 B：WS 与 REST 交叉核对（在线）

每 5 分钟拉一次 REST 最新 K 线，与本地 WS 写入的最后 N 根比对：

| 差异 | 判定 | 动作 |
|---|---|---|
| `open_time` 集合相同，`close` 差异 ≤ 1 个价格最小变动单位 | 正常（未收盘 K 线的实时变动） | 不处理 |
| `open_time` 集合相同，`close` 差异 > 1 tick 且 K 线已收盘 | WS 数据异常 | 以 REST 覆盖，产生 `DATA_SOURCE_DIVERGENCE` |
| REST 有、本地无的 `open_time` | WS 漏推 | 用 REST 补齐，产生 `DATA_GAP` |
| 本地有、REST 无 | 本地脏数据 | 标记该根为 `INVALID`，产生质量事件 |

> 该通路同时承担 27.1 的「Binance 自身多接口一致性检查」职责，是不引入第二数据源时的最低保障。

### 29.4.3 通路 C：轮询类数据的时效检查

OI 与 Funding 历史为 REST 轮询，无 WS 可对照，因此用「拉取时间 vs 业务时间」判断：

| 数据 | 轮询间隔 | 允许滞后 | 超限判定 |
|---|---|---|---|
| `open_interest` | 60 秒 | 3 分钟 | `DATA_STALE` |
| `funding_rate` 当前值 | 1 秒（WS） | 2 分钟 | `DATA_STALE` |
| `funding_history` | 每 8 小时 funding 周期后 +5 分钟 | 1 个周期 | `DATA_STALE` |
| `ticker` | 1 秒（WS） | 30 秒 | `DATA_STALE` |

**延迟影响（重要）**：由于 OI 只能轮询，`DER_OI_SURGE`（1h OI 变化率 ≥ +3.0%）的**最快检出延迟 = 轮询间隔 + 1h 窗口对齐延迟**。初始轮询间隔 60 秒意味着该规则最快约在 1 分钟后检出，最慢约 2 分钟后。这在异动场景下是可接受的，但必须在文档与卡片中标注「衍生品异动的检出延迟高于价格异动」。

## 29.5 已收盘 K 线约定（关键约束）

这是本章最重要的一条规则，直接决定 22.2 的价格异动规则会不会被半根 K 线污染。

### 29.5.1 规则

1. **所有基于 K 线的异动规则，只允许使用已收盘（closed）的 K 线**。
2. WS 推送的 K 线事件携带 `k.x` 字段（`x=true` 表示该 K 线已收盘）。`x=false` 的事件**只用于实时展示**（Dashboard 实时价格），**不写入 `market_candles` 表**，**不参与任何指标计算**。
3. 未收盘 K 线的实时数据存于 `market_candles` 之外的临时结构（进程内缓存 / Redis），仅供 Dashboard 使用，TTL = 2 × 周期。

### 29.5.2 为什么这条约束不可省略

`PRICE_PCT_1M` 的阈值是 0.35%。一根 1m K 线在开盘 10 秒时，其 `close` 相对 `open` 的变动可能已经超过 0.35%——这是**区间内的正常波动**，不是异动。若用半根 K 线判定：

- 会产生大量假 `PRICE_PCT_1M` 告警；
- 同一根 K 线在收盘时会被判定两次（一次半根触发、一次全根触发），产生重复推送；
- 阈值语义从「1 分钟涨跌幅」变成「1 分钟内的瞬时跳变」，与 22.2.1 的定义不符。

同理，`PRICE_FLASH_MOVE`（单根下影线 ≥ 2.0× ATR）必须用完整 K 线判定，否则「下影线」长度在未收盘时随时变化。

### 29.5.3 实现约定

```text
WS kline event
   |
   +-- k.x == false --> 实时缓存（Redis, key=rt:{market_type}:{symbol}:{interval}）
   |                    仅供 Dashboard；TTL = 2 * interval
   |                    不落 market_candles，不参与任何计算
   |
   +-- k.x == true  --> 写入 market_candles（upsert）
                        触发 29.4.1 连续性检查
                        触发特征重算
                        触发异动扫描（该周期已收盘）
```

**扫描时机**：异动扫描任务 `anomaly_scan`（23.1.2，每 1 分钟）**不自己判断「哪根收盘了」**，而是查询 `market_candles` 中 `close_time <= now()` 的最新已落库 K 线。这保证扫描只读已落库的完整数据，与 29.5.1 一致。

## 29.6 Spot 与 Futures 的对齐

### 29.6.1 双市场并存

系统同时采集 Spot 与 USDⓈ-M Futures（2.1 范围）。二者是**两套独立数据**，不合并、不互相填补。

### 29.6.2 异动规则的触发源（v2.3 明确）

v2.2 只说「BTC/ETH」，未指明用现货还是永续触发。v2.3 规定：

| 规则类别 | 触发源 | 理由 |
|---|---|---|
| 价格异动（22.2.1 全部） | **`USDM_FUTURES`** | 24 小时成交、更深流动性、且是模拟执行的标的，预警与执行对象一致 |
| 波动率异动（22.2.2） | `USDM_FUTURES` | 同上 |
| 成交量异动（22.2.3） | `USDM_FUTURES` | 同上 |
| 衍生品异动（22.2.4） | 仅 `USDM_FUTURES` | OI/Funding/Basis 本就属于衍生品市场 |
| 跨市场价差 | Spot vs Futures | 新增规则 `DER_BASIS_EXTREME` 的数据来源 |

**现货数据仍完整采集并入库**，用于：基差计算（4.5）、历史研究（6.1）、相似行情检索、快照中的多市场上下文。它**不直接触发**异动告警。

### 29.6.3 基差计算约定

```text
basis_pct = (futures_mid - spot_mid) / spot_mid * 100
```

- 使用同一时刻的 `best_bid`/`best_ask` 中间价，不用收盘价（收盘价时间不同步会引入伪基差）。
- 快照中记录 `basis_pct` 时必须同时记录两个市场的 `source_watermarks`。
- 若现货与期货某一方数据 `STALE`/`MISSING`，基差不计算，标记为 `missing_data`，**不得用陈旧值或 0 替代**。

## 29.7 写入与幂等

| 表 | 写入方式 | 幂等键 |
|---|---|---|
| `market_candles` | `INSERT ... ON CONFLICT DO UPDATE`（upsert） | `(exchange, market_type, symbol, timeframe, open_time)`（11.1） |
| `market_trades`（聚合成交） | `INSERT ... ON CONFLICT DO NOTHING` | `(exchange, market_type, symbol, trade_id)` |
| `orderbook_snapshots` | 追加写 | `(exchange, market_type, symbol, captured_at)` |
| `open_interest_history` | `INSERT ... ON CONFLICT DO UPDATE` | `(exchange, market_type, symbol, ts)` |
| `funding_history` | `INSERT ... ON CONFLICT DO NOTHING` | `(exchange, market_type, symbol, funding_time)` |

**upsert 的更新策略（重要）**：

- 已收盘 K 线若被再次写入且 `close` 不同 → 说明 WS 与 REST 冲突，**以 REST 为准**，同时产生 `DATA_SOURCE_DIVERGENCE` 质量事件。
- 未收盘 K 线**不落库**（29.5.1），因此「已收盘 K 线被改写」一定是异常，不存在正常情况。

**`orderbook_snapshots` 的采样纪律**：

- 原始深度只保留 **top20**（27.3 明确的容量策略）；
- 快照频率：每分钟 1 次；高频 WS 数据仅用于计算派生特征（spread、depth imbalance），**不逐条落库**；
- 快照必须记录 `captured_at`（本地接收时间）与 `exchange_ts`（如有），供 4.5 的「采样频率必须说明」使用。

## 29.8 时间戳语义

每条行情记录必须区分四个时间（4.2 已要求，本章定义语义）：

| 字段 | 来源 | 语义 |
|---|---|---|
| `open_time` / `close_time` | 交易所 | K 线区间边界（UTC） |
| `exchange_ts` | 交易所 | 交易所事件时间戳（WS 事件 `E`） |
| `received_at` | 本地 | 数据到达本地进程的时刻（UTC） |
| `processed_at` | 本地 | 完成标准化与入库的时刻（UTC） |

**时钟同步**：服务启动时调用 `/api/v3/time` 一次，运行中每 10 分钟校准一次，记录 `clock_offset_ms`。本地时钟偏移 > 5 秒时产生 `DATA_STALE` 级别的系统告警（归入 `SYS_*` 类）。

**延迟计算**：

```text
latency_ms = processed_at - exchange_ts
```

- `latency_ms > 2000` 记为延迟样本超阈，产生数据质量事件（DEGRADED 级）。
- 该指标进 37.2 的 `market_data_latency_ms` 指标，p99 为 37.3 的目标。

## 29.9 采集层故障降级矩阵

| 故障 | 检测方式 | 数据层行为 | 分析层行为 | 推送 |
|---|---|---|---|---|
| WS 断开 | ping 超时 / 30s 无消息 | 按 29.3.2 恢复；期间缺口由 REST 补 | 使用最后有效快照，标记 `STALE` | `DATA_WS_DISCONNECT`（WARN） |
| REST 不可用 | HTTP 5xx / 超时 | 停止补数，保留已有数据 | 降级为仅使用最后有效值，标记 `STALE` | `DATA_STALE`（CRITICAL） |
| 429 / 418 限流 | HTTP 状态码 | 全局退避（27.2），不并发重试 | 同上 | 限流本身不推送，仅记录指标 |
| 单个周期数据缺失 | 29.4.1 | 该周期标 `MISSING` | 依赖该周期的特征标 `missing_data` | `DATA_GAP`（CRITICAL） |
| 交易所返回脏 K 线 | 4.3 合法性检查 | 拒绝写入，标 `INVALID` | 该周期不可用 | `DATA_QUALITY_DEGRADED`（WARN） |

**关键原则**：采集层任何故障都**不得**导致已落库数据被删除或覆盖。数据只会「变旧」，不会「变错」。

## 29.10 采集层验收标准（v2.3 新增）

- [ ] WS 断开后自动重连，且重连后先 REST 对账再写 WS 数据。
- [ ] 断开 10 分钟后恢复，缺口被完整补齐，K 线连续性检查无 gap。
- [ ] 未收盘 K 线**不写入** `market_candles`（可通过查询表内 `open_time + duration > now()` 的记录数恒为 0 验证）。
- [ ] 已收盘 K 线被重复推送时，主记录不增加，且不产生重复质量事件。
- [ ] WS 与 REST 对同一根已收盘 K 线给出不同 `close` 时，以 REST 为准并产生 `DATA_SOURCE_DIVERGENCE`。
- [ ] `PRICE_PCT_1M` 规则在半根 1m K 线上的极端波动下不触发（构造用例）。
- [ ] OI 轮询间隔不超过 60 秒，且 `DER_OI_SURGE` 的检出延迟被记录。
- [ ] 现货与永续数据互不覆盖，基差仅在两侧均有效时计算。
- [ ] 盘口快照仅保留 top20，快照频率为每分钟 1 次。
- [ ] 本地时钟偏移 > 5 秒时产生系统告警。

---

# 30. 数据契约（v2.3 新增，P0）

第 11 章给出的是**逻辑表清单**，并明确「字段、索引和约束需在实施阶段进一步细化」。本章把其中**直接决定实现正确性**的表细化到字段级，并给出统一约定。

> **约定**：本章是表结构的唯一真源。第 11 章保持为「表清单与用途说明」，两者不重复；22.7 / 23.3 新增的表在本章统一回填（见 30.9）。

## 30.1 通用类型与约定

| 约定 | 规则 |
|---|---|
| 时间 | 一律 `TIMESTAMPTZ`，存 UTC，展示层转换（11.7） |
| 主键 | 一律 `TEXT`，使用带前缀的可读 ID（如 `snap_20261109_a1b2c3`），**不使用自增整数** |
| 价格 | `NUMERIC(38, 12)` |
| 数量 | `NUMERIC(38, 12)` |
| 资金/权益 | `NUMERIC(38, 8)` |
| 比率/置信度 | `NUMERIC(10, 6)`（0–1 之间；百分比一律存小数） |
| 结构化输出 | `JSONB` |
| 布尔 | `BOOLEAN`，非空且有默认值 |
| 枚举 | `TEXT` + `CHECK` 约束（不使用 PG ENUM，避免后续加值需改类型） |
| 命名 | 全小写下划线；表名复数；索引名 `idx_<table>_<cols>`；约束名 `uq_<table>_<cols>` / `ck_<table>_<rule>` |

**ID 生成规则**：

| 前缀 | 实体 | 生成方式 |
|---|---|---|
| `snap_` | 市场快照 | `snap_{yyyymmdd}_{ulid10}` |
| `run_` | Agent / 分析运行 | `run_{yyyymmdd}_{ulid10}` |
| `out_` | Agent 输出 | `out_{ulid10}` |
| `sig_` | 信号（四层统一） | `sig_{ulid10}` |
| `pred_` | 预测记录 | `pred_{ulid10}` |
| `ae_` | 异动事件 | `ae_{yyyymmdd}_{seq4}` |
| `ne_` | 通知事件 | `ne_{ulid10}` |
| `job_` | 任务运行 | `job_{ulid10}` |
| `txn_` | 账务分录 | `txn_{ulid10}` |

> 使用 ULID 而非 UUID4：ULID 前缀含时间戳，天然按创建时间排序，索引局部性更好，且可读。

## 30.2 市场数据

### `market_candles`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `exchange` | TEXT | NOT NULL | 固定 `binance` |
| `market_type` | TEXT | NOT NULL, CHECK IN ('SPOT','USDM_FUTURES') | |
| `symbol` | TEXT | NOT NULL | `BTCUSDT` / `ETHUSDT` |
| `timeframe` | TEXT | NOT NULL, CHECK IN ('1m','5m','15m','1h','4h') | |
| `open_time` | TIMESTAMPTZ | NOT NULL | K 线起点（UTC，对齐到周期边界） |
| `close_time` | TIMESTAMPTZ | NOT NULL | K 线终点 |
| `open` | NUMERIC(38,12) | NOT NULL | |
| `high` | NUMERIC(38,12) | NOT NULL | |
| `low` | NUMERIC(38,12) | NOT NULL | |
| `close` | NUMERIC(38,12) | NOT NULL | |
| `volume` | NUMERIC(38,12) | NOT NULL | 基础币成交量 |
| `quote_volume` | NUMERIC(38,12) | NOT NULL | 计价币成交额 |
| `trades_count` | INTEGER | NOT NULL | 成交笔数 |
| `taker_buy_base_volume` | NUMERIC(38,12) | NULL | 主动买入量（数据源支持时，4.2） |
| `taker_buy_quote_volume` | NUMERIC(38,12) | NULL | |
| `exchange_ts` | TIMESTAMPTZ | NOT NULL | 交易所事件时间 |
| `received_at` | TIMESTAMPTZ | NOT NULL | 本地接收时间 |
| `processed_at` | TIMESTAMPTZ | NOT NULL | 入库完成时间 |
| `is_closed` | BOOLEAN | NOT NULL DEFAULT TRUE | **恒为 TRUE**（29.5.1）；保留列用于迁移期校验 |
| `source` | TEXT | NOT NULL, CHECK IN ('WS','REST','REST_BACKFILL') | 数据来源 |

约束与索引：

```sql
PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time)
CHECK (high >= greatest(open, close, low))
CHECK (high >= low)
CHECK (volume >= 0)
CHECK (quote_volume >= 0)
```

```sql
CREATE INDEX idx_candles_lookup   ON market_candles (symbol, market_type, timeframe, close_time DESC);
CREATE INDEX idx_candles_processed ON market_candles (processed_at);
```

> **CHECK 约束必须包含 OHLC 合法性**（4.3 要求），且**写入前先校验**，违反则拒绝写入并产生 `INVALID` 质量事件，而不是依赖数据库约束事后报错。

### `market_trades`（聚合成交）

| 字段 | 类型 | 说明 |
|---|---|---|
| `exchange` / `market_type` / `symbol` | TEXT | |
| `trade_id` | BIGINT | Binance `aggTrade` 的 `a` 字段 |
| `price` / `quantity` / `quote_quantity` | NUMERIC | |
| `is_buyer_maker` | BOOLEAN | **主动买卖判定**：Binance 定义 `isBuyerMaker=true` 表示买方是挂单方，即**该笔为主动卖出**。本项目统一存 `is_taker_buy = NOT is_buyer_maker` |
| `exchange_ts` | TIMESTAMPTZ | |
| `received_at` | TIMESTAMPTZ | |

```sql
PRIMARY KEY (exchange, market_type, symbol, trade_id)
CREATE INDEX idx_trades_window ON market_trades (symbol, market_type, exchange_ts DESC);
```

> `is_taker_buy` 的方向语义是 4.5「主动买卖量变化」的基础，**方向搞反会让全部流动性分析结论反转**。转换逻辑必须在代码注释与本文档同时固定，并配单测（36.3）。

### `orderbook_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `exchange` / `market_type` / `symbol` | TEXT | |
| `captured_at` | TIMESTAMPTZ | 本地快照时刻（UTC） |
| `exchange_ts` | TIMESTAMPTZ | 交易所时间（如有） |
| `bids` | JSONB | `[[price, qty], ...]` top20，**降序** |
| `asks` | JSONB | `[[price, qty], ...]` top20，**升序** |
| `best_bid` / `best_ask` | NUMERIC(38,12) | 冗余存储，便于索引查询 |
| `bid_qty_sum` / `ask_qty_sum` | NUMERIC(38,12) | top20 深度合计 |
| `spread_bps` | NUMERIC(12,4) | 点差（基点） |

```sql
PRIMARY KEY (exchange, market_type, symbol, captured_at)
CREATE INDEX idx_books_symbol ON orderbook_snapshots (symbol, market_type, captured_at DESC);
```

### `open_interest_history` / `funding_history`

```sql
-- open_interest_history
PRIMARY KEY (exchange, market_type, symbol, ts)
  open_interest NUMERIC(38,12) NOT NULL    -- 合约张数
  open_interest_value NUMERIC(38,12) NULL   -- 计价币价值

-- funding_history
PRIMARY KEY (exchange, market_type, symbol, funding_time)
  funding_rate NUMERIC(18,10) NOT NULL
  mark_price   NUMERIC(38,12) NOT NULL
```

### `instrument_metadata`

```sql
PRIMARY KEY (exchange, market_type, symbol)
  price_precision SMALLINT NOT NULL
  quantity_precision SMALLINT NOT NULL
  tick_size NUMERIC(38,12) NOT NULL
  step_size NUMERIC(38,12) NOT NULL
  min_qty NUMERIC(38,12) NOT NULL
  min_notional NUMERIC(38,12) NOT NULL
  contract_multiplier NUMERIC(38,12) NOT NULL DEFAULT 1
  quote_asset TEXT NOT NULL
  margin_asset TEXT NULL
  onboard_date TIMESTAMPTZ
  raw JSONB NOT NULL    -- exchangeInfo 原始响应，便于追溯
```

### `market_data_quality_events`

```sql
PRIMARY KEY (id)
  detected_at TIMESTAMPTZ NOT NULL
  exchange TEXT NOT NULL
  market_type TEXT NULL
  symbol TEXT NULL
  timeframe TEXT NULL
  rule_id TEXT NOT NULL          -- 复用 22.2.5 的 DATA_* / SYS_* 规则 ID
  severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','CRITICAL'))
  data_quality TEXT NOT NULL CHECK (data_quality IN ('GOOD','DEGRADED','STALE','MISSING','INVALID'))
  detected_by TEXT NOT NULL CHECK (detected_by IN ('WS','REST','CROSSCHECK','SCHEDULER','SYSTEM'))
  context JSONB NOT NULL        -- 缺口区间、字段值、阈值快照等
  resolved_at TIMESTAMPTZ NULL
```

## 30.3 特征与快照

### `market_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `snapshot_id` | TEXT | PK，`snap_...` |
| `symbol` / `market_type` | TEXT | |
| `as_of` | TIMESTAMPTZ | **数据截止时刻**（交易所时间），所有特征的数据上界 |
| `created_at` | TIMESTAMPTZ | 构建完成时刻 |
| `available_as_of` | TIMESTAMPTZ | 本次输入实际可用截止时间；按 received_at/版本引用校验 |
| `time_range_start` / `time_range_end` | TIMESTAMPTZ | 快照覆盖的数据区间 |
| `data_quality` | TEXT | 整体质量标记（4.3 的五态） |
| `data_quality_detail` | JSONB | 各周期/各数据源的分项质量 |
| `feature_version` | TEXT | 特征计算代码版本（`git sha` 或语义版本） |
| `schema_version` | TEXT | 快照内容结构版本 |
| `content` | JSONB | 快照完整内容（4.8 列出的全部语义块） |
| `source_watermarks` | JSONB | 各数据源的最新时刻（如 `{"candles_1h": "...", "oi": "..."}`） |

```sql
CREATE INDEX idx_snapshots_lookup ON market_snapshots (symbol, market_type, as_of DESC);
```

> **防未来数据泄漏的强制约束**：市场事实的事件时间必须 ≤ `as_of`；采集、构建等元数据时间不与市场事件时间混用。快照另存 `available_as_of`（实际信息可用截止时刻），实时输入的 `received_at` 必须 ≤ 该时刻；历史回放按当时接收时间或不可变版本引用切片，不把后补数据伪装成当时已知。缺少可用时间证明的历史输入标记为事后研究。分别测试事件时间、接收时间与数据修订。

### `snapshot_data_refs`

```sql
PRIMARY KEY (snapshot_id, data_kind, ref_key)
  data_kind TEXT NOT NULL     -- 'candle' / 'trade' / 'book' / 'oi' / 'funding'
  ref_key   TEXT NOT NULL     -- 如 'BTCUSDT:1h:20261101T0000Z'
  row_count INTEGER NULL
```

用于「快照复现」：给定 `snapshot_id`，可精确还原其输入数据范围。

### `market_metrics`

```sql
PRIMARY KEY (symbol, market_type, timeframe, metric_time, metric_name)
  metric_time TIMESTAMPTZ NOT NULL
  metric_name TEXT NOT NULL         -- 'EMA_20' / 'RSI_14' / 'ATR_14' / 'ADX_14' ...
  value NUMERIC(38,12) NOT NULL
  params JSONB NOT NULL             -- 计算参数，如 {"period":14}
  input_from TIMESTAMPTZ NOT NULL   -- 输入数据起点
  input_to   TIMESTAMPTZ NOT NULL   -- 输入数据终点
  feature_version TEXT NOT NULL     -- 4.4 要求
  data_quality TEXT NOT NULL
  computed_at TIMESTAMPTZ NOT NULL
```

> `feature_version` 是第 38 章「回测与实盘一致性」的核心锚点。

### `market_regimes`

```sql
PRIMARY KEY (symbol, market_type, timeframe, as_of)
  regime TEXT NOT NULL CHECK (regime IN ('UPTREND','DOWNTREND','RANGE','HIGH_VOLATILITY',
                                          'LOW_VOLATILITY','BREAKOUT_ATTEMPT','TREND_REVERSAL','UNCERTAIN'))
  volatility_state TEXT NOT NULL CHECK (volatility_state IN ('LOW','MEDIUM','HIGH'))
  confidence NUMERIC(10,6) NOT NULL
  score JSONB NOT NULL       -- 各子判定的原始分数
  rule_version TEXT NOT NULL
  computed_at TIMESTAMPTZ NOT NULL
```

> **v2.3 明确**：`regime`（8 态）与 `volatility_state`（3 态）是**两个正交维度**，前者是第 4.6 章的市场状态，后者是 22.2.2 `VOL_REGIME_FLIP` 规则依赖的波动状态。两者在同一行中并列存储，`VOL_REGIME_FLIP` 的触发条件明确为 **`volatility_state` 由 LOW→HIGH 或 HIGH→LOW 的变化**，而非 `regime` 的变化。

### `market_structure_features` / `sr_zones`

```sql
-- market_structure_features
PRIMARY KEY (symbol, market_type, timeframe, as_of)
  structure TEXT NOT NULL      -- 'HH_HL' / 'LH_LL' / 'RANGE' / 'UNDEFINED'
  swing_highs JSONB NOT NULL
  swing_lows  JSONB NOT NULL
  bos_events JSONB NOT NULL    -- break of structure
  choch_events JSONB NOT NULL
  feature_version TEXT NOT NULL

-- sr_zones
PRIMARY KEY (id)
  symbol TEXT NOT NULL
  market_type TEXT NOT NULL
  timeframe TEXT NOT NULL
  zone_type TEXT NOT NULL CHECK (zone_type IN ('SUPPORT','RESISTANCE'))
  lower_price NUMERIC(38,12) NOT NULL
  upper_price NUMERIC(38,12) NOT NULL
  strength NUMERIC(10,6) NOT NULL
  touch_count INTEGER NOT NULL
  created_at TIMESTAMPTZ NOT NULL
  invalidated_at TIMESTAMPTZ NULL
```

## 30.4 AI 与研究

### `analysis_reports`（v2.4：主要产品对象）

| 字段 | 类型 | 说明 |
|---|---|---|
| `analysis_id` | TEXT | PK；关联各 `agent_runs.analysis_id` |
| `symbol` / `market_type` | TEXT | 标的与市场 |
| `market_snapshot_id` | TEXT | FK → market_snapshots |
| `snapshot_as_of` | TIMESTAMPTZ | 市场事实截止时间；输入实际可用边界见关联快照 `available_as_of` |
| `published_at` | TIMESTAMPTZ NULL | 首次用户可见时间，发布时固定；未发布时为空 |
| `analysis_mode` | TEXT | `LIVE` / `BACKFILL` / `RETROSPECTIVE` |
| `status` | TEXT | `RUNNING` / `COMPLETE` / `PARTIAL` / `FAILED` |
| `previous_analysis_id` / `supersedes_id` | TEXT NULL | 前一可比报告与修订来源，FK → analysis_reports |
| `content` | JSONB | 第 4.7.1 节报告内容；发布后的内容不可覆盖，修订产生新报告 |
| `schema_version` / `versions` | TEXT / JSONB | 内容 schema 与模型/Prompt/特征版本 |
| `degraded` / `degraded_reason` | BOOLEAN / TEXT NULL | 降级标记与原因 |
| `created_at` | TIMESTAMPTZ | 创建时间 |

索引：`(symbol, market_type, published_at DESC)`；预测通过 `signals.analysis_id` 关联。原有分析列表/详情 API 返回报告对象；没有信号的报告仍能检索、阅读与反馈。

### `analysis_feedback`（v2.4）

字段：`feedback_id`（PK）、`analysis_id`（FK）、`useful`（BOOLEAN NULL）、`reason`（TEXT NULL）、`review_kind`（`USER`/`HUMAN_REVIEW`/`LLM_ASSISTED`）、`rubric_version`、`dimension_scores`（JSONB）、`reviewer_ref`、`created_at`。记录只追加；用户反馈与辅助评审分别统计。通过鉴权的 `POST /api/v1/analysis/{analysis_id}/feedback` 追加反馈；详情返回当前反馈摘要。

### `agent_runs` / `agent_outputs`

```sql
-- agent_runs
PRIMARY KEY (run_id)
  analysis_id TEXT NOT NULL
  agent_name TEXT NOT NULL
  agent_version TEXT NOT NULL
  model_id TEXT NOT NULL
  prompt_version TEXT NOT NULL
  market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id)
  as_of TIMESTAMPTZ NOT NULL
  time_horizon TEXT NOT NULL
  status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEGRADED'))
  tier TEXT NULL CHECK (tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP'))
  degraded BOOLEAN NOT NULL DEFAULT FALSE
  degraded_reason TEXT NULL
  started_at TIMESTAMPTZ NOT NULL
  finished_at TIMESTAMPTZ NULL
  duration_ms INTEGER NULL
  input_ref JSONB NOT NULL     -- 输入引用（快照 + 工具调用）
  error JSONB NULL

-- agent_outputs
PRIMARY KEY (output_id)
  run_id TEXT NOT NULL REFERENCES agent_runs(run_id)
  output JSONB NOT NULL          -- 4.7 规定的 15 个字段的结构化输出
  schema_version TEXT NOT NULL
  created_at TIMESTAMPTZ NOT NULL
```

> `output` 用 JSONB 而非拆列：字段会随 Agent 演化（4.7 已列出最低要求但非封闭集）。**但 schema 必须版本化**（`schema_version`），且读取时必须做版本适配。

### `model_runs` / `llm_cost_records`

```sql
-- model_runs
PRIMARY KEY (model_run_id)
  agent_run_id TEXT NULL REFERENCES agent_runs(run_id)
  provider TEXT NOT NULL
  model_id TEXT NOT NULL
  prompt_version TEXT NOT NULL
  input_tokens INTEGER NOT NULL
  output_tokens INTEGER NOT NULL
  cached_tokens INTEGER NULL
  latency_ms INTEGER NOT NULL
  retry_count INTEGER NOT NULL DEFAULT 0
  status TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','TIMEOUT','SCHEMA_INVALID','RATE_LIMITED'))
  error JSONB NULL
  created_at TIMESTAMPTZ NOT NULL

-- llm_cost_records
PRIMARY KEY (id)
  created_at TIMESTAMPTZ NOT NULL
  model_run_id TEXT NOT NULL
  model_id TEXT NOT NULL
  agent_name TEXT NULL
  tier TEXT NULL
  input_tokens INTEGER NOT NULL
  output_tokens INTEGER NOT NULL
  estimated_cost_usd NUMERIC(18,8) NOT NULL
  price_snapshot JSONB NOT NULL    -- 计算时使用的单价快照
  is_degraded BOOLEAN NOT NULL DEFAULT FALSE
```

### `prompt_versions` / `agent_versions`

```sql
-- prompt_versions
PRIMARY KEY (agent_name, prompt_version)
  template_path TEXT NOT NULL
  template_hash TEXT NOT NULL      -- 内容哈希，用于确认线上跑的到底是哪份
  description TEXT NULL
  created_at TIMESTAMPTZ NOT NULL
  change_note TEXT NOT NULL

-- agent_versions
PRIMARY KEY (agent_name, agent_version)
  config JSONB NOT NULL
  tool_allowlist TEXT[] NOT NULL
  tier TEXT NOT NULL
  created_at TIMESTAMPTZ NOT NULL
```

## 30.5 信号与预测

### `signals`（四层对象统一存储）

第 4.9 章的四层对象（Bias / Opportunity / Setup / Executable Signal）**统一存一张表**，用 `signal_type` 区分，不分表。

```sql
PRIMARY KEY (signal_id)
  signal_type TEXT NOT NULL CHECK (signal_type IN ('MARKET_BIAS','MARKET_OPPORTUNITY','TRADE_SETUP','EXECUTABLE_SIGNAL'))
  parent_signal_id TEXT NULL REFERENCES signals(signal_id)   -- 演进链
  symbol TEXT NOT NULL
  market_type TEXT NOT NULL
  timeframe TEXT NOT NULL
  direction TEXT NOT NULL CHECK (direction IN ('LONG','SHORT','NEUTRAL'))
  setup_type TEXT NULL
  market_regime TEXT NOT NULL
  confidence NUMERIC(10,6) NOT NULL
  supporting_evidence JSONB NOT NULL DEFAULT '[]'
  contradicting_evidence JSONB NOT NULL DEFAULT '[]'
  entry_zone JSONB NULL
  entry_trigger TEXT NULL
  stop_loss NUMERIC(38,12) NULL
  take_profit_levels JSONB NULL
  invalidation_condition TEXT NULL
  expected_holding_period TEXT NULL
  risk_reward_ratio NUMERIC(12,4) NULL
  status TEXT NOT NULL CHECK (status IN ('CREATED','UNDER_OBSERVATION','TRIGGERED','EXPIRED',
        'INVALIDATED','REJECTED_BY_RISK','SUBMITTED_TO_PAPER','PAPER_OPEN','PAPER_CLOSED','EVALUATED'))
  market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id)
  agent_output_id TEXT NULL REFERENCES agent_outputs(output_id)
  model_id TEXT NOT NULL
  prompt_version TEXT NOT NULL
  time_horizon TEXT NOT NULL
  created_at TIMESTAMPTZ NOT NULL
  expires_at TIMESTAMPTZ NOT NULL
  -- 预测评估所需
  analysis_id TEXT NOT NULL REFERENCES analysis_reports(analysis_id)
  analysis_mode TEXT NOT NULL      -- LIVE / BACKFILL / RETROSPECTIVE，与报告一致
  snapshot_as_of TIMESTAMPTZ NOT NULL
  published_at TIMESTAMPTZ NULL    -- 预测首次可见时间；晚于报告时按自身实际发布时间
  reference_price NUMERIC(38,12) NULL -- 发布后评价基准 P0，见 33.2
  reference_price_at TIMESTAMPTZ NULL
  reference_price_source TEXT NULL
  snapshot_reference_price NUMERIC(38,12) NULL -- 快照时研究基准，单独报告
  entry_price NUMERIC(38,12) NULL     -- 计划/实际入场语义，不能替代评估基准；旧版基准值迁移至独立字段，保留旧口径版本
  scenario_probabilities JSONB NULL -- {up,down,flat}；事件与阈值见 33.3/33.4
  probability NUMERIC(10,6) NULL      -- 辅助条件二分类概率 P(UP | 非 FLAT)，与 confidence 区分
  prediction_window JSONB NOT NULL   -- {"start":..., "end":..., "horizon":...}
```

```sql
CREATE INDEX idx_signals_status   ON signals (status, expires_at);
CREATE INDEX idx_signals_eval     ON signals (signal_type, created_at DESC);
CREATE INDEX idx_signals_snapshot ON signals (market_snapshot_id);
```

> **`confidence` 与 `probability` 必须分开**：`confidence` 是模型自述的主观置信度（4.7 已声明不等于统计胜率）；`probability` 是用于 Brier Score 的**显式概率**。若只有 `confidence`，Brier Score 无从计算（见 33.4）。

### `signal_events`

```sql
PRIMARY KEY (event_id)
  signal_id TEXT NOT NULL REFERENCES signals(signal_id)
  from_status TEXT NULL
  to_status TEXT NOT NULL
  reason TEXT NOT NULL
  context JSONB NOT NULL DEFAULT '{}'
  occurred_at TIMESTAMPTZ NOT NULL
```

> **强制约束**：信号状态**只能**通过追加 `signal_events` 变更，禁止 `UPDATE signals SET status=...`。每次读取信号当前状态必须由 `signal_events` 推导或由缓存维护，并可通过 `signals.status` 冗余加速（冗余值必须与事件链一致，由触发器或事务保证）。

### `prediction_observations` / `prediction_evaluations`

```sql
-- prediction_observations：预测窗口内每个观察点的实际行情
PRIMARY KEY (observation_id)
  prediction_id TEXT NOT NULL
  observed_at TIMESTAMPTZ NOT NULL
  open NUMERIC(38,12) NOT NULL
  high NUMERIC(38,12) NOT NULL
  low  NUMERIC(38,12) NOT NULL
  close NUMERIC(38,12) NOT NULL
  volume NUMERIC(38,12) NOT NULL

-- prediction_evaluations：到期判定结果
PRIMARY KEY (evaluation_id)
  prediction_id TEXT NOT NULL
  evaluated_at TIMESTAMPTZ NOT NULL
  window_start TIMESTAMPTZ NOT NULL
  window_end TIMESTAMPTZ NOT NULL
  base_price NUMERIC(38,12) NOT NULL
  final_price NUMERIC(38,12) NOT NULL
  ret_pct NUMERIC(18,8) NOT NULL
  direction_label TEXT NULL          -- 'UP'/'DOWN'/'FLAT'，口径见 33.2
  direction_correct BOOLEAN NULL
  high_price NUMERIC(38,12) NOT NULL
  low_price NUMERIC(38,12) NOT NULL
  mfe_pct NUMERIC(18,8) NOT NULL
  mae_pct NUMERIC(18,8) NOT NULL
  target_hit BOOLEAN NULL
  interval_hit BOOLEAN NULL
  invalidation_hit BOOLEAN NULL
  brier_score NUMERIC(12,8) NULL
  metrics JSONB NOT NULL             -- 第 33 章口径下的全部指标明细
  evaluator_version TEXT NOT NULL     -- 口径版本；口径变更必须升版本
```

> `evaluator_version` 是必须的：**评估口径变更必须可追溯**，否则「Prompt 更好」还是「口径变了」无法区分。

### `analysis_evaluation_runs` / `evaluation_results`

```sql
-- analysis_evaluation_runs
PRIMARY KEY (eval_run_id)
  started_at TIMESTAMPTZ NOT NULL
  finished_at TIMESTAMPTZ NULL
  window_start TIMESTAMPTZ NOT NULL
  window_end TIMESTAMPTZ NOT NULL
  evaluator_version TEXT NOT NULL
  scope JSONB NOT NULL          -- 过滤条件（资产/周期/agent/模型/prompt/状态）
  status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED'))

-- evaluation_results
PRIMARY KEY (result_id)
  eval_run_id TEXT NOT NULL REFERENCES analysis_evaluation_runs(eval_run_id)
  dimension TEXT NOT NULL        -- 'DIRECTION' / 'PRICE' / 'INTERVAL' / 'TARGET' / 'RISK' / 'PROBABILITY' / 'ECONOMIC'
  metric_name TEXT NOT NULL
  group_key JSONB NOT NULL       -- 分组维度键值，如 {"agent":"Technical","timeframe":"1h"}
  metric_value NUMERIC(18,10) NULL
  sample_size INTEGER NOT NULL
  is_insufficient BOOLEAN NOT NULL DEFAULT FALSE
  detail JSONB NOT NULL
```

## 30.6 异动与通知

### `anomaly_rules`

```sql
PRIMARY KEY (rule_id, rule_version)
  rule_id TEXT NOT NULL
  rule_version TEXT NOT NULL
  category TEXT NOT NULL         -- 'PRICE'/'VOLATILITY'/'VOLUME'/'DERIVATIVES'/'DATA_SYSTEM'
  description TEXT NOT NULL
  severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','CRITICAL'))
  push_mode TEXT NOT NULL CHECK (push_mode IN ('PUSH','AGGREGATE','DIGEST'))
  params JSONB NOT NULL          -- 阈值等可配置参数
  asset_overrides JSONB NOT NULL DEFAULT '{}'   -- BTC/ETH 分别配置（22.2.1 末段）
  market_type TEXT NOT NULL DEFAULT 'USDM_FUTURES'
  enabled BOOLEAN NOT NULL DEFAULT TRUE
  effective_from TIMESTAMPTZ NOT NULL
  effective_to TIMESTAMPTZ NULL
  created_at TIMESTAMPTZ NOT NULL
  change_note TEXT NOT NULL
```

> **阈值版本化**：修改阈值**不修改旧行**，而是插入新 `rule_version` 并把旧的 `effective_to` 置为新版本的 `effective_from`。历史 `anomaly_events` 引用的 `rule_version` 因而永远可复现（22.3 已要求「阈值快照」）。

### `anomaly_events`

```sql
PRIMARY KEY (anomaly_event_id)
  detected_at TIMESTAMPTZ NOT NULL
  symbol TEXT NOT NULL
  market_type TEXT NOT NULL
  rule_id TEXT NOT NULL
  rule_version TEXT NOT NULL REFERENCES anomaly_rules(rule_id, rule_version)
  severity TEXT NOT NULL
  detected_by TEXT NOT NULL DEFAULT 'RULE_ENGINE'
  window_start TIMESTAMPTZ NOT NULL
  window_end TIMESTAMPTZ NOT NULL
  market_snapshot_id TEXT NOT NULL
  metrics JSONB NOT NULL
  threshold JSONB NOT NULL
  direction TEXT NULL
  cooldown_key TEXT NOT NULL
  fingerprint TEXT NOT NULL
  revision INTEGER NOT NULL DEFAULT 0
  revision_reason TEXT NOT NULL DEFAULT 'INITIAL_DETERMINISTIC'
  llm_interpretation_id TEXT NULL
  notification_status TEXT NOT NULL DEFAULT 'PENDING'
```

```sql
CREATE UNIQUE INDEX uq_anomaly_fp_rev ON anomaly_events (fingerprint, revision);
CREATE INDEX idx_anomaly_detected ON anomaly_events (detected_at DESC);
CREATE INDEX idx_anomaly_rule     ON anomaly_events (rule_id, detected_at DESC);
```

> `fingerprint` 的生成**不得**包含 `revision` 或任何会随分析结果变化的内容（22.4.4.1）。

### `anomaly_evaluations`

```sql
PRIMARY KEY (evaluation_id)
  anomaly_event_id TEXT NOT NULL REFERENCES anomaly_events(anomaly_event_id)
  horizon TEXT NOT NULL          -- '1h' / '4h'
  ret_pct NUMERIC(18,8) NOT NULL
  max_favorable_pct NUMERIC(18,8) NOT NULL
  max_adverse_pct NUMERIC(18,8) NOT NULL
  reversal BOOLEAN NOT NULL      -- 是否在 horizon 内反向
  is_directional BOOLEAN NOT NULL
  detail JSONB NOT NULL DEFAULT '{}' -- 分规则目的评价；user_feedback 为只追加反馈列表
  evaluated_at TIMESTAMPTZ NOT NULL
```

### `notification_events`

```sql
PRIMARY KEY (notification_event_id)
  created_at TIMESTAMPTZ NOT NULL
  event_type TEXT NOT NULL
  severity TEXT NOT NULL
  push_mode TEXT NOT NULL
  fingerprint TEXT NULL
  revision INTEGER NOT NULL DEFAULT 0
  revision_reason TEXT NULL
  channel TEXT NOT NULL
  rendered_type TEXT NOT NULL    -- 'interactive' / 'text' / 'markdown' / 'template_card'
  recipient_summary TEXT NULL    -- 脱敏
  payload_digest TEXT NOT NULL   -- payload 摘要（非原文，避免泄敏）
  payload_bytes INTEGER NOT NULL
  delivery_status TEXT NOT NULL CHECK (delivery_status IN ('SUCCESS','PARTIAL','FAILED',
        'RATE_LIMITED','SKIPPED','PENDING'))
  response_code TEXT NULL
  response_body JSONB NULL
  invalid_recipients TEXT[] NULL
  retry_count INTEGER NOT NULL DEFAULT 0
  latency_ms INTEGER NULL
  is_test BOOLEAN NOT NULL DEFAULT FALSE
  is_backfill BOOLEAN NOT NULL DEFAULT FALSE
  error JSONB NULL
  anomaly_event_id TEXT NULL
  signal_id TEXT NULL
  job_run_id TEXT NULL
```

```sql
CREATE INDEX idx_notif_created ON notification_events (created_at DESC);
CREATE INDEX idx_notif_status  ON notification_events (delivery_status, created_at DESC);
CREATE INDEX idx_notif_fp      ON notification_events (fingerprint, revision);
```

### `notification_dead_letters`

```sql
PRIMARY KEY (dead_letter_id)
  notification_event_id TEXT NOT NULL REFERENCES notification_events(notification_event_id)
  channel TEXT NOT NULL
  reason TEXT NOT NULL
  payload_ref TEXT NOT NULL     -- 指向可重放的 payload 存储
  retryable BOOLEAN NOT NULL
  created_at TIMESTAMPTZ NOT NULL
  resolved_at TIMESTAMPTZ NULL
  resolution TEXT NULL
```

### `notification_preferences`

```sql
PRIMARY KEY (id)          -- 单用户，固定 id='default'
  daily_quota INTEGER NOT NULL DEFAULT 60
  quiet_hours TEXT NOT NULL DEFAULT '00:00-07:00'
  critical_bypass_quiet BOOLEAN NOT NULL DEFAULT FALSE
  channel_enabled JSONB NOT NULL     -- {"feishu":true,"wecom_app":true,"wecom_bot":false}
  delivery_mode TEXT NOT NULL DEFAULT 'broadcast'
  severity_routing JSONB NOT NULL DEFAULT '{}'
  rule_overrides JSONB NOT NULL DEFAULT '{}'   -- 规则级限额与阈值覆盖
  updated_at TIMESTAMPTZ NOT NULL
```

### `notification_channel_state`

```sql
PRIMARY KEY (channel)
  token_bucket JSONB NOT NULL      -- {"tokens":n,"capacity":n,"refill_at":ts}
  access_token TEXT NULL           -- 企微 token 缓存
  access_token_expires_at TIMESTAMPTZ NULL
  consecutive_failures INTEGER NOT NULL DEFAULT 0
  last_error JSONB NULL
  last_success_at TIMESTAMPTZ NULL
  health TEXT NOT NULL CHECK (health IN ('HEALTHY','DEGRADED','UNAVAILABLE','UNKNOWN'))
  updated_at TIMESTAMPTZ NOT NULL
```

> **必须持久化**（22.7 已说明）：限流状态跨重启保留，否则重启会瞬时打爆限流；企微 `access_token` 跨 worker 共享，避免重复 `gettoken` 触发频率限制（15.7.1 验收项）。

## 30.7 调度与系统

```sql
-- scheduled_jobs
PRIMARY KEY (job_id)
  job_type TEXT NOT NULL CHECK (job_type IN ('CRON','INTERVAL','EVENT','MANUAL'))
  cron_expr TEXT NULL
  interval_seconds INTEGER NULL
  timezone TEXT NOT NULL DEFAULT 'UTC'
  dependencies TEXT[] NOT NULL DEFAULT '{}'
  timeout_seconds INTEGER NOT NULL DEFAULT 600
  max_retries INTEGER NOT NULL DEFAULT 3
  max_backfill_window_seconds INTEGER NOT NULL DEFAULT 86400
  push_event_type TEXT NULL
  push_mode TEXT NOT NULL DEFAULT 'AGGREGATE'
  enabled BOOLEAN NOT NULL DEFAULT TRUE
  config JSONB NOT NULL DEFAULT '{}'
  updated_at TIMESTAMPTZ NOT NULL

-- job_runs
PRIMARY KEY (job_run_id)
  job_id TEXT NOT NULL REFERENCES scheduled_jobs(job_id)
  idempotency_key TEXT NOT NULL     -- {job_id}:{scheduled_at}
  scheduled_at TIMESTAMPTZ NOT NULL
  started_at TIMESTAMPTZ NOT NULL
  finished_at TIMESTAMPTZ NULL
  status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT',
        'SKIPPED_DEPENDENCY','MISSED','CANCELLED'))
  is_backfill BOOLEAN NOT NULL DEFAULT FALSE
  attempt INTEGER NOT NULL DEFAULT 1
  error JSONB NULL
  result JSONB NULL
  output_ref TEXT NULL

CREATE UNIQUE INDEX uq_job_runs_idem ON job_runs (idempotency_key);
```

> `idempotency_key` 的**唯一索引是幂等的物理保证**——同一时间窗并发触发时，第二个插入会失败并回退到读取已有结果（23.1.3）。

### `audit_logs` / `tool_invocations` / `system_events`

```sql
-- audit_logs：所有写操作
PRIMARY KEY (id)
  occurred_at TIMESTAMPTZ NOT NULL
  actor TEXT NOT NULL          -- 'user' / 'agent:{name}' / 'scheduler:{job_id}' / 'system'
  action TEXT NOT NULL
  resource_type TEXT NOT NULL
  resource_id TEXT NULL
  request JSONB NOT NULL
  response_summary JSONB NULL
  ip_hash TEXT NULL
  trace_id TEXT NULL

-- tool_invocations
PRIMARY KEY (invocation_id)
  agent_run_id TEXT NULL
  tool_name TEXT NOT NULL
  params JSONB NOT NULL
  result_summary JSONB NULL
  status TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','TIMEOUT','DENIED'))
  duration_ms INTEGER NOT NULL
  created_at TIMESTAMPTZ NOT NULL

-- system_events
PRIMARY KEY (id)
  occurred_at TIMESTAMPTZ NOT NULL
  category TEXT NOT NULL
  severity TEXT NOT NULL
  message TEXT NOT NULL
  context JSONB NOT NULL DEFAULT '{}'
```

## 30.8 迁移与版本

| 约定 | 规则 |
|---|---|
| 迁移工具 | **Alembic**（SQLAlchemy 生态），迁移脚本入 `migrations/`（19 章目录结构） |
| 迁移粒度 | 一次迁移 = 一个可回滚的逻辑变更；禁止在迁移中做数据搬运超过 100 万行（大表用分批） |
| 向后兼容窗口 | **不允许**「加列即删列」。删除列需经历：新增 → 双写 → 回填 → 停用 → 观察 ≥2 周 → 删除，共 6 步 |
| 迁移必须可逆 | 每个迁移必须有 `downgrade()`；CI 校验 |
| 生产库迁移 | 迁移前自动 `pg_dump` 备份；迁移失败自动回滚并保留现场 |
| `schema_version` | 存在 `config_versions` 表（11.6），记录每次迁移的应用版本 |

**`config_versions` 表**：

```sql
PRIMARY KEY (version_id)
  applied_at TIMESTAMPTZ NOT NULL
  app_version TEXT NOT NULL
  migration_revision TEXT NOT NULL
  config_hash TEXT NOT NULL
  notes TEXT NULL
```

## 30.9 完整表清单（回填第 11 章）

第 11 章是逻辑分类，本节是**权威清单**。22.7 与 23.3 新增的表已合并进来，各章不再单独作为真源。

| 分组 | 表 |
|---|---|
| 行情 | `market_candles`、`market_trades`、`orderbook_snapshots`、`market_tickers`、`funding_history`、`open_interest_history`、`instrument_metadata`、`market_data_quality_events`、`market_data_sync_runs` |
| 特征与快照 | `market_metrics`、`market_regimes`、`market_structure_features`、`sr_zones`、`market_snapshots`、`snapshot_data_refs` |
| AI 与研究 | `analysis_reports`、`analysis_feedback`、`agent_runs`、`agent_outputs`、`model_runs`、`prompt_versions`、`agent_versions`、`research_reports`、`historical_pattern_matches`、`strategy_experiments`、`experiment_results` |
| 信号与预测 | `signals`、`signal_events`、`signal_snapshots`、`prediction_observations`、`prediction_evaluations`、`analysis_evaluation_runs`、`evaluation_results` |
| 异动与通知 | `anomaly_rules`、`anomaly_events`、`anomaly_evaluations`、`notification_templates`、`notification_events`、`notification_dead_letters`、`notification_preferences`、`notification_channel_state` |
| 调度与系统 | `scheduled_jobs`、`job_runs`、`job_locks`、`audit_logs`、`system_events`、`tool_invocations`、`llm_cost_records`、`config_versions` |
| 模拟交易（P1） | `paper_platform_accounts`、`paper_platform_configs`、`paper_submission_requests`、`paper_platform_orders`、`paper_platform_trades`、`execution_events`、`paper_ledger_entries`、`paper_reconciliation_runs`、`paper_reconciliation_diffs`、`paper_instance_health`、`paper_account_controls` |

**新增表**（v2.3）：`job_runs`、`job_locks`（原仅在 23.3 提及）、`notification_channel_state` 的字段级定义。

---

# 31. API 契约与错误码字典（v2.3 新增，P0）

第 12 章给出端点清单，第 22.8 / 23.3 又新增端点但未合并。本章是 **API 的唯一真源**，定义统一约定、分页、幂等、错误码。

## 31.1 基础约定

| 项 | 约定 |
|---|---|
| 前缀 | `/api/v1` |
| 内容类型 | `application/json; charset=utf-8` |
| 字段命名 | `snake_case` |
| 时间 | 输入输出均为 ISO-8601 UTC，格式 `2026-11-09T01:23:45Z`（秒精度） |
| 认证 | `Authorization: Bearer <token>`（34.3） |
| 请求 ID | 请求头 `X-Request-Id`，缺失则服务端生成并回传 |
| 追踪 ID | 请求头 `X-Trace-Id`，跨异步任务传递 |
| 版本 | 路径版本 `v1`；**破坏性变更才升版本**，加字段不算破坏性 |

## 31.2 统一响应封装

**成功**：

```json
{
  "data": { ... },
  "meta": {
    "request_id": "req_01J...",
    "trace_id": "tr_01J...",
    "generated_at": "2026-11-09T01:23:45Z"
  }
}
```

**分页成功**：

```json
{
  "data": [ ... ],
  "meta": {
    "request_id": "req_01J...",
    "pagination": {
      "limit": 50,
      "offset": 0,
      "total": 1284,
      "has_more": true,
      "next_cursor": "eyJ0cyI6MTc2..."
    }
  }
}
```

**失败**：

```json
{
  "error": {
    "code": "DATA_QUALITY_STALE",
    "message": "行情数据陈旧，拒绝生成分析",
    "details": { "symbol": "BTCUSDT", "last_update": "2026-11-09T01:20:00Z", "staleness_seconds": 225 },
    "retryable": false,
    "request_id": "req_01J..."
  }
}
```

| 字段 | 说明 |
|---|---|
| `code` | **稳定的机器可读错误码**，见 31.6。禁止用 HTTP 状态码或自由文本代替 |
| `message` | 人类可读描述，面向 UI 展示，**不保证稳定**，禁止用于程序判断 |
| `details` | 结构化上下文，按错误码定义字段 |
| `retryable` | 客户端是否可重试 |

## 31.3 分页

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 50 | 上限 500（`market_candles` 类大表上限 1000） |
| `offset` | int | 0 | 简单分页 |
| `cursor` | string | — | **游标分页**，用于深翻页与稳定排序 |

规则：

1. 列表端点**必须**支持 `cursor`，因为行情与事件类数据会持续追加，`offset` 分页在翻页过程中会重复或漏项。
2. `cursor` 是**不透明字符串**，服务端编码排序键（如 `(created_at, id)`）；客户端**不得**解析或构造。
3. 响应中 `has_more=false` 时不再返回 `next_cursor`。
4. 默认排序**必须**是确定性的（带唯一 tiebreaker），否则分页结果不可复现。

## 31.4 幂等

**适用范围**：`POST` 中创建资源或触发动作的端点，尤其是分析运行、信号提交、模拟下单、通知重发、任务手动触发。

| 机制 | 约定 |
|---|---|
| 幂等键头 | `Idempotency-Key: <client-generated-uuid>` |
| 服务端键 | 对**触发类**端点由服务端派生（如 `analysis_runs` → `{job}:{snapshot_id}:{trigger}`），此时客户端头可选 |
| 保留期 | 24 小时 |
| 重复请求 | 返回**首次的完整响应**，HTTP `200`（而非 `201`），响应头带 `Idempotent-Replayed: true` |
| 键冲突 | 同一键但请求体不同 → `409`，错误码 `IDEMPOTENCY_KEY_REUSED` |

> 23.1.3 的任务幂等键 `{job_id}:{scheduled_at}` 与本节是两套键：任务级由调度器持有，API 触发的手动运行用 API 幂等键，二者最终都落到 `job_runs` 表的唯一索引上（30.7）。

## 31.5 长任务

耗时超过 2 秒的操作一律异步：

```json
POST /api/v1/analysis/runs
→ 202 Accepted
{
  "data": {
    "run_id": "run_01J...",
    "status": "PENDING",
    "status_url": "/api/v1/analysis/runs/run_01J...",
    "stream_url": "/api/v1/analysis/runs/run_01J.../stream"
  }
}
```

状态查询：`GET /api/v1/analysis/runs/{run_id}` → `status ∈ {PENDING, RUNNING, SUCCESS, FAILED, TIMEOUT}`。

**实时进度（可选）**：通过 SSE（`text/event-stream`）推送，事件类型 `status` / `agent_completed` / `log` / `done`。SSE 不可用时降级为轮询 `GET .../status`。

## 31.6 错误码字典（权威）

格式：`{DOMAIN}_{REASON}`，全大写，下划线分隔。**新增错误码必须在此登记**，禁止在代码中临时造码。

### 通用（`COMMON_*`）

| 码 | HTTP | 含义 | retryable |
|---|---|---|---|
| `COMMON_BAD_REQUEST` | 400 | 请求格式或参数非法 | false |
| `COMMON_VALIDATION_FAILED` | 422 | 字段级校验失败，`details.errors[]` 给出字段与原因 | false |
| `COMMON_UNAUTHORIZED` | 401 | 未认证或 token 无效 | false |
| `COMMON_FORBIDDEN` | 403 | 已认证但无权访问该资源/工具 | false |
| `COMMON_NOT_FOUND` | 404 | 资源不存在 | false |
| `COMMON_CONFLICT` | 409 | 状态冲突（如重复开仓、状态机非法跃迁） | false |
| `COMMON_IDEMPOTENCY_KEY_REUSED` | 409 | 同一幂等键，请求体不同 | false |
| `COMMON_RATE_LIMITED` | 429 | 本服务限流（`Retry-After` 头给出秒数） | true |
| `COMMON_INTERNAL_ERROR` | 500 | 未预期错误，`details.trace_id` 用于排障 | true |
| `COMMON_SERVICE_UNAVAILABLE` | 503 | 依赖不可用（DB/Redis） | true |
| `COMMON_TIMEOUT` | 504 | 处理超时 | true |

### 行情与数据（`DATA_*`）

| 码 | HTTP | 含义 |
|---|---|---|
| `DATA_NOT_FOUND` | 404 | 指定时间段无数据 |
| `DATA_QUALITY_STALE` | 409 | 数据陈旧，拒绝生成依赖该数据的分析 |
| `DATA_QUALITY_MISSING` | 409 | 数据缺失，拒绝生成依赖该数据的分析 |
| `DATA_QUALITY_INVALID` | 409 | 数据非法（OHLC 关系错误等） |
| `DATA_GAP_DETECTED` | 409 | 检测到 K 线缺口 |
| `DATA_RATE_LIMITED` | 429 | 交易所限流 |
| `DATA_SOURCE_DIVERGENCE` | 409 | 多源/多接口价格不一致 |
| `DATA_TIMESTAMP_INVALID` | 400 | 时间区间非法（如 end < start、跨度超限） |

### 分析与模型（`ANALYSIS_*` / `LLM_*`）

| 码 | HTTP | 含义 |
|---|---|---|
| `ANALYSIS_NOT_FOUND` | 404 | |
| `ANALYSIS_SNAPSHOT_MISSING` | 409 | 快照不存在或已过期 |
| `ANALYSIS_SKIPPED_DEPENDENCY` | 409 | 依赖任务未成功 |
| `LLM_UNAVAILABLE` | 503 | 模型服务不可用（可降级） |
| `LLM_RATE_LIMITED` | 429 | 模型限流 |
| `LLM_TIMEOUT` | 504 | 模型调用超时 |
| `LLM_SCHEMA_INVALID` | 502 | 模型输出不符合 Schema，重试耗尽 |
| `LLM_BUDGET_EXCEEDED` | 429 | 超出日预算硬上限（可降级） |
| `LLM_CONTENT_REJECTED` | 400 | 输入被内容策略拒绝 |

### 异动与通知（`ANOMALY_*` / `NOTIFY_*`）

| 码 | HTTP | 含义 |
|---|---|---|
| `ANOMALY_RULE_NOT_FOUND` | 404 | |
| `ANOMALY_RULE_VERSION_INVALID` | 400 | |
| `ANOMALY_SCAN_IN_PROGRESS` | 409 | 已有扫描任务在跑 |
| `NOTIFY_CHANNEL_REQUIRED` | 400 | 缺 `channel` 参数（31.9） |
| `NOTIFY_CHANNEL_DISABLED` | 409 | 渠道未启用 |
| `NOTIFY_CHANNEL_UNAVAILABLE` | 503 | 渠道健康检查失败 |
| `NOTIFY_CHANNEL_UNKNOWN` | 400 | 未知渠道名 |
| `NOTIFY_QUOTA_EXCEEDED` | 429 | 账户级配额耗尽 |
| `NOTIFY_QUIET_HOURS` | 409 | 处于免打扰时段 |
| `NOTIFY_DEDUPLICATED` | 200 | 被去重层拦截（**非错误**，返回 `delivery_status=SKIPPED`） |
| `NOTIFY_QUOTA_COUNTING_MODE` | 400 | 配额计数模式非法 |

### 预测与评估（`PRED_*` / `EVAL_*`）

| 码 | HTTP | 含义 |
|---|---|---|
| `PRED_NOT_FOUND` | 404 | |
| `PRED_ALREADY_EVALUATED` | 409 | 已评估，重复评估被拒 |
| `PRED_IMMUTABLE` | 409 | 试图修改已创建的预测内容（5.1 禁止） |
| `EVAL_INSUFFICIENT_SAMPLE` | 200 | 样本不足（**非错误**，返回 `is_insufficient=true`） |
| `EVAL_RUN_IN_PROGRESS` | 409 | |
| `EVAL_LOOKAHEAD_VIOLATION` | 409 | 检测到未来数据泄漏，评估中止 |

### 模拟交易（`PAPER_*` / `RISK_*` / `FT_*`）

| 码 | HTTP | 含义 |
|---|---|---|
| `PAPER_ACCOUNT_NOT_FOUND` | 404 | |
| `PAPER_ACCOUNT_PAUSED` | 409 | 账户已暂停 |
| `PAPER_ORDER_NOT_FOUND` | 404 | |
| `PAPER_RECON_DIFF_UNRESOLVED` | 409 | 存在未解释对账差异，阻断新开仓（7.8） |
| `PAPER_DRY_RUN_VIOLATION` | 500 | Freqtrade 非 dry-run，**拒绝启动**（7.7） |
| `RISK_LIMIT_EXCEEDED` | 409 | 单笔风险/杠杆/日亏损限制 |
| `RISK_SERVICE_UNAVAILABLE` | 503 | 风控服务不可用，拒绝新开仓 |
| `RISK_HALTED` | 409 | 已进入风控暂停态 |
| `FT_ADAPTER_TIMEOUT` | 504 | Freqtrade 超时（**须先查询状态，不得盲目重试**，7.8） |
| `FT_INSTANCE_DOWN` | 503 | Freqtrade 实例不可用 |

## 31.7 完整端点目录（合并第 12、22.8、23.3 章）

### 行情

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/markets` | 支持的交易对与市场类型列表 |
| GET | `/api/v1/markets/{symbol}/candles` | K 线；参数 `market_type`、`timeframe`、`start`、`end`、`limit` |
| GET | `/api/v1/markets/{symbol}/overview` | 价格、涨跌、状态、数据质量摘要 |
| GET | `/api/v1/markets/{symbol}/indicators` | 指标；参数 `timeframe`、`names`、`start` |
| GET | `/api/v1/markets/{symbol}/structure` | 结构与关键价位 |
| GET | `/api/v1/markets/{symbol}/derivatives` | OI、Funding、Basis |
| GET | `/api/v1/markets/{symbol}/liquidity` | 盘口摘要与流动性特征 |
| GET | `/api/v1/markets/{symbol}/quality` | 数据质量状态与事件 |

### AI 分析

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/analysis/runs` | 触发分析（异步，幂等） |
| GET | `/api/v1/analysis/runs/{run_id}` | 运行状态与结果 |
| GET | `/api/v1/analysis/runs/{run_id}/stream` | SSE 进度 |
| GET | `/api/v1/analysis/latest` | 最近一次分析（可按 symbol/market_type 过滤） |
| GET | `/api/v1/analysis/{analysis_id}` | 分析报告详情（4.7.1，含 Agent 输出与反馈摘要） |
| POST | `/api/v1/analysis/{analysis_id}/feedback` | 追加用户反馈或授权评审，支持幂等键 |
| GET | `/api/v1/agents/performance` | Agent 维度评估（口径见第 33 章） |

### 研究与预测

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/research/patterns` | 相似行情检索 |
| POST | `/api/v1/research/experiments` | 创建实验（异步） |
| GET | `/api/v1/research/experiments` | 实验列表 |
| GET | `/api/v1/research/experiments/{id}` | 实验详情与结果 |
| GET | `/api/v1/predictions` | 预测列表（按状态/时间/资产筛选） |
| GET | `/api/v1/predictions/{prediction_id}` | 预测详情 + 观察序列 + 评估结果 |
| GET | `/api/v1/evaluations/summary` | 评估汇总（双轨并排） |
| GET | `/api/v1/evaluations/by-agent` | 按 Agent 分组 |
| POST | `/api/v1/evaluations/run` | 触发评估批次（异步） |

### 回测

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/backtests` | 创建回测（异步，幂等） |
| GET | `/api/v1/backtests/{backtest_id}` | 状态 |
| GET | `/api/v1/backtests/{backtest_id}/results` | 结果 |

### 异动

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/anomalies/rules` | 规则列表（含版本） |
| PUT | `/api/v1/anomalies/rules/{rule_id}` | **创建新版本**（不改旧版本，见 30.6） |
| GET | `/api/v1/anomalies/events` | 异动事件列表 |
| GET | `/api/v1/anomalies/events/{id}` | 单事件详情（含各 revision 的投递记录） |
| POST | `/api/v1/anomalies/events/{event_id}/feedback` | 追加有用/噪声反馈；支持幂等键 |
| POST | `/api/v1/anomalies/scan` | 手动触发扫描 |
| GET | `/api/v1/anomalies/evaluations` | 异动后表现统计（规则有效率） |

### 通知

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/notifications/test` | 测试消息，**必须带 `channel`**（31.9） |
| POST | `/api/v1/notifications/test/broadcast` | 按投递模式多渠道测试 |
| GET | `/api/v1/notifications/events` | 投递记录列表 |
| GET | `/api/v1/notifications/events/{id}` | 单条详情（含重试历史） |
| POST | `/api/v1/notifications/events/{id}/retry` | 重试（手动） |
| GET | `/api/v1/notifications/dead-letters` | 死信列表 |
| POST | `/api/v1/notifications/dead-letters/{id}/redrive` | 死信重发 |
| GET | `/api/v1/notifications/preferences` | 推送偏好 |
| PUT | `/api/v1/notifications/preferences` | 更新偏好 |
| GET | `/api/v1/notifications/channels` | 渠道状态、健康、限流余量 |
| POST | `/api/v1/notifications/channels/{channel}/precheck` | 前置自检（22.5.7） |

### 调度

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/jobs` | 任务定义列表 |
| GET | `/api/v1/jobs/{job_id}/runs` | 运行历史 |
| POST | `/api/v1/jobs/{job_id}/trigger` | 手动触发（走同一幂等逻辑，23.1.4） |
| GET | `/api/v1/jobs/{job_id}/health` | 任务健康与连续失败计数 |

### 模拟交易（P1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/paper/accounts` | |
| GET | `/api/v1/paper/accounts/{id}/status` | |
| POST | `/api/v1/paper/accounts/{id}/orders` | 幂等提交 |
| GET | `/api/v1/paper/accounts/{id}/orders/{order_id}` | |
| GET | `/api/v1/paper/accounts/{id}/positions` | |
| POST | `/api/v1/paper/accounts/{id}/positions/{pid}/close` | |
| POST | `/api/v1/paper/accounts/{id}/pause` / `resume` | |
| GET | `/api/v1/paper/accounts/{id}/events` | |
| POST | `/api/v1/paper/reconciliation/run` | |

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针（不鉴权） |
| GET | `/ready` | 就绪探针（依赖检查，不鉴权） |
| GET | `/metrics` | Prometheus 指标（不鉴权，仅内网） |
| GET | `/api/v1/system/events` | 系统事件 |
| GET | `/api/v1/audit/logs` | 审计日志（需鉴权） |

## 31.8 鉴权与公开端点

| 端点 | 是否需要鉴权 |
|---|---|
| `/health`、`/ready`、`/metrics` | **否**（但必须只绑定内网/本地，34.5） |
| `/api/v1/**` | **是**（34.3） |
| 企微回调 URL | **是**（签名校验 + 一次性 token） |

## 31.9 测试接口的特殊约定

`POST /api/v1/notifications/test`：

| 规则 | 说明 |
|---|---|
| `channel` 必填 | 缺失 → `400 NOTIFY_CHANNEL_REQUIRED` |
| 可选 `recipient` | 仅 `wecom_app` 支持，覆盖 `WECOM_TO_USER` |
| 绕过配额与冷却 | 保证验收可执行；但写入 `notification_events` 且 `is_test=true` |
| 不产生 `anomaly_events` | 不占用 `cooldown_key`、不计入规则级限额 |
| 响应 | 返回各渠道的 `delivery_status`、响应码、耗时 |

## 31.10 版本化与弃用

| 变更类型 | 处理 |
|---|---|
| 新增可选字段 | 直接发布，不升版本 |
| 新增必填字段 | 升次版本号（`v1.1`），旧客户端仍可用 |
| 删除字段 / 改语义 / 改类型 | 升主版本（`v2`），旧版本保留 30 天并返回 `Deprecation` 头 |
| 错误码语义变更 | **视为破坏性变更**，必须升版本 |

---

# 32. 配置体系与环境变量全集（v2.3 新增，P0）

v2.2 只在 22.4.6 和 24.3 给出了零散的环境变量，没有统一约定，也没有说明配置与 `config_versions` 表的关系。本章建立配置的唯一真源。

## 32.1 配置层级与优先级

```text
默认值（代码内 Pydantic Settings 默认值）
   ↓ 被覆盖
.env / .env.local（本地，不入库）
   ↓ 被覆盖
环境变量（Docker Compose / 宿主机）
   ↓ 被覆盖
API 运行时覆盖（notification_preferences 等用户可调项）
```

**优先级规则（从低到高）**：

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1（最低） | 代码内默认值 | 所有可配置项**必须有**默认值，系统在零配置下可启动 |
| 2 | `.env` | 本地开发配置，`.gitignore` 排除 |
| 3 | 环境变量 | Docker Compose `environment` 注入 |
| 4 | 数据库中的用户偏好 | 仅 `notification_preferences` 这类**用户可调**项；不可调项（如 API 密钥、阈值基线）**不允许**被数据库覆盖 |

> **硬约束**：**安全相关项（密钥、token、白名单、dry-run 开关）只能来自环境变量，不允许数据库或 API 覆盖**。否则会出现「Dashboard 改一个字段就解除了安全限制」的事故路径。

## 32.2 配置文件结构

```text
.env.example          # 全部配置项的模板，含注释与默认值（入库）
.env                  # 本地实际配置（不入库）
config/
  base.toml           # 非敏感的结构化配置（阈值、窗口、频率）
  override.local.toml # 本地覆盖（不入库）
```

**为什么分 `.env` 与 `.toml`**：

- `.env` 存**机密**（密钥、token、Webhook URL），只做「存在性」判断，不承载复杂结构；
- `.toml` 存**结构化非机密配置**（异动阈值、通知配额、模型档位映射、窗口长度），支持嵌套与校验。

`.env.example` 必须包含**全部**配置项（敏感项留空并注释），这是「配置项可枚举」这一要求的落地形式（32.5）。

## 32.3 加载与校验

| 规则 | 说明 |
|---|---|
| 加载时机 | 进程启动时一次性加载；配置**不在运行时热加载**（除 `notification_preferences`） |
| 校验 | 全部配置经 Pydantic `Settings` 校验；**校验失败必须阻止进程启动**，不得降级为默认值 |
| 必需项缺失 | 明确报出缺失的键名，而不是静默用默认值 |
| 敏感项检查 | 启动时打印**脱敏后**的配置摘要；含密钥的项只显示 `***` |
| 格式校验 | 数值范围、枚举值、正则（如 cron 表达式）均在启动时校验 |
| 配置快照 | 启动时把生效配置（脱敏）写入 `config_versions` 表，`config_hash` 为其哈希 |

## 32.4 环境变量全集

### 应用基础

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `local` | `local` / `staging` / `prod` |
| `APP_TIMEZONE` | `Asia/Shanghai` | **用户时区**：免打扰时段、日报时间、定时任务的用户语义时间（与 22.4.4.2 一致） |
| `APP_HOST` | `127.0.0.1` | 后端绑定地址。**默认仅回环**，见 34.5 |
| `APP_PORT` | `8000` | |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `json` / `console` |
| `DATABASE_URL` | — | **必需**，PostgreSQL 连接串 |
| `REDIS_URL` | — | **必需**，Redis 连接串 |
| `FRONTEND_URL` | `http://localhost:3000` | 用于生成推送中的 Dashboard 链接 |
| `DASHBOARD_PUBLIC_BASE_URL` | — | **短链基准地址**。公网可达时必须设置为真实地址（22.5.4 要求短链） |

### 鉴权（第 34 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | `true` | |
| `AUTH_TOKEN` | — | **必需**（`AUTH_ENABLED=true` 时）。Bearer token |
| `AUTH_TOKEN_SHA256` | — | 可选：只存哈希，进程启动时比对 |
| `AUTH_RATE_LIMIT_PER_MIN` | `10` | 登录/失败尝试限流 |
| `PUBLIC_CALLBACK_ENABLED` | `false` | 企微回调的一次性公网入口（34.6） |
| `PUBLIC_CALLBACK_PORT` | `8081` | |
| `PUBLIC_CALLBACK_TOKEN` | — | 启用时**必需** |

### 行情采集（第 29 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MARKET_SYMBOLS` | `BTCUSDT,ETHUSDT` | |
| `MARKET_TYPES` | `SPOT,USDM_FUTURES` | |
| `MARKET_TIMEFRAMES` | `5m,15m,1h,4h` | `1m` 为可选（29.2.1） |
| `MARKET_ENABLED_INTERVALS_1M` | `false` | 启用 1m 则相关异动规则才启用 |
| `MARKET_WS_RECONNECT_MAX_MS` | `30000` | 退避上限 |
| `MARKET_OI_POLL_SECONDS` | `60` | OI 轮询间隔（29.4.3） |
| `MARKET_CROSSCHECK_SECONDS` | `300` | WS/REST 交叉核对间隔 |
| `MARKET_BACKFILL_ENABLED` | `true` | |
| `MARKET_BACKFILL_MAX_KLINES` | `1000` | 单页上限（27.2） |
| `MARKET_BOOK_SNAPSHOT_INTERVAL_SECONDS` | `60` | 盘口快照频率 |
| `MARKET_BOOK_DEPTH_LEVELS` | `20` | 只存 top20 |
| `MARKET_FUNDING_POLL_SECONDS` | `600` | 资金费率历史轮询间隔 |
| `MARKET_TRADE_RETENTION_DAYS` | `90` | 聚合成交保留天数（27.3） |
| `CLOCK_SYNC_INTERVAL_SECONDS` | `600` | 时钟校准（29.8） |
| `CLOCK_SKEW_ALERT_MS` | `5000` | 偏移告警阈值 |

### 异动与规则（第 22 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ANOMALY_SCAN_INTERVAL_SECONDS` | `60` | `anomaly_scan` 频率 |
| `ANOMALY_RULE_CONFIG_PATH` | `config/base.toml` | 阈值配置 |
| `ANOMALY_EVAL_HORIZONS` | `1h,4h` | 异动后表现评估窗口 |
| `ANOMALY_RULES_DISABLED` | — | 逗号分隔的规则 ID，用于临时停用 |

### 通知（第 22.4 / 22.5 节）

| 变量 | 默认 | 说明 |
|---|---|---|
| `FEISHU_ENABLED` | `false` | |
| `FEISHU_WEBHOOK_URL` | — | `FEISHU_ENABLED=true` 时必需 |
| `FEISHU_SECRET` | — | 签名校验时必需 |
| `WECOM_APP_ENABLED` | `false` | |
| `WECOM_CORP_ID` | — | |
| `WECOM_CORP_SECRET` | — | |
| `WECOM_AGENT_ID` | — | |
| `WECOM_TO_USER` | — | 逗号或 `\|` 分隔 |
| `WECOM_TO_PARTY` | — | |
| `WECOM_TO_TAG` | — | |
| `WECOM_APP_MSG_TYPE` | `text` | `text` / `markdown`（22.5.4） |
| `WECOM_BOT_ENABLED` | `false` | |
| `WECOM_BOT_WEBHOOK_URL` | — | |
| `NOTIFY_DAILY_QUOTA` | `60` | 账户级（22.4.4.2） |
| `NOTIFY_QUIET_HOURS` | `00:00-07:00` | **按 `APP_TIMEZONE`** 解释 |
| `NOTIFY_CRITICAL_BYPASS_QUIET` | `false` | |
| `NOTIFY_CHANNEL_PRIORITY` | `feishu,wecom_app` | |
| `NOTIFY_DELIVERY_MODE` | `broadcast` | `broadcast` / `first_success` / `severity_routing` |
| `NOTIFY_SEVERITY_ROUTING` | — | 如 `CRITICAL:wecom_app,INFO:feishu` |
| `NOTIFY_QUOTA_COUNTING_MODE` | `per_event` | 多渠道是否重复计数（22.4.4.2） |
| `NOTIFY_DEDUP_TTL_SECONDS` | `86400` | 指纹去重 TTL |
| `NOTIFY_COOLDOWN_CRITICAL_SECONDS` | `300` | |
| `NOTIFY_COOLDOWN_WARN_SECONDS` | `1800` | |
| `NOTIFY_MAX_RETRIES` | `4` | 22.4.5 |
| `NOTIFY_RETRY_BACKOFF` | `1s,3s,10s,30s` | |
| `NOTIFY_AI_MERGE_WINDOW_SECONDS` | `30` | 首投与 AI 解读的合并等待窗口（22.4.4.1 规则 5） |
| `NOTIFY_WECOM_TOKEN_REFRESH_MARGIN_SECONDS` | `300` | token 提前刷新余量 |

### LLM（第 24 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER_DEFAULT` | `cloud` | `cloud` / `local` |
| `LLM_CLOUD_BASE_URL` | — | |
| `LLM_CLOUD_API_KEY` | — | |
| `LLM_PROVIDER_LOCAL_BASE_URL` | `http://ollama:11434/v1` | |
| `LLM_PROVIDER_LOCAL_MODEL` | — | |
| `LLM_PROVIDER_LOCAL_API_KEY` | `ollama` | |
| `LLM_TIER_LIGHT_MODEL` | — | |
| `LLM_TIER_STANDARD_MODEL` | — | |
| `LLM_TIER_DEEP_MODEL` | — | |
| `LLM_DAILY_TOKEN_BUDGET` | — | 硬上限 |
| `LLM_DAILY_COST_BUDGET_USD` | — | 硬上限 |
| `LLM_MAX_INPUT_TOKENS` | `24000` | 24.2 |
| `LLM_MAX_OUTPUT_TOKENS` | `4000` | |
| `LLM_AGENT_MAX_TOKENS` | `8000` | |
| `LLM_TIMEOUT_SECONDS` | `60` | |
| `LLM_MAX_RETRIES` | `2` | |
| `LLM_SCHEMA_REPAIR_ATTEMPTS` | `1` | 输出不符合 Schema 时的修复重试次数（见下） |
| `LLM_ANALYSIS_TOTAL_BUDGET_SECONDS` | `180` | 全流程超时预算（24.4） |

> **`LLM_SCHEMA_REPAIR_ATTEMPTS` 是 v2.3 补充的缺口**：v2.2 规定「响应校验」（10.1）但未说校验失败怎么办。v2.3 规定：Schema 校验失败时，把**校验错误信息**回灌给模型做一次修复（不重传原始上下文，只传输出 + 错误），仍失败则该 Agent 标记 `FAILED`/`SKIPckipped_DEGRADED` 并在 Master 汇总中显式标注该 Agent 缺失，**不得伪造输出**。

### 调度（第 23 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | |
| `SCHEDULER_TIMEZONE` | `UTC` | **cron 一律按 UTC 解释**（23.1.1） |
| `SCHEDULER_MAX_BACKFILL_SECONDS` | `86400` | 补跑上限 |
| `SCHEDULER_LOCK_MODE` | `postgres_advisory` | 单机可设 `local` |
| `SCHEDULER_WORKER_POOL` | `4` | 全局并发上限 |

### Freqtrade（P1）

| 变量 | 默认 | 说明 |
|---|---|---|
| `FREQTRADE_ENABLED` | `false` | |
| `FREQTRADE_API_BASE_URL` | — | 仅 Docker 内网可达 |
| `FREQTRADE_API_USERNAME` / `_PASSWORD` | — | |
| `FREQTRADE_DRY_RUN_ENFORCE` | `true` | **启动时强制校验 `dry_run=true`**，否则拒绝启动（7.7） |
| `FREQTRADE_IMAGE_TAG` | — | **必须固定版本**，禁止 `latest` |

### 可观测性（第 37 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `METRICS_ENABLED` | `true` | |
| `METRICS_BIND` | `127.0.0.1:9100` | |
| `METRICS_AUTH_REQUIRED` | `false` | |
| `LOG_SAMPLING_RATE` | `1.0` | 日志采样率 |

### 备份与保留（第 27 章）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKUP_ENABLED` | `true` | |
| `BACKUP_DIR` | `/data/backups` | |
| `BACKUP_RETENTION_DAYS` | `14` | |
| `BACKUP_RESTORE_DRILL_ENABLED` | `true` | 季度恢复演练 |
| `RETENTION_ENABLED` | `true` | 数据保留清理任务 |

## 32.5 配置项可枚举性

**要求**：任何新配置项必须同时出现在：

1. `config/base.toml`（非敏感）或 `.env.example`（敏感）；
2. 本章对应小节的表格中；
3. 代码中的 Pydantic Settings 模型。

**CI 校验（36.4）**：提供一个测试，遍历 Settings 模型的所有字段，断言每一项都在本章表格中出现（通过解析本章 Markdown 表格或维护一份机器可读的 `config/catalog.toml`）。

> 推荐实现：维护 `config/catalog.toml` 作为**机器可读的配置目录**（键名、类型、默认值、是否敏感、章节），本章表格由人工维护但 CI 校验二者一致。避免「文档漏写一项」这类最常见也最容易被忽略的债。

## 32.6 敏感信息处理

| 规则 | 说明 |
|---|---|
| 存储 | 只在 `.env` / 环境变量，**不入库、不入仓库** |
| 日志 | 全部敏感值在日志中掩码：`https://open.feishu.cn/.../hook/****`、`key=****`、`corpsecret=****` |
| Dashboard | 展示时脱敏；企微 `CorpSecret` **永不回显**，飞书 URL 只显示前后 6 位 |
| API | 任何接口不回显密钥；`GET /api/v1/notifications/channels` 只返回「已配置/未配置」与掩码值 |
| 错误堆栈 | 异常信息中的 URL 也要掩码，避免出现在 traceback |
| 仓库 | `.env` 必须列入 `.gitignore`；`.env.example` 只含空值 |

## 32.7 配置变更与版本

| 变更 | 处理 |
|---|---|
| 修改 `.env` | 需重启对应服务；启动时写入 `config_versions` 快照 |
| 修改 `config/base.toml` | 同上；`config_hash` 变化 |
| 修改数据库中的用户偏好 | 立即生效，`notification_preferences.updated_at` 更新 |
| 阈值变更 | 走 `anomaly_rules` 版本化流程（30.6），**不改配置文件**，因为阈值需要可回溯复现 |

> **重要区分**：**技术配置**（连接、密钥、端口）走 `.env`；**业务参数**（异动阈值、配额）走数据库版本化表。理由是业务参数需要「修改可追溯 + 历史可复现」，而技术配置不需要。混用会导致「改了一个阈值，历史异动无法复现」。

---

# 33. 评估指标数学口径（v2.3 新增，P0）

第 5.2 章给出指标清单但没有数学定义。**评估口径就是评估系统本身**——口径不唯一，实现者会各自发明算法，评估结论随之失去意义。本章是指标口径的唯一真源。

## 33.1 通用定义与符号

| 符号 | 含义 |
|---|---|
| `t0` | 预测首次对用户可见时刻（`signals.published_at`）；创建时间与快照时间分别保存 |
| `P0` | **基准价格**，见 33.2 |
| `T` | 预测时间窗口（`signals.prediction_window`），`[t0, t1]` |
| `t1` | 窗口终点 = `t0 + time_horizon` |
| `O(t)` | 时刻 `t` 的已收盘 K 线 OHLC |
| `C(t)` | 时刻 `t` 的收盘价 |
| `H(t)` / `L(t)` | 时刻 `t` 的最高/最低价 |
| `r(t)` | 从 `t0` 到 `t` 的收益率，`(C(t) - P0) / P0` |
| `Δt` | 时间粒度（评估周期，如 `15m`） |
| `N` | 样本数 |

**评估粒度**：常规观察序列采用 `15m` 已收盘 K 线；`t1 = t0 + time_horizon` 不因粒度改变。终点价取不晚于 `t1` 的最近已收盘 `1m` K 线收盘价，保存实际价格时刻；陈旧超过 60 秒或缺失时标记 `SKIPPED_NO_ENDPOINT`。端点取价误差需在报告披露。

路径指标只使用完整位于 `[t0,t1]` 内的 K 线。边界不足 15m 的部分用完整位于窗口内的 1m K 线补充，禁止将覆盖发布时间之前或截止时间之后的整根 K 线极值纳入。更细粒度仍无法确定的目标/止损先后顺序标记 `AMBIGUOUS`，不推断路径。

## 33.2 基准价格 P0 的定义

**这是最容易被实现者搞错的一项，必须唯一化。**

```text
P0 = 同一标的与市场中，published_at 当时已接收的最新有效成交价格
```

| 规则 | 说明 |
|---|---|
| 时间条件 | 成交的交易所时间与本地接收时间均不得晚于 `published_at`，不使用事后补到的价格 |
| 时效门槛 | 最新成交距发布时间不得超过 5 秒；门槛可配置并纳入 `evaluator_version` |
| 一次性确定并落库 | 保存 `reference_price`、`reference_price_at`、来源与接收时间引用；不可事后覆盖 |
| 不使用未收盘 K 线 | 最新成交是独立价格观测，不改变 29.5 的指标与异动仅使用已收盘 K 线约束 |
| 数据缺失或陈旧 | 标记 `SKIPPED_NO_BASE`，不进入指标分母，显式展示数量与覆盖率 |
| 快照研究基准 | 另存 `snapshot_reference_price`，可取快照所属周期最后已收盘价；仅用于单独的快照研究报告 |
| 历史兼容 | 旧版基准与新基准分口径展示；缺少发布时原始价格时不事后伪造新基准 |

> `P0` 与 `entry_zone` 是不同的东西：`entry_zone` 是「计划入场区间」（可能尚未触发），`P0` 是「评估基准价」。**评估用 `P0`，不用入场价**——否则「未触发」与「触发」样本被混为一谈。

**评价模式隔离（v2.4）**：实时主报告仅统计 `LIVE` 且发布时未见结果的预测。`BACKFILL` 与 `RETROSPECTIVE` 单独统计；历史输入不含未来行情仍不足以消除模型训练知识带来的污染。录制输出保证重跑一致性，不自动证明历史预测有效。报告必须分别显示快照截止、发布时间、分析延迟和模式。

## 33.3 方向评估

### 33.3.1 标签定义

窗口结束时，按 `r(t1)` 与阈值 `θ` 判定方向标签：

```text
θ = DIRECTION_THRESHOLD_PCT  (初始 0.30%，即 30 个基点)

r(t1) >  θ  →  'UP'
r(t1) < -θ  →  'DOWN'
否则          →  'FLAT'
```

**`θ` 的取值与依据**：

- BTC 的 15m 噪声水平约在 0.1%–0.2%，ETH 约 0.15%–0.3%；取 0.30% 作为初始统一值；
- **`θ` 必须按资产可覆盖**（`evaluation_results.group_key` 中区分），首轮用统一值，待样本 ≥ 200 条后按资产分别校准；
- `θ` 的变更必须升 `evaluator_version`（30.5），否则历史与新结果不可比。

> **为什么必须设阈值**：若 `θ = 0`，则任何微小波动（包括手续费级别的噪声）都会被计为一次「方向判断」，Direction Accuracy 会趋近 50% 且方差极大，失去区分能力。5.2 已提示「必须明确」，v2.3 给出初始值。

### 33.3.2 指标定义

设样本集 `S`（已到期、未被 `SKIPPED_*` 排除的预测），每个样本有预测方向 `d̂`（LONG/SHORT/NEUTRAL）与实际标签 `d`（UP/DOWN/FLAT）。

| 指标 | 定义 | 公式 |
|---|---|---|
| **Direction Accuracy** | 仅在二分类样本上计算（排除实际为 FLAT 或预测为 NEUTRAL 的样本） | `Acc = #{d̂ == d} / #{d ∈ {UP,DOWN} 且 d̂ ∈ {LONG,SHORT}}` |
| **Direction Coverage** | 参与二分类评估的样本占比 | `Cov = #{d ∈ {UP,DOWN} 且 d̂ ∈ {LONG,SHORT}} / \|S\|` |
| **Balanced Accuracy** | 缓解 UP/DOWN 不均衡 | `(Recall_UP + Recall_DOWN) / 2` |
| **三分类 Accuracy** | 全样本含 FLAT 的准确率 | `#{d̂ == d} / \|S\|`（`NEUTRAL` 预测 FLAT） |

> **口径要点**：Direction Accuracy 与 Coverage **必须成对展示**。只报 Accuracy 会掩盖「模型全部输出 NEUTRAL 导致 Coverage 极低」这一退化情形。样本不足时（见 33.6）两者均标 `INSUFFICIENT_SAMPLE`。

### 33.3.3 模糊带（Borderline）的处理

若 `|r(t1)| < θ`，该样本标记 `FLAT` 并**同时**进入三分类评估与一个附加统计 `borderline_rate`：

```text
borderline_rate = #{ |r(t1)| < θ } / |S|
```

报告展示 `borderline_rate`。较高比例可能来自真实低波动环境或阈值与周期不匹配，不能据此自动下调阈值。阈值按资产与预测窗口预先固定，仅在训练/校准区间调整，冻结后在独立窗口验证，禁止为改善测试成绩事后改口径。

## 33.4 概率与置信度

### 33.4.1 两个不同的量

| 字段 | 语义 | 来源 |
|---|---|---|
| `confidence` | 模型**自述**的主观置信度（4.7 已声明不等于统计胜率） | Agent 输出 |
| `probability` | 用于 Brier Score 的**显式概率** | Agent 输出的 `scenario_probabilities` 中的方向概率 |

**Brier Score 只能使用事件定义明确的显式概率，不能使用 `confidence`**。三分类使用 `scenario_probabilities`，辅助二分类使用按 33.4.2 定义的 `probability`。对应概率缺失时该指标为 `NULL`，不参与统计并报告覆盖率；仅有报告叙事情景、未映射到 UP/DOWN/FLAT 时不能直接计算方向 Brier。

> **口径要点**：把 `confidence` 当概率用是常见错误——`confidence` 是模型对「自己判断有多确定」的主观评分，其分布与实际命中率无关，直接代入 Brier 会得到无意义的分数。

### 33.4.2 Brier Score

主概率评价采用互斥完备的 `UP/DOWN/FLAT` 三分类概率，概率均在 `[0,1]` 且总和为 1：多分类 Brier = `mean(Σ_k (p_k - y_k)^2)`，范围 `[0,2]`。与仅用当时训练窗口估计的类别频率基线比较，不能使用测试期频率拟合基线。

二分类为辅助评价：`p = P(UP | UP 或 DOWN)`，由预测时保存的 `p_up / (p_up + p_down)` 得到；分母为零时不适用。实际 FLAT 不进入该条件指标，须同时显示排除数量与三分类主指标。不得直接将未归一化的 UP 概率用于条件二分类。

对上述二分类事件，概率 `p`：

```text
Brier = mean( (p - y)^2 )     其中 y ∈ {1, 0}
```

- 范围 `[0, 1]`，越小越好；恒定预测 0.5 时为 `0.25`。类别不平衡时，应比较预先固定的历史频率基线及 Brier Skill Score，不能把 0.25 当作通用有效门槛；
- 计算样本：`probability` 非空、实际标签为 UP/DOWN（FLAT 不计入）；
- **必须同时输出「Brier 样本覆盖率」**（参与计算的样本数 / 全部样本数）。

### 33.4.3 Calibration

分箱校准曲线。**分箱方式固定为等频分箱**：

```text
按 probability 排序 → 等分成 10 组（每组样本数尽量相等） →
每组计算：mean_predicted_probability, observed_frequency, count
```

- 输出：10 个箱的 `(mean_p, observed_freq, count)`；
- **可靠性指标**：Expected Calibration Error

```text
ECE = Σ (n_b / N) * |mean_p_b - observed_freq_b|
```

- ECE 越低越好；参考值：`< 0.05` 为良好；
- **必须输出每箱的 `count`**。某箱样本 < 10 时该箱标 `INSUFFICIENT_SAMPLE`，但不参与 ECE 计算（从分母剔除并说明）。

## 33.5 价格、区间、目标、风险

### 33.5.1 误差类

窗口终点与窗口内的极值：

| 指标 | 定义 | 公式 |
|---|---|---|
| **MAE** | 终点绝对误差 | `mean( \|C(t1) - P̂\| )`，`P̂` 为预测价格 |
| **RMSE** | 终点均方根误差 | `sqrt( mean( (C(t1) - P̂)^2 ) )` |
| **MAPE** | 终点平均绝对百分比误差 | `mean( \|C(t1) - P̂\| / P0 ) * 100` |
| **Endpoint Return Error** | 终点收益偏差（比 MAE 更贴近方向性） | `mean( r(t1) - r̂(t1) )` |

**MAE / RMSE 的口径要点**：

- 使用**终点**收盘价，不使用窗口内极值。窗口内极值属于 MFE/MAE 路径指标（33.5.3），两者不可混用；
- 若预测未给出点价格（只给了区间），则 MAE/RMSE **不计算**，标记 `NOT_APPLICABLE`；
- 价格单位为 USDT；**不做价格归一化**（避免不同价位资产混算时产生偏差）。

### 33.5.2 区间

| 指标 | 定义 | 口径要点 |
|---|---|---|
| **Interval Coverage** | 实际终点价落入预测区间的比例 | 区间为**预测时创建**的 `[lower, upper]`（存于 `signals`），**不得**事后按分位数重新构造 |
| **Interval Width (avg)** | 区间宽度占基准价的平均比例 | `mean( (upper - lower) / P0 )` |
| **Interval Sharpness** | 区间宽度与覆盖率的权衡 | 单独报告，不合成单一分数 |

> **强约束**：区间覆盖率**必须与区间宽度并排展示**。一个宽度为 ±20% 的区间可以有 99% 覆盖率，毫无信息量。单独的覆盖率数字会误导。

### 33.5.3 路径指标 MFE / MAE

窗口 `[t0, t1]` 内的最大有利/不利变动：

```text
MFE% = max over t in T of  ( (max(C(t), H(t)) - P0) / P0 )   for LONG
       max over t in T of  ( (P0 - min(C(t), L(t))) / P0 )   for SHORT

MAE% = max over t in T of  ( (P0 - min(C(t), L(t))) / P0 )   for LONG
       max over t in T of  ( (max(C(t), H(t)) - P0) / P0 )   for SHORT
```

**口径要点**：

- 使用 33.1 定义的窗口内 `15m` 与边界 `1m` K 线 `high`/`low`（含影线），禁止使用跨越窗口边界的整根极值；
- 对 `NEUTRAL` 方向预测，MFE/MAE 不计算（无方向可言），标 `NOT_APPLICABLE`；
- **必须同时给出观察窗口长度**（`time_horizon`），因为 MFE/MAE 随窗口长度单调增长，不可跨窗口比较。跨周期聚合时必须按 `time_horizon` 分组。

### 33.5.4 目标触达

| 指标 | 定义 | 口径要点 |
|---|---|---|
| **Target Hit Rate** | `take_profit_levels` 中**任一**目标在窗口内被触达的比例 | 触达判定用 `high >= target`（LONG）/ `low <= target`（SHORT），**盘中触达即算**，不要求收盘 |
| **Stop Hit Rate** | 止损被触达的比例 | `low <= stop`（LONG）/ `high >= stop`（SHORT），盘中触达即算 |
| **Time To Target** | 首次触达到目标的时长（占窗口比例） | 未触达为 `NULL` |

> 「盘中触达即算」与「收盘触达」会给出差异显著的结论。**本项目统一采用盘中触达**，因为模拟交易中的止盈也是盘中触达触发（7.9 需 PoC 验证），口径一致才能让轨道 A 与轨道 B 可比。

### 33.5.5 失效条件评估

`invalidation_condition` 是**自然语言或结构化条件**，无法直接数值化。实现方式：

| 判定方式 | 适用 | 说明 |
|---|---|---|
| **结构化条件**（优先） | 可数值化的失效条件 | 如 `close < support_level`、`atr_ratio > 2.5`、`regime_change_to != 原状态` |
| **文本条件**（兜底） | 不可数值化的条件 | 由 LLM 依据窗口内实际行情判定「该失效条件是否已发生」 |

| 指标 | 定义 |
|---|---|
| **Invalidation Accuracy** | 失效条件判定为「已发生」与「未发生」的准确率（文本条件的 LLM 判定需记录判定依据） |
| **Invalidation Precision** | 判定「已发生」的样本中，行情确实出现显著不利变动的比例 |

**口径要点**：

- `Invalidation Accuracy` 只有在存在独立真实标签（确定性条件或独立人工标注）时计算。没有真实标签时只记录条件是否触发及证据，不把 LLM 自评当作准确率；文本裁判与预测模型需分别记录。
- **结构化条件必须优先实现**。文本条件判定引入 LLM 不确定性，会污染评估结论；仅在无法结构化时使用文本判定，且**该部分的评估结果单独分组**，不与结构化条件混合统计；
- 文本条件判定记录 `judged_by='llm'` 与 `model_id`，允许按模型分组对比；
- `Invalidation Accuracy` 是**双向**指标：既要看「该失效的被识别出来」，也要看「不该失效的没被误判」。仅报告召回会鼓励过度触发失效。

### 33.5.6 机会评估

| 指标 | 定义 | 口径要点 |
|---|---|---|
| **Opportunity Trigger Rate** | 被标记机会中达到预定义触发条件的比例 | 只衡量条件触发，不代表机会质量 |
| **Opportunity Precision** | 已触发机会中达到预先固定的有利路径条件、且满足不利路径约束的比例 | 成功事件、观察窗口与阈值在创建时固定；成本后价值另由经济性轨道评价 |
| **Opportunity Recall** | 统一机会定义下满足成功事件的所有候选中，被提前标记的比例 | 必须先固定候选全集、触发和成功条件并全量扫描；候选全集未定义时为 NOT_APPLICABLE |
| **Lead Time** | 机会创建到条件达成的时长 | 未达成者不计入 |
| **Setup Trigger Rate** | `Trade Setup` 的 `entry_trigger` 被满足的比例 | 与 Opportunity Precision 分开统计 |

> `Opportunity Recall` 需要以「全量扫描」为分母，与 `Precision` 的「以机会为分母」不同，两者**不可互换**。绝大多数报告只报 Precision 会高估能力。

## 33.6 最小样本量与 `INSUFFICIENT_SAMPLE`

### 33.6.1 门槛

| 统计类型 | 最小样本量 `N_min` | 说明 |
|---|---|---|
| 比率类（Accuracy、Hit Rate、Coverage） | **30** | 低于此值只报计数，不报比率 |
| 方向 Accuracy（单组） | **50** | |
| Brier / ECE | **100** | 校准曲线每箱需 ≥10 |
| 经济性指标（Sharpe、Sortino、期望值） | **100** | |
| 最大回撤 | **50** | 路径依赖强，样本过少无意义 |
| 成本占比 | **50** | |
| 分组下钻（在上述门槛之上再分层） | **基础门槛 × 2** | 防止「20 条样本切成 4 组每组 5 条」 |

### 33.6.2 行为规则

| 条件 | 输出 |
|---|---|
| `N < N_min` | `is_insufficient=true`，`metric_value=NULL`，只显示 `sample_size` 与计数 |
| `N_min ≤ N < 2 × N_min` | 输出值，但**标注** `LOW_CONFIDENCE`，禁止排名 |
| `N ≥ 2 × N_min` | 正常输出，允许排名 |

**硬约束（5.4 已要求「不输出误导性排名」）**：

1. 任何排名列表，**样本不足的组一律不参与排名**，且在表中显示为 `—` 而非排在末位；
2. 排名**必须同时显示** `sample_size` 与 `is_insufficient` 标记；
3. `INSUFFICIENT_SAMPLE` 的组**不显示**任何百分比/比率数值，只显示原始计数。

### 33.6.3 多重比较问题

按 Agent × 模型 × Prompt × 周期 × 资产 × 状态分组后，组数会很多，**在大量组上取最优必然产生虚假冠军**。

| 规则 | 说明 |
|---|---|
| 比较依据 | 组数多少不决定可否排名；依据有效样本、置信区间、配对对照与独立时间窗口验证，未经验证只并列展示 |
| 赢家筛选 | 排名仅用于**候选筛选**，不作为结论；结论需在**独立时间窗口**上复核 |
| 多重比较提示 | 报告中标注「已比较 N 组，取最优存在选择偏差」 |
| 时间隔离 | 报告必须同时给出样本内/样本外两段（5.4 已要求） |

## 33.7 重叠预测窗口的去重

同一资产、同一周期、同一 Agent 的预测可能高度重叠，例如每小时生成一个 4h 预测，相邻窗口重叠 3h；每小时生成的固定 1h 预测通常相接，并不天然重叠。重叠与共同市场冲击均可能造成样本相关，去重不等于获得独立样本。

### 33.7.1 去重规则

| 规则 | 说明 |
|---|---|
| **分组键** | 在每个 Agent/模型/Prompt 评价组内，按 `(symbol, market_type, timeframe, time_horizon, analysis_mode)` 分组，不按预测方向拆分 |
| **判定重叠** | 交集时长 / 较短窗口时长 > 50% |
| **保留规则** | 按 `(published_at, signal_id)` 升序遍历；与已保留窗口满足重叠判定者标记 `EXCLUDED_OVERLAP`，其余保留；不淘汰此前保留的样本 |
| **对照一致性** | 配对模型实验使用共同的时点/窗口选择集合，避免不同方法挑选不同样本；全部样本仍保留供审计 |
| **记录** | 剔除决策必须记录在 `prediction_evaluations.detail.overlap_group` 与 `excluded_reason`，可审计 |

### 33.7.2 报告要求

所有比率类指标必须**同时**报告两个数字：

- `metric_value`：含全部样本；
- `metric_value_deduped`：按 33.7.1 去重后的值。

**主报告使用去重后的值**，但两个都必须展示。两者差异过大（> 10 个百分点）本身是重要信号，说明样本高度冗余。

### 33.7.3 与「按 Agent/模型/Prompt 分组」的关系

分组评估时，**在每个分组内**独立执行去重，而不是全局去重后再分组。

## 33.8 口径版本化

| 规则 | 说明 |
|---|---|
| 口径标识 | `evaluator_version`，语义化版本（如 `v1.2.0`） |
| 变更分类 | **破坏性**（改公式、改标签定义、改阈值 `θ`、改去重规则）→ 升主版本；**非破坏性**（新增指标、扩展示字段）→ 升次版本 |
| 记录 | `prediction_evaluations.evaluator_version` 逐条记录 |
| 报告 | 报告必须展示口径版本；**不同口径版本的结果不得混在一张排名表里** |
| 重算 | 口径破坏性变更后，历史预测**可批量重算**（预测内容未变，重算合法），但新旧结果必须分表展示 |

## 33.9 双轨经济性指标口径

轨道 B 的指标定义见 25.3，本章补充**数学口径**：

| 指标 | 定义 | 口径要点 |
|---|---|---|
| **净收益率** | `Σ(净盈亏) / 初始权益` | 净盈亏 = 毛盈亏 − 手续费 − Funding − 滑点成本 |
| **盈亏比** | `mean(盈利交易净盈亏) / \|mean(亏损交易净盈亏)\|` | 按**交易**计，不按持仓计 |
| **期望值** | `胜率 × 平均盈利 − 败率 × 平均亏损` | 使用**净**盈亏 |
| **成本占比** | `Σ成本 / Σ毛利` | 分母为 0 时标 `NOT_APPLICABLE` |
| **最大回撤** | 权益曲线 `max(peak - trough)/peak` 的最大值 | 基于**权益曲线**（含未实现盈亏）还是已实现，需固定：采用**逐笔已实现权益曲线**，并额外报告基于 `mark-to-market` 的版本 |
| **Sharpe** | `mean(r) / std(r)`，**按每笔交易收益**计算，**不年化** | 无风险利率取 0；`std` 用**样本标准差**（ddof=1） |
| **Sortino** | `mean(r) / downside_deviation`，分母只算负收益 | |
| **资金利用率** | `Σ(持仓保证金 × 时长) / (权益 × 总时长)` | 时间加权平均 |

**口径要点**：

1. **不年化**。样本量在个人系统下不足，年化会放大到无意义的数字；
2. Sharpe/Sortino **必须与 `sample_size` 同时展示**，且按 33.6 门槛判 `INSUFFICIENT_SAMPLE`；
3. 「判断对但亏钱」的识别：按 25.1 的四象限，**必须用轨道 A 的方向判定结果与轨道 B 的净盈亏做交叉表**，这是双轨评估的核心产出，不能只报两个独立数字。

## 33.10 评估计算的性能约束

评估是批量计算，必须可在后台完成：

| 约束 | 值 |
|---|---|
| 单次评估批次目标耗时 | 全量 30 天窗口 < 60 秒 |
| 超时 | 10 分钟，超时标记 `TIMEOUT`（23.1.4） |
| 增量 | 每次只评估**新到期**的预测 + 重算口径变更影响的窗口 |
| 幂等 | 批次幂等键 = `{scope_hash}:{window_start}:{window_end}:{evaluator_version}` |
| 无未来数据泄漏 | 评估在 `t1` 后运行；结果标签仅使用 `[t0,t1]` 内行情与已冻结基准，禁止窗口外极值进入指标。分析输入只使用快照截止前且当时实际可用的数据。分别测试事件时间、接收时间和窗口边界 |

---

# 34. 鉴权、访问控制与公网暴露边界（v2.3 新增，P0）

第 13.4 章要求「Dashboard 与内部服务使用鉴权」，但全文没有机制定义。同时存在一处**直接冲突**：28.2 的待办要求「验证阶段需要临时公网入口（可信 IP 配置的前置条件要求可验证 URL）」，而 13.4 要求「本地服务默认不暴露公网」。本章解决这个冲突。

## 34.1 威胁模型

单用户、本地部署的真实威胁源，与公网 SaaS 完全不同：

| 威胁 | 可能性 | 影响 | 缓解 |
|---|---|---|---|
| 本机其他用户/进程访问 | 中 | 中 | 绑定回环地址 + 令牌鉴权 |
| 局域网内他人访问 Dashboard | **高**（默认 0.0.0.0 绑定时） | 中 | 默认绑 `127.0.0.1`；显式暴露需开启鉴权 |
| 恶意网页发跨站请求（CSRF） | 中 | 中 | Bearer token 非 Cookie 存储 + 校验 Origin |
| 令牌泄漏（截图、日志、仓库） | 中 | **高** | 令牌只存 `.env`，日志掩码，Dashboard 不回显 |
| 企微回调 URL 被扫描/爆破 | 低 | 中 | 一次性 token + 签名校验 + 速率限制 |
| LLM 提示注入导致越权调用工具 | 中 | **高** | Tool Gateway 权限检查（10.5）、工具白名单 |
| 真实交易凭证泄漏 | 低 | **极高** | 系统内**根本不配置**真实凭证（7.7） |

**不在威胁模型内**：多租户隔离、横向移动、DDoS 防护、WAF。本项目是个人本地系统，不建设这些。

## 34.2 安全基线

| 项 | 规则 |
|---|---|
| 默认绑定 | 后端 `127.0.0.1:8000`，前端 `127.0.0.1:3000` |
| 默认鉴权 | **开启**（`AUTH_ENABLED=true`），且 `AUTH_TOKEN` 缺失时**拒绝启动** |
| 密钥存储 | 仅 `.env` / 环境变量 |
| 传输 | 本机回环用 HTTP；如需局域网访问，用 HTTPS（自签证书或反向代理） |
| 真实交易凭证 | **系统中不存在任何真实交易凭证**（7.7）；启动时扫描 `.env`，发现疑似真实 API key + `dry_run=false` 组合时**拒绝启动** |
| 最小权限 | Agent 工具白名单（30.4 `agent_versions.tool_allowlist`） |
| 审计 | 所有写操作与敏感读操作写入 `audit_logs` |

## 34.3 鉴权机制

### 34.3.1 Bearer Token（主方案）

单用户本地系统，**不引入用户体系**。采用静态 Bearer Token：

```http
Authorization: Bearer <AUTH_TOKEN>
```

| 规则 | 说明 |
|---|---|
| 令牌生成 | `python -c "import secrets; print(secrets.token_urlsafe(32))"`；≥ 32 字节熵 |
| 存储 | `.env` 的 `AUTH_TOKEN`（或 `AUTH_TOKEN_SHA256`，服务启动时哈希比对，磁盘上不存明文） |
| 传输 | 仅通过 `Authorization` 头，**不放 URL、不放 Cookie** |
| 前端持有 | 首次访问输入，存于 `sessionStorage`（**不是** `localStorage`，减少长期暴露） |
| 过期 | 静态令牌**默认不过期**；提供「轮换」功能：Dashboard 可生成新令牌并作废旧令牌 |
| 比较 | 使用**恒定时间比较**（`hmac.compare_digest`），防时序侧信道 |

### 34.3.2 为什么不用 Cookie 会话

Bearer token 免除了 CSRF token 的复杂度，代价是前端需手动附加头。对本项目（单用户、本机）这个代价比收益小得多。若未来要加「手机浏览器访问」，建议改为 Cookie + `SameSite=Strict` + CSRF token（见 34.7）。

### 34.3.3 失败处理

| 情况 | 响应 | 记录 |
|---|---|---|
| 无 token / token 错误 | `401 COMMON_UNAUTHORIZED` | 计数，不记详情 |
| 连续失败 ≥ 10 次 / 分钟 | `429 COMMON_RATE_LIMITED` + `Retry-After` | 触发 `SYSTEM_ALERT`（WARN） |
| 请求来源 IP 不在允许列表 | `403 COMMON_FORBIDDEN` | 记录（`ALLOWED_IPS` 未设置时不启用） |

`/health`、`/ready`、`/metrics` **不鉴权**（31.8），但必须只绑定本地（34.5）。

## 34.4 前端鉴权流程

```text
用户打开 http://localhost:3000
   |
   v
前端检查 sessionStorage 中的 token
   |
   +-- 无 token --> 显示「输入访问令牌」页（仅一个输入框，不做登录名/密码）
   |                 |
   |                 v
   |              POST /api/v1/auth/verify  { token }
   |                 |
   |                 +-- 成功 --> 存入 sessionStorage，刷新页面
   |                 +-- 失败 --> 提示「令牌无效」，不进入主界面
   |
   +-- 有 token --> 每个 API 请求附加 Authorization 头
                      |
                      +-- 收到 401 --> 清除 token，回到令牌页
```

**新增端点**：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/auth/verify` | 校验令牌，返回 `{"valid": true, "app_timezone": "..."}` |
| `POST` | `/api/v1/auth/rotate` | 生成新令牌并作废旧令牌（需有效旧令牌） |
| `GET` | `/api/v1/auth/session` | 当前会话状态（用于前端初始化） |

**Dashboard 不显示完整令牌**。令牌只在生成时**一次性显示**，之后仅显示掩码（前 6 位 + `****`）与指纹。

## 34.5 网络暴露边界

| 暴露方式 | 默认 | 允许条件 | 约束 |
|---|---|---|---|
| 仅本机回环 | **默认** | — | `APP_HOST=127.0.0.1` |
| 局域网访问 | 关闭 | 用户显式设置 `APP_HOST=0.0.0.0` | **必须** `AUTH_ENABLED=true` 且 `AUTH_TOKEN` 非空；必须在 Dashboard 显著提示「已暴露到局域网」 |
| 公网访问 | **不允许**（本项目不提供） | — | 如需，仅能通过 34.6 的一次性回调端口，且**仅暴露回调路径** |

**强制校验**：若 `APP_HOST` 不是 `127.0.0.1` 或 `localhost`，则：

1. `AUTH_ENABLED` 必须为 `true`；
2. `AUTH_TOKEN` / `AUTH_TOKEN_SHA256` 必须非空；
3. 否则**拒绝启动**，错误信息明确指出缺哪一项。

**Docker Compose 端口绑定**：

```yaml
ports:
  - "127.0.0.1:8000:8000"     # 不用 "8000:8000"
  - "127.0.0.1:3000:3000"
```

> **常见错误**：Docker 的 `ports: - "8000:8000"` 会绑定到 `0.0.0.0`，**绕过 `APP_HOST` 设置**，使服务暴露在局域网。这是本地部署最常见的暴露来源，必须在 Compose 中显式写 `127.0.0.1:` 前缀。

## 34.6 企微可信 IP 验证的一次性公网入口（解决 28.2 冲突）

**背景**：企微要求配置「可信域名」或「接收消息服务器 URL」作为配置可信 IP 的前置条件，需要一个**公网可访问的 URL**。这与「不暴露公网」直接冲突。

**v2.3 方案：独立的、临时的、一次性的回调入口**，与主服务完全隔离。

### 34.6.1 设计

```text
                  ┌──────────────────────────────────────────┐
                  │  独立容器/进程：public-callback          │
公网 (临时)  ───► │  - 独立端口 8081                          │
                  │  - 只监听特定路径                         │
                  │  - 一次性 token 校验                      │
                  │  - 无业务逻辑，无数据访问                 │
                  │  - 验证通过后自动关闭                     │
                  └──────────────────────────────────────────┘
                              │
                              ▼  (仅企微后台配置时访问一次)
                        本地主服务 (127.0.0.1)
```

| 约束 | 说明 |
|---|---|
| 端口独立 | `PUBLIC_CALLBACK_PORT=8081`，与 8000/3000 分离 |
| 路径限定 | 只响应 `/{PUBLIC_CALLBACK_TOKEN}` 与企微要求的验证路径，其余 404 |
| 无状态 | 该进程**不连接数据库、不读行情、不写任何业务数据** |
| 一次性 | 企微校验通过后**自动关闭监听**；也可手动停止容器 |
| 端口暴露 | 仅在需要时通过 Compose profile 启用；默认不启动 |
| 有效期 | 建议 ≤ 24 小时；`docker compose --profile callback up` 手动启动，用完即停 |

### 34.6.2 使用流程

1. 启动一次性入口：`docker compose --profile callback up -d public-callback`
2. 获取临时公网地址（隧道 / 临时域名）
3. 在企微后台配置「接收消息服务器 URL」为 `https://<临时地址>/<token>`，完成校验
4. **立即停止该入口**：`docker compose --profile callback down`
5. 在企微后台配置企业可信 IP（从 `gettoken` 报错中的 `from ip` 获取，22.5.2 第 3 步）
6. 之后正常发消息不再需要公网入口

### 34.6.3 约束

- 该入口**不接收**任何业务消息转发；本项目是纯单向推送，不需要接收回调；
- 若启用该入口，必须在 Dashboard 的「安全」页面显示醒目提示与「立即关闭」按钮；
- 该入口的存在**不得**改变主服务的绑定地址。

> **结论**：本项目在正常使用下**不暴露公网**。唯一例外是企微后台配置阶段的一次性、可关闭、无状态的验证入口。这满足了 13.4 的要求，也满足了 28.2 的待办要求。

## 34.7 手机访问（可选增强）

第 22.5 节的整个企微设计都是为了让通知进到个人微信。用户很可能也想在手机上直接看分析详情。

| 方案 | 说明 | 安全边界 |
|---|---|---|
| **A. 仅推送**（默认推荐） | 手机上通过微信收到消息，点击短链跳转。短链指向内网地址，在手机上打不开——需配合方案 B 或 C | 短链可指向一个**带一次性 token 的只读页面** |
| **B. Tailscale / WireGuard** | 私有网络，手机可访问 `http://<tailscale-ip>:3000` | 需在手机上配置客户端；鉴权同 34.3；不暴露公网 |
| **C. 内网穿透 + 只读视图** | 通过隧道暴露**只读** Dashboard | **必须**：独立只读令牌、只暴露 GET 端点、限流、访问日志 |

**v2.3 规定**：

1. 首期**只实现方案 A 的推送链路**，Dashboard 仍为本地访问；
2. 但**短链设计必须现在就位**（22.5.4 已要求短链）：`/s/{short_id}` 形式的短地址，短 ID 为不可猜测的随机串；
3. 短链解析必须支持两种后端：本地解析（返回 302 到完整 URL）与「受保护的只读页」（未来方案 C）。当前实现本地解析即可，**但短链服务的数据结构预留只读模式**（`short_links` 表加 `mode` 字段）。

## 34.8 工具与 Agent 的权限边界

第 10.5 章要求 Tool Gateway 管权限，但权限矩阵本身缺失。本章补齐。

### 34.8.1 权限层级

```text
Level 0  READ_PUBLIC     只读公开数据（K线、指标、市场状态）
Level 1  READ_RESEARCH   历史检索、相似情景、预测历史、Agent 表现
Level 2  WRITE_RESEARCH  触发分析、创建实验、运行回测、运行评估
Level 3  READ_EXECUTION  读取模拟账户/订单/持仓
Level 4  WRITE_EXECUTION 提交模拟订单、平仓、暂停/恢复账户
Level 5  SYSTEM          改配置、改推送偏好、触发任务
```

### 34.8.2 权限矩阵

| Agent / 角色 | L0 | L1 | L2 | L3 | L4 | L5 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Technical / Structure / Derivatives / Liquidity Agent | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Market Regime Agent | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Historical Research Agent | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Strategy Research / Review Agent | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Experiment Analysis Agent | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Report Agent | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Master Agent | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Event/Risk Agent | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| System Diagnosis Agent | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Dashboard（用户） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scheduler | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Paper Trading Adapter | — | — | — | ✅ | ✅ | ❌ |

**关键约束**：

1. **任何 Agent 都不得拥有 L4（写入执行）**。模拟下单只允许由 `Risk Manager` → `Paper Trading Adapter` 链路执行（7.3、7.7）；
2. **任何 Agent 都不得拥有 L5**。配置与推送偏好的修改只能来自已认证的用户请求；
3. `run_analysis_backtest` / `run_strategy_backtest` 属 L2，**只能创建隔离实验**，不得修改正式模拟账户或发布策略（6.2 硬约束）；
4. `submit_paper_order` 等工具**仅**在 Paper Adapter 内部服务可用（10.4 已声明），Agent 侧不可见。

### 34.8.3 越权处理

| 情况 | 行为 |
|---|---|
| Agent 请求未授权工具 | Tool Gateway 拒绝，返回 `403 COMMON_FORBIDDEN`，写入 `tool_invocations`（`status='DENIED'`），产生 `SYSTEM_ALERT`（WARN） |
| 工具执行超时 | `TOOL_TIMEOUT`，按 10.5 重试策略处理，超出后该 Agent 标记失败 |
| 参数越界（如请求超长时间范围的 K 线） | 参数校验拒绝，返回 `400 COMMON_VALIDATION_FAILED` |
| 提示注入诱导调用高权限工具 | **Tool Gateway 是独立进程/独立服务，不接受来自 LLM 的权限声明**；权限只由服务端配置决定 |

> **提示注入防护的关键**：Agent 的自然语言请求**永远不能提升自身权限**。权限检查发生在 Tool Gateway，依据服务端配置（`agent_versions.tool_allowlist`）而非请求内容。4.7 原则 7 的「外部内容为不可信数据」在架构上的落点就是这一条。

## 34.9 密钥轮换

| 密钥 | 轮换方式 | 影响 |
|---|---|---|
| `AUTH_TOKEN` | Dashboard 轮换（34.4） | 旧令牌立即失效 |
| `FEISHU_SECRET` / Webhook | 飞书后台重生成，更新 `.env`，重启服务 | 期间推送失败进重试队列 |
| `WECOM_CORP_SECRET` | 企微后台重置，更新 `.env`，重启 | **必须**同时清空 `notification_channel_state.access_token`，否则会用旧 token 反复触发 `40001` |
| LLM API Key | 云端控制台重生成 | 同上 |

## 34.10 安全验收（补 15.5）

- [ ] `APP_HOST=0.0.0.0` 且无令牌时，进程**拒绝启动**（不是仅告警）。
- [ ] Docker Compose 中所有 `ports` 均显式绑定 `127.0.0.1:`。
- [ ] `/api/v1/**` 无有效令牌时返回 401；`/health`、`/ready`、`/metrics` 不需要令牌但只在本机可达。
- [ ] 令牌在日志、错误堆栈、Dashboard 中均不出现明文。
- [ ] 前端令牌存于 `sessionStorage`，关闭标签页后消失。
- [ ] 连续 10 次错误令牌请求后触发限流与 `SYSTEM_ALERT`。
- [ ] Tool Gateway 拒绝 Agent 调用 L4/L5 工具，并记录 `DENIED`。
- [ ] `.env` 中出现疑似真实交易凭证时，进程拒绝启动。
- [ ] `public-callback` 容器默认不启动；启动后仅响应带 token 的路径；关闭后端口不再监听。
- [ ] Agent 无法通过自然语言指令获得超出白名单的工具权限。

---

# 35. 前端规格与 Dashboard 实现约定（v2.3 新增，P0）

第 8 章定义了页面的**内容与顺序**，但没有定义**如何实现**。本章补齐前端工程约定。

## 35.1 技术基线

| 项 | 选型 | 说明 |
|---|---|---|
| 框架 | Next.js（App Router） | 9.2 已定 |
| 语言 | TypeScript，`strict: true` | |
| 图表 | TradingView Lightweight Charts（K 线）+ Apache ECharts（统计/评估） | 9.2 已定 |
| 数据获取 | TanStack Query（或等价的请求缓存库） | 服务端状态缓存 + 重取 |
| 客户端状态 | 轻量（Zustand 或 Context），仅用于 UI 态 | 行情等**服务端状态不进全局 store** |
| 样式 | CSS Modules 或 Tailwind，二选一并全局统一 | 不混用 |
| 组件库 | 轻量自建，优先 headless 方案 | 减少依赖体积 |

**硬约束**：

1. `strict: true` 必须开启，`noUncheckedIndexedAccess` 建议开启；
2. **不得**在前端硬编码任何阈值、颜色语义、评估口径相关常量——全部来自 API；
3. 前端**不得**直接访问数据库或 Binance，一律走 `/api/v1/**`；
4. 前端**不持有**任何服务端凭据（34.3）。

## 35.2 页面路由表

```text
/
├── (market)
│   ├── /                       首页 = Market Overview + AI 分析（8.1）
│   ├── /markets/[symbol]       单资产详情（多周期 + 结构 + 衍生品 + 流动性）
│   ├── /analysis/[analysisId]  分析详情（8.2）
│   ├── /opportunities          机会列表
│   ├── /predictions            预测追踪（8.1 第 5 区）
│   ├── /predictions/[id]       预测详情 + 后续行情对照
│   └── /research               研究与相似情景
├── (evaluate)
│   ├── /performance            AI 表现（双轨并排，25.5）
│   ├── /agents                 Agent 对比
│   ├── /experiments            实验列表（8.3）
│   └── /experiments/[id]       实验详情
├── (operations)
│   ├── /anomalies              异动事件列表
│   ├── /anomalies/[id]         异动详情（含各 revision 的投递记录）
│   ├── /notifications          投递记录与死信
│   ├── /notifications/prefs    推送偏好（含渠道自检，22.5.7）
│   ├── /jobs                   任务与运行历史
│   ├── /system                 系统健康与事件
│   └── /paper                  模拟交易（8.4，P1）
└── /auth/verify                令牌输入页（34.4）
```

**URL 必须是深链接可分享的**：任何推送中的「详情链接」都指向某个可独立访问的 URL（短链 `/s/{id}` → 302 到具体页面）。

## 35.3 数据获取与刷新策略

| 页面/区块 | 刷新方式 | 间隔 | 说明 |
|---|---|---|---|
| 首页价格与实时 K 线 | **轮询** | 5 秒 | 复用实时缓存接口（29.5.1） |
| 首页多周期指标与状态 | 轮询 | 60 秒 | |
| 分析详情 | 轮询（分析进行中）→ 不轮询（完成后） | 进行中 3 秒 | 完成即停 |
| 机会 / 预测列表 | 轮询 | 60 秒 | |
| 评估报表 | **手动刷新 + 陈旧提示** | — | 评估是重计算，避免频繁触发 |
| 异动事件 | 轮询 | 30 秒 | |
| 通知记录 | 轮询 | 30 秒 | |
| 任务运行历史 | 轮询（运行中）→ 30 秒 | | |

**规则**：

1. 页面不可见（`visibilitychange`）时**暂停轮询**，恢复可见时立即刷新一次；
2. 所有轮询必须使用**条件请求**（`ETag` / `Last-Modified`），服务端返回 `304` 时不重传数据体；
3. 连续错误达 3 次后**指数退避**（5s → 15s → 60s），并在页面显示「连接异常」提示；
4. **不使用 WebSocket**（除分析进度的 SSE）——复杂度不划算，且本地场景轮询足够。

## 35.4 状态设计

每个页面必须显式区分以下状态，**不允许只实现「有数据」的情况**：

| 状态 | 表现 | 触发条件 |
|---|---|---|
| `LOADING` | 骨架屏 / 加载指示 | 首次加载 |
| `READY` | 正常内容 | 有数据 |
| `EMPTY` | 「暂无数据」+ 原因 + 下一步动作 | 查询成功但无数据 |
| `STALE` | 内容 + 「数据陈旧」横幅（显示最后更新时间） | 数据超过 TTL |
| `DEGRADED` | 内容 + 部分数据缺失提示 | `data_quality != GOOD` |
| `ERROR` | 错误信息 + 重试按钮 + `request_id`（便于排障） | 请求失败 |

**`EMPTY` 必须给出下一步动作**。例如「本周期无异动记录」应附带「查看异动规则」链接；「无已到期预测」应说明「预测将在窗口结束后自动评估」。

## 35.5 数据质量的前端表达

这是本项目区别于普通行情网站的关键：**数据质量必须可见**。

| 数据质量 | 前端表现 |
|---|---|
| `GOOD` | 正常显示，不加标记 |
| `DEGRADED` | 区块顶部黄色提示条：「部分数据降级，指标可能不完整」 |
| `STALE` | 橙色提示条 + 「最后更新于 X 前」+ 隐藏实时价格（避免误导） |
| `MISSING` | 该区块显示占位符 + 「数据缺失」，**不显示 0 或上一次的值** |
| `INVALID` | 红色提示条 + 「数据异常，已排除」 |

**禁止**：把 `MISSING` 渲染成 `0`、把 `STALE` 的价格渲染成当前显示价、把缺失字段渲染成空白后让用户误以为是「没有异动」。

## 35.6 关键组件约定

| 组件 | 职责 | 关键要求 |
|---|---|---|
| `SymbolHeader` | 标的、当前价、涨跌、市场状态、数据质量徽标 | 状态标签与颜色统一映射，**颜色来自 API 或共享常量**，不散落在组件里 |
| `MultiTimeframePanel` | 多周期 K 线 + 指标 | 周期切换不重载整页 |
| `MarketRegimeBadge` | 市场状态徽标 | 多周期不一致时**并列展示多个徽标**（4.6 要求） |
| `EvidenceBlock` | 支持证据 / 反对证据 | 区分显示，**不得只显示支持证据** |
| `ConfidenceMeter` | 置信度展示 | 必须附带「主观置信度，非统计胜率」提示（4.7 要求） |
| `AnomalyCard` | 异动卡片 | 显示规则 ID、触发阈值、实测值、级别、投递状态与各 revision |
| `EvaluationPanel` | 双轨评估面板 | **左右并排**（25.5），不可折叠其一；显示 `sample_size` 与 `INSUFFICIENT_SAMPLE` 标记 |
| `SampleSizeBadge` | 样本量徽标 | `N < N_min` 显示灰色「样本不足」，**不显示任何比率数值** |
| `QualityBanner` | 数据质量横幅 | 按 35.5 规则 |
| `NotificationChannelCard` | 渠道状态卡 | 显示健康、限流余量、掩码后的凭据、**人工确认项清单**（22.5.7） |

## 35.7 评估页面的强制规则

第 33 章的口径必须在 UI 上被正确表达：

| 规则 | UI 要求 |
|---|---|
| `INSUFFICIENT_SAMPLE` | 该格显示 `—` + tooltip「样本不足（n/N_min）」，**不显示百分比** |
| `LOW_CONFIDENCE` | 显示值但带黄色标记 |
| 排名 | 样本不足的组**不参与排名**，显示为 `—` 而非末位 |
| 口径版本 | 页面顶部显示 `evaluator_version` 与生效时间 |
| 去重前后 | 方向/区间等比率同时显示去重前后的值（33.7.2） |
| 双轨 | 两轨并排；**「判断对但亏钱」的四象限交叉表必须单独展示**（33.9） |
| 覆盖率 | Brier 覆盖率、区间宽度必须与对应指标并排展示 |

## 35.8 图表约定

| 图表 | 类型 | 约定 |
|---|---|---|
| K 线 | Lightweight Charts | 成交量副图；叠加 EMA/Bollinger；**标注快照 `as_of` 时刻** |
| 预测对照 | 叠加折线 | 在价格图上叠加预测区间带 + 基准价水平线 + 实际走势 |
| 评估趋势 | ECharts 折线 | 准确率随时间（含样本量副轴） |
| 校准曲线 | ECharts 散点 + 对角线 | 显示每箱样本数；样本 <10 的箱置灰 |
| 双轨对比 | ECharts 散点/四象限 | 四象限散点，轴为「方向命中」×「净盈亏」 |
| 权益曲线 | ECharts 折线 | 同时显示回撤副图 |

**通用约定**：

1. 所有图表**必须**能显示数据缺失区间（留空，不连线跨越）；
2. 时间轴统一 UTC 存储、按用户时区展示（11.7）；
3. 颜色语义全局统一：涨=绿/跌=红（需与用户偏好一致，可配置）；
4. 图表必须有**表格替代视图**（可访问性与精确读数）。

## 35.9 移动端

| 断点 | 布局 |
|---|---|
| `< 640px` | 单列；K 线简化（隐藏副图指标）；卡片纵向堆叠；表格横向滚动 + 首列冻结 |
| `640–1024px` | 单列 + 更宽图表 |
| `> 1024px` | 首页多列（市场概览 2/3 + 分析结论 1/3） |

移动端必须能完整阅读异动卡片与 AI 结论——这是通知进微信后的主要查看场景。

## 35.10 前端工程约定

| 项 | 规则 |
|---|---|
| 组件目录 | `apps/web/src/components/{market,analysis,anomaly,evaluation,notification,system,common}` |
| 数据层 | `apps/web/src/api/`（请求封装 + 类型）；类型**从 OpenAPI 自动生成**，不手写 |
| 类型来源 | 后端导出 OpenAPI（31），前端用 `openapi-typescript` 生成 TypeScript 类型 |
| 状态码 | ESLint + Prettier + `eslint-plugin-react-hooks` |
| 组件测试 | Vitest + Testing Library，覆盖关键组件的状态渲染（35.4 的 6 种状态） |
| E2E | Playwright，覆盖 3 条主路径：登录 → 首页 → 分析详情；异动详情；推送偏好修改 |
| 禁止 | `any`；未处理的 `Promise`；在 `useEffect` 中发请求（改用 Query 库） |

## 35.11 构建与运行

| 项 | 值 |
|---|---|
| 构建 | `npm run build`（Next.js 生产构建） |
| 开发 | `npm run dev`（热更新），代理 `/api` → `http://127.0.0.1:8000` |
| 容器 | 多阶段构建；运行阶段仅 `node_modules` + `.next` + `public` |
| 静态资源 | K 线图表按需加载（`dynamic import`），避免首屏体积 |
| 环境变量 | 前端构建期只注入 `NEXT_PUBLIC_*`；后端地址通过同源代理解决，**不硬编码后端 URL** |
| 健康检查 | `/` 返回 200 即视为前端就绪 |

## 35.12 性能目标

| 指标 | 目标 |
|---|---|
| 首屏（本地，缓存热） | < 1.0 秒 |
| LCP | < 2.0 秒 |
| 首页轮询响应 | < 300 ms（本地） |
| 分析详情页（冷加载） | < 1.5 秒 |
| JS 初始包（gzip） | < 200 KB（图表库懒加载） |

> 这些数字以本机开发环境为准，属「明显劣化即可发现」的自检线，不是 SLO。

---

# 36. 测试策略、工程约定与 CI（v2.3 新增，P0）

第 15 章给出了验收标准，但没有任何测试策略与工程约定。目录结构（19 章）里已有 `tests/`、`migrations/`、`scripts/`，本章让它们有明确规则。

## 36.1 测试分层

| 层 | 范围 | 依赖 | 运行时 | 覆盖率要求 |
|---|---|---|---|---|
| **单元测试** | 纯函数：指标计算、结构识别、规则判定、渲染裁剪、评估口径 | 无外部依赖 | < 30 秒 | 核心算法 **≥ 90%** |
| **契约测试** | API 响应结构、错误码、OpenAPI 一致性 | 需要 DB（容器） | < 2 分钟 | 全部端点 |
| **集成测试** | 采集→入库→特征→异动→推送 的完整链路（用录制回放数据） | 需要 DB + Redis | < 5 分钟 | 关键链路全覆盖 |
| **端到端测试** | 前端主路径 + 真实模拟下单（Mock Freqtrade） | 全栈容器 | < 10 分钟 | 3 条主路径 |
| **回放测试** | 用历史行情回放，验证异动规则命中率与误报率 | 需要 DB | 按需 | 规则基线 |

**分层原则**：

1. **不测实现细节**——测「规则在给定输入下产生给定输出」，而非「调用了某个私有函数」；
2. **异动规则必须有回放测试**（36.5），这是本项目区别于普通项目的地方；
3. **任何涉及时间的功能必须可注入时钟**（用 `freezegun` / 依赖注入），禁止 `datetime.now()` 直接调用；
4. **任何涉及随机性的地方必须有固定种子**（`random.seed` / `numpy.random.seed`）。

## 36.2 目录与命名

```text
tests/
├── unit/
│   ├── indicators/
│   ├── structure/
│   ├── regime/
│   ├── anomaly/          # 每条规则一个测试文件
│   ├── notifications/    # 渲染、裁剪、去重、冷却、配额
│   ├── evaluation/       # 评估口径（对应 33 章每节）
│   └── risk/
├── contract/             # API 契约
├── integration/
│   ├── ingestion/        # 29 章
│   ├── anomaly_to_notify/# 22.6 链路
│   ├── scheduler/        # 23 章
│   └── evaluation/
├── e2e/
├── fixtures/
│   ├── market/           # 录制的 Binance 原始报文
│   ├── responses/        # 录制的 REST 响应
│   └── llm/              # 录制的模型输出（含各种坏输出）
└── helpers/
```

**命名**：`test_<模块>_<被测行为>_<场景>`，如 `test_anomaly_price_pct_1m_closed_candle_only`。

## 36.3 必测清单（关键不变量）

以下不变量**必须有**对应测试，且**必须**在 CI 中运行：

| 不变量 | 所属章节 | 测试类型 | 断言要点 |
|---|---|---|---|
| 未收盘 K 线不落库 | 29.5 | 集成 | `market_candles` 中不存在 `close_time > now()` 的行 |
| K 线连续性无缺口 | 29.4.1 | 集成 | 补数后相邻差值 == interval |
| OHLC 合法性 | 4.3 | 单元 | 非法 K 线被拒并产生 `INVALID` 事件 |
| 主动买卖方向正确 | 30.2 | 单元 | `is_taker_buy == NOT is_buyer_maker` |
| 快照不含未来数据 | 4.8 | 单元 | `content` 中所有时间戳 ≤ `as_of` |
| 预测不可被事后修改 | 5.1 | 单元 | 修改已创建预测 → `409 PRED_IMMUTABLE` |
| 评估无未来数据泄漏 | 5.4 / 33.10 | 单元 | 评估只读 `window_end` 之后的数据 |
| 异动判定不依赖 LLM | 15.6 | 集成 | 禁用 LLM / 断网后仍能检出并推送 |
| 同一异动的不同 revision 都能投递 | 22.4.4.1 | 集成 | revision 0 与 1 均送达 |
| 同一 `(fingerprint, revision)` 只投递一次 | 22.4.4.1 | 集成 | 重复触发不产生第二条 |
| 去重与冷却是不同键空间 | 22.4.4.1 | 单元 | 两者互不干扰 |
| 渠道限流状态按渠道独立 | 22.4.1 | 集成 | 企微限流不影响飞书 |
| 限流不消耗账户级配额 | 22.4.4.2 | 单元 | |
| 测试接口必须带 channel | 31.9 | 契约 | 缺参 → `400 NOTIFY_CHANNEL_REQUIRED` |
| 账户级配额全渠道合计 | 22.4.4.2 | 集成 | 两渠道合计 ≤ 配额 |
| CRITICAL 豁免配额与免打扰 | 22.4.4.2 | 单元 | |
| 评估样本不足时不输出比率 | 33.6 | 单元 | `metric_value IS NULL` |
| 评估排名排除样本不足组 | 33.6 | 单元 | |
| 评估重叠去重生效 | 33.7 | 单元 | |
| 区间与覆盖率必须成对输出 | 33.5.2 | 单元 | API 响应同时含两者 |
| `confidence` 不被当作概率 | 33.4 | 单元 | 无 `probability` 时 Brier 为 NULL |
| 回测与实盘特征版本一致 | 38 | 集成 | |
| 幂等键重复提交不重复执行 | 23.1.3 / 31.4 | 集成 | |
| 补跑标记为 backfill 且不占实时配额 | 23.1.3 | 集成 | |
| 超出补跑上限标记 MISSED | 15.8 | 集成 | |
| Freqtrade 非 dry-run 拒绝启动 | 7.7 | 契约 | 进程退出码非 0 |
| 对账差异阻断新开仓 | 7.8 | 集成 | |
| 幂等键相同不重复开仓 | 7.8 | 集成 | |
| Agent 无 L4/L5 权限 | 34.8 | 单元 | Tool Gateway 拒绝 |
| 无令牌访问 API 返回 401 | 34.3 | 契约 | |
| 暴露到非回环且无令牌时拒绝启动 | 34.5 | 契约 | |
| 日志不含密钥明文 | 34.10 | 单元 | 扫描日志断言无密钥 |
| 配置项全部登记 | 32.5 | 单元 | Settings 字段 ⊆ 配置目录 |

## 36.4 CI 流水线

```yaml
# .github/workflows/ci.yml 结构
jobs:
  lint:          # ruff + mypy + eslint + tsc --noEmit        (无依赖，~1 分钟)
  unit:          # pytest tests/unit                          (~30 秒)
  contract:      # pytest tests/contract + services: postgres, redis
  integration:   # pytest tests/integration                   (录制回放，不打真交易所)
  e2e:           # playwright + docker compose up            (PR 上仅主分支跑)
  build:         # docker build 全部镜像 + alembic upgrade head 验证
  config-check:  # 配置目录与 Settings 一致性 (32.5)
  contract-diff: # OpenAPI 变更检测：破坏性变更需人工批准
```

**触发规则**：

| 分支 | 执行 |
|---|---|
| 特性分支 / PR | `lint` + `unit` + `contract` + `build` |
| 合并到 `main` | 全部 + `integration` + `e2e` |
| 定时（每日） | 全部 + `replay` 回放测试 + 依赖安全扫描 |

**硬约束**：

1. `lint`、`unit`、`contract` **必须通过才能合并**；
2. **禁止在 CI 中访问真实 Binance 或真实 LLM API**——一律用录制回放（`tests/fixtures/`）。真实 API 调用仅允许在本地开发或专门的 `nightly` 手动任务中；
3. OpenAPI 破坏性变更（31.10）必须在 PR 中标注并经人工批准；
4. 数据库迁移必须通过 `alembic upgrade head` + `downgrade -1` 往返测试。

## 36.5 异动规则的回放测试（专项）

第 22.2 的约 40 条规则是确定性引擎，必须有**可复现的行为基线**。

```text
输入：tests/fixtures/market/replay_2026-11-01_to_2026-11-08/
      （含 1m/5m/15m/1h/4h 的 BTCUSDT + ETHUSDT K 线，及对应的 OI/Funding）

输出：每条规则的触发次数、触发时刻、级别、被抑制的次数
      → tests/fixtures/anomaly/baseline.json
```

| 规则 | 断言 |
|---|---|
| 全部规则 | 触发次数与基线**完全一致**（确定性引擎必须完全一致） |
| 单条规则 | 级别判定与基线一致 |
| 阈值版本 | 修改阈值后，基线必须随之变化（验证规则确实读配置） |
| 误报率 | 统计 WARN 级规则的触发中「1h 后反向」的比例，**作为指标输出但不设硬断言**（真实分布会漂移） |

> **基线文件是规则的行为契约**。任何规则修改都必须**显式更新基线**，PR 中附上「哪些规则的触发次数变了、为什么」。这比「改了阈值但没人发现误报率翻倍」安全得多。

## 36.6 LLM 相关的测试

LLM 不确定性要求**不测输出内容，测输出结构与失败处理**。

| 测试 | 断言 |
|---|---|
| Schema 校验 | 合法输出通过；缺字段/多字段/类型错误被拒 |
| 修复重试 | Schema 错误时最多重试 `LLM_SCHEMA_REPAIR_ATTEMPTS` 次（32.4） |
| 修复耗尽 | 该 Agent 标记失败，Master 汇总中显式标注缺失，**不伪造输出** |
| 超时 | 标记 `TIMEOUT`，触发部分结果输出（24.4） |
| 降级 | 预算超限时按 24.2 顺序降级，输出带 `degraded=true` 与原因 |
| 提示注入 | Agent 输出中包含「忽略规则」的文本时，**不改变**任何权限与行为（34.8.3） |
| 不可信内容 | 外部抓取的文本被标记为数据，不被当作指令执行（4.7 原则 7） |

**Mock 策略**：LLM 调用在测试中全部 mock，mock 返回录制好的响应（`tests/fixtures/llm/`），包含正常、缺字段、非法 JSON、超时、拒答五类。

## 36.7 代码规范

| 项 | 规则 |
|---|---|
| Python | Python 3.12+；`ruff`（lint + format）；`mypy --strict`（核心模块）/ 默认（非核心） |
| 行宽 | 100 |
| 类型 | **所有函数签名有类型注解**；`Any` 必须显式注释理由 |
| 命名 | 模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE` |
| 导入 | 禁止 `from x import *`；禁止循环导入 |
| 日志 | 用 `structlog` 输出 JSON；禁止 `print`；禁止在日志中输出完整 prompt（只输出摘要与 hash） |
| 异常 | 定义项目级异常基类；**禁止裸 `except:`**；禁止 `except Exception: pass` |
| 事务 | 跨表写操作必须在单个事务内；状态变更用「追加事件」而非覆盖（30.5） |
| 时间 | 一律用注入的 `now()` 函数，禁止直接 `datetime.now(timezone.utc)` |
| 随机 | 一律用注入的 `rng`，禁止全局 `random` |

## 36.8 数据库迁移约定

见 30.8。补充：

| 规则 | 说明 |
|---|---|
| 生成 | `alembic revision --autogenerate` 后**必须人工审查**（autogenerate 会误判索引/约束） |
| 命名 | `NNNN_<snake_case_description>.py`，编号递增 |
| 数据回填 | 单独一个迁移，不用 `op.execute` 内联大批量逻辑 |
| 破坏性变更 | 遵循 30.8 的 6 步流程 |
| 验证 | CI 执行 `upgrade head` → `downgrade -1` → `upgrade head` |

## 36.9 脚本目录约定

`scripts/` 目录（19 章）用于运维脚本：

```text
scripts/
├── bootstrap_db.sh          # 建库 + 初始迁移 + 种子数据
├── backfill_market.py       # 历史补数（可指定区间、限速）
├── replay_anomaly.py        # 异动回放（生成基线）
├── recalc_evaluation.py     # 按新口径重算评估
├── rotate_token.py          # 令牌轮换
├── restore_backup.sh        # 从备份恢复（季度演练用）
├── smoke_test.py            # 部署后冒烟检查
└── seed_config.py           # 生成 config/base.toml 默认值
```

**约定**：脚本必须支持 `--dry-run`（除 `bootstrap` 与 `restore`）；必须可重复执行（幂等）；必须有 `--help` 与清晰的失败退出码。

## 36.10 文档约定

| 规则 | 说明 |
|---|---|
| 本文档是唯一真源 | 章节一经发布，修改必须在本文件内进行，不另建文档 |
| 新增章节 | 从第 39 章起递增，不重排既有编号（延续 v2.3 的做法） |
| 变更记录 | 每次修改更新第 0 章的版本表 |
| 交叉引用 | 用「见第 X 章」而不是行号（行号会漂移） |
| 代码引用 | 本文档不贴大段实现代码，只贴**契约性**片段（Schema、JSON、SQL、协议） |
| 决策记录 | 重要设计变更记入第 18 章「关键设计决策记录」 |

---

# 37. 可观测性与性能目标（v2.3 新增，P0）

第 13 章列了要监控什么，但没给指标名、暴露方式与目标值。「延迟」「积压」若没有数字定义，无法判断异常。

## 37.1 可观测性三支柱

| 支柱 | 工具 | 说明 |
|---|---|---|
| 日志 | structlog（JSON） | 结构化日志，带 `trace_id` / `request_id` / `job_run_id` |
| 指标 | Prometheus（`prometheus_client`） | `/metrics` 暴露 |
| 追踪 | 结构化 `trace_id` 贯穿 | 首期不做 OpenTelemetry 分布式追踪，用 trace_id 串联日志 |

> 首期**不做**完整的分布式追踪系统。个人本地系统里，`trace_id` + 结构化日志已经足够定位问题。

## 37.2 指标清单

### 采集层

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `market_data_latency_ms` | Histogram | `symbol,market_type,timeframe,source` | 交易所时间 → 入库完成的延迟（29.8） |
| `market_candles_written_total` | Counter | `symbol,market_type,timeframe,source` | |
| `market_gaps_total` | Counter | `symbol,market_type,timeframe` | 检测到的缺口数 |
| `market_data_quality_events_total` | Counter | `rule_id,severity` | |
| `market_ws_connections_total` | Counter | `market_type` | |
| `market_ws_connected` | Gauge | `market_type` | 1=已连接 |
| `market_ws_reconnects_total` | Counter | `market_type` | |
| `market_clock_offset_ms` | Gauge | — | 本地时钟偏移 |
| `market_binance_rate_limit_hits_total` | Counter | `endpoint` | 429/418 次数 |

### 特征与异动

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `feature_compute_duration_ms` | Histogram | `feature_name` | |
| `anomaly_events_total` | Counter | `rule_id,severity` | |
| `anomaly_scan_duration_ms` | Histogram | — | |
| `anomaly_scan_last_success_timestamp` | Gauge | — | 监控扫描是否卡死 |
| `anomaly_rule_trigger_rate` | Gauge | `rule_id` | 滚动 24h 触发率，用于调参（22.7） |

### LLM

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `llm_requests_total` | Counter | `model_id,tier,agent,status` | |
| `llm_latency_ms` | Histogram | `model_id,tier` | |
| `llm_tokens_total` | Counter | `model_id,direction` | `direction=in\|out` |
| `llm_cost_usd_total` | Counter | `model_id,tier` | |
| `llm_daily_cost_usd` | Gauge | — | 当日累计，对比 24.2 硬上限 |
| `llm_schema_invalid_total` | Counter | `agent` | Schema 校验失败次数 |
| `llm_degraded_total` | Counter | `reason` | 降级次数 |

### 通知

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `notification_deliveries_total` | Counter | `channel,delivery_status,event_type` | |
| `notification_delivery_latency_ms` | Histogram | `channel` | 投递耗时 |
| `notification_queue_depth` | Gauge | `channel` | 待发队列长度 |
| `notification_dead_letters_total` | Counter | `channel` | |
| `notification_suppressed_total` | Counter | `reason` | `reason=dedup\|cooldown\|quota\|quiet_hours\|rule_limit` |
| `notification_daily_quota_used` | Gauge | — | 当日已用配额 |
| `notification_channel_rate_limit_hits_total` | Counter | `channel` | 渠道限流次数 |
| `notification_channel_health` | Gauge | `channel` | 1=健康 |

### 调度与评估

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `job_runs_total` | Counter | `job_id,status` | |
| `job_duration_ms` | Histogram | `job_id` | |
| `job_last_success_timestamp` | Gauge | `job_id` | 监控任务卡死 |
| `job_backfill_total` | Counter | `job_id` | |
| `job_missed_total` | Counter | `job_id` | 超出补跑上限 |
| `evaluation_backlog` | Gauge | — | 待评估预测数 |

### 执行（P1）

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `paper_equity` | Gauge | `account_id` | |
| `paper_open_positions` | Gauge | `account_id` | |
| `paper_order_failures_total` | Counter | `account_id,reason` | |
| `paper_reconciliation_diffs` | Gauge | `account_id` | 未解释差异数（>0 阻断开仓） |
| `paper_freqtrade_health` | Gauge | `account_id` | |

## 37.3 目标值（SLO）

| 项 | 目标 | 硬上限 | 测量窗口 |
|---|---|---|---|
| **行情入库延迟** p95 | < 500 ms | 2 s | 5 分钟 |
| **行情入库延迟** p99 | < 1 s | 3 s | 5 分钟 |
| **异动检出→确定性快报发出** p95 | < 5 s | 30 s | 1 小时 |
| **异动检出→AI 解读发出** p95 | < 60 s | 330 s | 1 天 |
| **单次全链路分析** p95 | < 90 s | 180 s | 1 天 |
| **单次全链路分析** 最坏 | — | 300 s（超时输出部分结果） | — |
| **快照构建** p95 | < 2 s | 10 s | 5 分钟 |
| **通知投递耗时** p95（飞书） | < 2 s | 10 s | 1 小时 |
| **通知投递耗时** p95（企微） | < 3 s | 15 s | 1 小时 |
| **Dashboard 首页查询** p95 | < 200 ms | 1 s | 5 分钟 |
| **评估批次（全量 30 天）** | < 60 s | 600 s | — |
| **K 线缺口修复时间** p95 | < 5 分钟 | 30 分钟 | 1 天 |
| **重启后恢复可用**（含补数与对账） | < 10 分钟 | 30 分钟 | — |

**指标与目标的关系**：上表任一项持续超出「硬上限」时，产生 `SYSTEM_ALERT`（WARN）；超出「目标」超过 3 个连续窗口时，产生 `SYSTEM_ALERT`（WARN）。

## 37.4 量化成功指标

第 1.3 节的成功标准是定性的，本节给出可验收的量化门槛。**这些是目标值，不是承诺**；实际基线需在 Phase 5 测量后回填。

| 维度 | 指标 | 目标 | 备注 |
|---|---|---|---|
| 数据 | 补数后 K 线缺口数 | = 0 | M1 硬标准 |
| 数据 | 入库延迟 p99 | < 1 s | |
| 推送 | 异动检出→送达 p95 | < 5 s | |
| 推送 | 每日重复/轰炸次数 | = 0 | 同 `(fingerprint, revision)` 不重复 |
| 推送 | 死信率 | < 1% | |
| 异动 | WARN 规则日均触发数 | 5–30 条 | 超过 30 说明阈值过松，需校准 |
| 异动 | CRITICAL 规则日均触发数 | 0–3 条 | |
| 异动 | 按规则目的定义的有效性 | 分别评价方向变化、波动扩大、流动性恶化或数据故障检出 | 数据/风险规则不要求后续存在方向性；结合用户噪声反馈调节 |
| 分析 | 全链路成功率 | > 95% | |
| 分析 | Schema 校验失败率 | < 5% | 超过则该 Agent 的 Prompt 需调整 |
| 分析内容 | 事实与引用、冲突处理、变化解释 | 通过第 4.7.3 固定场景，关键证据错误为 0 | 持续展示第 5.2.1 各维度，不能只看 schema 成功率 |
| 评估 | 方向与三分类表现 | 在共同预测时点上与预注册规则/历史多数类基线比较 | 展示增益、覆盖率及时间分块置信区间；不以固定 55% 判定有效 |
| 评估 | 样本积累 | 6 个月内争取 ≥ 200 条可评价前向预测 | 为积累目标；不保证统计显著，须考虑相关性与效应大小 |
| 评估 | Brier Score | 优于预先固定的历史频率基线 | 三分类主指标、二分类辅助指标分开展示，报告 skill 与覆盖率 |
| 调度 | 任务按时率 | > 99% | 观察 ≥3 天 |
| 调度 | 漏跑（MISSED）数 | = 0 | 超出补跑上限也要显式记录 |

> **诚实性约束**：上表中的评估类目标若在实测中未达成，**必须在报告中如实呈现，不得调整口径使其显得达成**。评估的价值在于揭示缺陷（28.3 已声明）。口径变更必须升 `evaluator_version` 并分表展示（33.8）。

## 37.5 日志规范

### 37.5.1 结构

```json
{
  "ts": "2026-11-09T01:23:45.123Z",
  "level": "INFO",
  "event": "notification.delivered",
  "service": "notification-gateway",
  "trace_id": "tr_01J...",
  "request_id": "req_01J...",
  "job_run_id": "job_01J...",
  "agent_run_id": null,
  "anomaly_event_id": "ae_20261109_0012",
  "duration_ms": 812,
  "msg": "..."
}
```

### 37.5.2 规则

| 规则 | 说明 |
|---|---|
| 事件名 | `<domain>.<action>`，全小写下划线；**事件名是稳定契约**，可被检索与告警 |
| 级别 | `DEBUG`（默认关）/ `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| 禁止 | 不打印完整 prompt、完整 LLM 响应、密钥、完整盘口、完整 K 线数组 |
| 脱敏 | 统一脱敏器处理（32.6） |
| 采样 | 高频路径（每次 K 线写入）默认不记 INFO 日志，依赖指标 |
| 轮转 | 按大小 100 MB，保留 10 个文件 |

### 37.5.3 必须记日志的事件

| 事件 | 级别 |
|---|---|
| `anomaly.detected` | INFO |
| `analysis.started` / `analysis.completed` / `analysis.failed` | INFO / INFO / ERROR |
| `agent.degraded` / `agent.failed` | WARNING / ERROR |
| `notification.delivered` / `notification.failed` / `notification.suppressed` / `notification.dead_lettered` | INFO / ERROR / INFO / ERROR |
| `notification.revised`（revision 升级） | INFO |
| `job.started` / `job.completed` / `job.failed` / `job.backfilled` / `job.missed` | INFO |
| `data.gap_detected` / `data.quality_degraded` | WARNING |
| `paper.order_submitted` / `paper.order_failed` | INFO / ERROR |
| `paper.reconciliation_diff` | ERROR |
| `risk.halted` | CRITICAL |
| `auth.denied` | WARNING |
| `config.loaded` | INFO（脱敏摘要） |

## 37.6 健康检查

| 端点 | 语义 | 判定 |
|---|---|---|
| `/health` | 进程存活 | 恒 200（不查依赖） |
| `/ready` | 就绪 | 检查 DB、Redis 连接；任一不可用 → 503 |
| `/api/v1/system/health` | 详细健康 | 各组件状态、数据质量、任务状态、渠道健康 |

**深度健康检查**（Dashboard 使用）：每 30 秒采集一次，涵盖：数据新鲜度、各任务最后成功时间、异动扫描存活、渠道健康、DB/Redis 状态、LLM 可用性、磁盘占用、评估积压。

## 37.7 容量与资源

| 资源 | 监控 | 阈值动作 |
|---|---|---|
| 磁盘（Postgres 数据卷） | `market_data_disk_usage_pct` | > 80% 告警；> 90% 触发清理任务并告警 |
| 磁盘（备份目录） | `backup_disk_usage_pct` | > 80% 删除最旧备份并告警 |
| 数据库连接数 | `db_pool_active` / `db_pool_size` | 接近上限告警 |
| Redis 内存 | `redis_memory_used_bytes` | > 80% 告警 |
| 内存（各服务 RSS） | `process_resident_memory_bytes` | 持续增长超基线 2 倍告警（泄漏迹象） |
| 容器磁盘 | `docker_disk_usage_pct` | |

**容量预期**（基于 27.3）：

| 项 | 日增 | 一年 |
|---|---|---|
| K 线 | ~1.5 MB | ~550 MB |
| 聚合成交 | ~10 MB | ~3.6 GB（90 天后清理） |
| 盘口快照 | ~2 MB | ~730 MB |
| Agent 输出 | ~3 MB | ~1.1 GB |
| 快照 | ~10 MB | ~3.6 GB |
| **合计（保留策略后）** | | **约 3–5 GB/年** |

**保留策略（27.4 已要求，此处具体化）**：

| 数据 | 保留期 | 清理任务 |
|---|---|---|
| 1m K 线 | 90 天 | `retention_candles_1m`，每日 03:00 |
| 聚合成交 | 90 天 | `retention_trades`，每日 03:10 |
| 盘口快照 | 30 天 | `retention_books`，每日 03:20 |
| 实时缓存（Redis） | 2 × interval | TTL 自动过期 |
| 其余全部 | 永久 | 不清理 |

> 清理任务必须在 `scheduled_jobs` 中注册，并遵守 23.1.3 的幂等与补跑机制（补跑清理任务是安全的，因为清理是幂等的）。

## 37.8 降级时的可观测性

**系统进入降级状态时必须显式可见**，不得静默：

| 降级 | 可观测信号 |
|---|---|
| LLM 不可用 | `llm_degraded_total{reason=llm_unavailable}` 递增；分析输出 `degraded=true`；推送「降级卡片」 |
| 预算超限 | `llm_daily_cost_usd` 达到上限；`llm_degraded_total{reason=budget}` |
| 交易所不可用 | `market_ws_connected=0`；`market_data_quality_events_total{rule_id=DATA_STALE}` |
| 某渠道不可用 | `notification_channel_health{channel}=0`；该渠道投递全部失败 |
| 数据质量降级 | `market_data_quality_events_total` 递增；快照 `data_quality != GOOD` |

**Dashboard 必须有一个「当前系统状态」总览**，把这些降级状态集中展示（35.4 的 `DEGRADED` 状态）。

---

# 38. 回测与实盘一致性约束（v2.3 新增，P0）

第 6 章区分了 Analysis Backtest 与 Trading Strategy Backtest，但漏掉了一条比 look-ahead bias 更隐蔽的污染源：**回测里用的特征与线上用的特征不是同一套实现**。

## 38.1 问题的严重性

设想：线上用 `pandas_ta` 计算 ATR（`wilder` 平滑），回测里手写了一个 `ewm(alpha=1/14)` 版本。两者数值相差 2%–5%。在 ATR 倍数阈值类规则上（22.2.2 `VOL_ATR_SPIKE`），这足以让「同一段历史」在回测中触发 8 次、在线上触发 3 次。

后果：

1. 回测得出的「策略有效」结论**无法迁移到实盘**；
2. 更糟的是——**看不出来**。没有报错、没有异常，只是数字对不上；
3. 评估体系（第 5 章）会把这两种结果混在一起统计，得出「Prompt 更好」的错误结论。

## 38.2 强制约束

### 38.2.1 单一实现源

| 约束 | 说明 |
|---|---|
| **特征计算唯一实现** | 指标、结构、Regime、支撑阻力**只有一份实现**，位于 `services/market_intelligence/` 与 `domain/market/`。回测、线上评估、实时分析**全部调用同一函数** |
| **禁止回测专用实现** | 回测代码中**不得**出现「为了性能而重写」的指标实现。若线上实现太慢，优化**那一份**，而不是另写一份 |
| **纯函数要求** | 特征计算函数必须满足：`(input_data, params, feature_version) -> features`，无隐藏全局状态、无 I/O、无 `now()` 调用 |

### 38.2.2 `feature_version` 一致性

第 30.3 节已规定 `market_metrics` 与 `market_structure_features` 都存 `feature_version`。v2.3 补充一致性检查：

| 规则 | 说明 |
|---|---|
| 定义 | `feature_version` = 特征计算模块的版本标识（`git short sha` + 语义版本） |
| 回测记录 | 回测结果**必须**记录所用的 `feature_version` |
| 对比检查 | 回测结果与线上结果的对比时，`feature_version` 不同则**必须标注「口径不可比」** |
| 一致性测试 | 存在自动化测试：对同一输入数据，线上路径与回测路径产出的特征**逐值相等**（浮点容差 1e-9） |

### 38.2.3 数据快照对齐

| 规则 | 说明 |
|---|---|
| 回测使用**已落库的历史 K 线**，不从交易所实时拉取 | 保证可复现 |
| 回测在每个时间点只能看到 `t <= as_of` 的数据 | 由「切片输入」实现，而非由调用方自觉 |
| 回测使用的数据**必须经过与线上一致的质量检查** | 数据质量差的 K 线在线上被标 `INVALID` 排除，回测中也要排除 |
| 回测起始点必须有一个**完整的预热窗口** | 指标（如 EMA200）需要足够历史；预热期数据不计入评估 |

**预热窗口**：

| 指标 | 最小预热 |
|---|---|
| EMA 200 | 至少 200 × 周期 |
| ADX 14 | 约 100 × 周期 |
| 滚动分位数（22.2.2 `VOL_RVOL_ZSCORE` 用 30 日） | 至少 30 日 |
| ATR 比值（`VOL_ATR_SPIKE` 用 96h 均值） | 至少 96 小时 |

预热期不足时，回测**拒绝启动**并说明缺多少数据，而不是用不足的样本开始算。

## 38.3 两类回测的一致性要求

| 要求 | Analysis Backtest | Trading Strategy Backtest |
|---|---|---|
| 特征实现唯一 | ✅ 强制 | ✅ 强制 |
| `feature_version` 记录 | ✅ 强制 | ✅ 强制 |
| 预热窗口 | ✅ 强制 | ✅ 强制 |
| 数据质量过滤 | ✅ 强制 | ✅ 强制 |
| 成本模型 | 可选 | ✅ 强制（6.2 要求） |
| 滑点模型 | 可选 | ✅ 强制 |
| Funding 模型 | 可选 | ✅ 强制 |
| 样本外测试 | ✅ 强制 | ✅ 强制 |
| 参数版本记录 | ✅ 强制 | ✅ 强制 |

## 38.4 回测与实盘的差异声明

即使满足上述约束，回测与实盘仍存在**不可消除的差异**。回测报告**必须**声明以下简化：

| 差异 | 说明 |
|---|---|
| 盘口数据缺失 | 若无 L2 历史数据，滑点模型只能是假设 |
| 成交模型 | 无真实撮合；`26.2` 已声明「不做部分成交处理」 |
| 延迟 | 回测假设零延迟，实盘有网络与计算延迟 |
| 资金费率 | 回测用历史费率，实盘费率可能不同 |
| 规则集 | 线上规则阈值在持续校准（22.2），回测用的是**固定快照** |
| AI 分析 | 回测若调用 LLM，结果不可复现；**回测应使用录制的模型输出**（36.6） |

**报告模板必须包含「简化假设」区块**（26.2 已要求「每一条都必须标注为简化假设」），逐条列出并在结论中体现其影响方向。

## 38.5 回测隔离

| 规则 | 说明 |
|---|---|
| `run_backtest` 只能创建隔离研究实验 | 6.2 硬约束，不得修改正式模拟账户或发布策略 |
| 回测不写 `signals` / `prediction_evaluations` | 回测结果写入 `experiment_results`，与线上评估完全隔离 |
| 回测不触发通知 | 回测失败也不推送（避免污染真实推送统计） |
| 回测有独立资源限额 | 单次回测最大 CPU 时间与内存占用（37.7） |
| 回测幂等 | 幂等键 = `{实验配置哈希}:{数据区间}:{feature_version}:{evaluator_version}` |

## 38.6 一致性验收

- [ ] 同一输入数据，线上路径与回测路径的特征输出**逐值相等**（自动测试）。
- [ ] 回测结果记录 `feature_version`，与线上对比时版本不同会给出「口径不可比」标注。
- [ ] 预热窗口不足时回测拒绝启动并说明缺口。
- [ ] 回测产物只落在 `experiment_results`，不污染 `signals` 与 `prediction_evaluations`。
- [ ] 回测不产生任何 `notification_events`。
- [ ] 回测报告包含完整的「简化假设」区块。
- [ ] 回测中 AI 分析使用录制输出，同一实验重跑结果完全一致。

---
