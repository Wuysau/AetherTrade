# AetherTrade 记忆体系改造：Agent 可执行工作包

版本 1.0 · 2026-11-04\
依据：《AetherTrade 整体记忆体系架构设计蓝图》\
参考基线：`public-snapshot` /
`b15a825f51630f59efe43371915a066ff17ba59d`\
状态：待评审、待执行；不代表代码已实现或测试已通过。

## 0. 最终交付目标

1.  每个策略拥有独立、持久化、版本化的记忆状态。
2.  历史决策、市场假设、计划演变和执行事实可追溯。
3.  每轮模型调用都从可信来源构造完整、冻结、可复现的上下文。
4.  单模型、交叉分析、RULE、HYBRID 共享同一策略状态和事实来源。
5.  服务重启、模型切换、人工干预、部分成交及 UNKNOWN
    恢复后，决策连续性不丢失。
6.  上下文压缩不会删除风险边界、活动订单、有效计划或失效条件。
7.  记忆缺失、冲突、过期或作用域不匹配时明确报告，并阻止新增风险。
8.  不建设第二套订单/持仓账本，不引入无必要的独立记忆基础设施。

本任务解决决策上下文连续性，不以降低 HOLD
比例或增加交易频率为验收目标。不得通过强制
BUY、降低风控或忽略数据质量制造交易。

## 1. Agent 执行总规则

### 1.1 基线校准

本工作包依据 `b15a825f` 形成，执行时必须以实际最新工作树为准。Agent
开始前：

-   检查当前分支、HEAD、工作区、Worktree 和未提交修改。
-   阅读根目录及相关子目录 `AGENTS.md`、正式 SPEC、ADR、迁移和测试规约。
-   检查
    `analysis_snapshot.py`、`program_model_context.py`、`decision_assessment.py`、`automation.py`、`model_service.py`、`model_client.py`
    及现有决策/执行仓储。
-   核对当前是否已有策略状态、记忆事件、Context Checkpoint
    或相关迁移，避免重复创建。
-   对照本工作包逻辑接口；若当前代码已实现，提供源码位置和测试证据，不重复造轮子。
-   若与正式 SPEC、订单 ADR 或工作树改动冲突，先报告，不擅自覆盖。

### 1.2 资源与主机安全

AetherTrade 曾发生 Docker/WSL 虚拟磁盘资源事故。所有任务必须遵守：

-   优先使用现有轻量测试环境；不因记忆功能自动创建新 Docker
    容器、镜像或数据库卷。
-   默认一次只运行一个重型数据库/容器测试任务。
-   重型测试前报告磁盘可用空间、Docker
    资源数量、预计新增空间和运行时间。
-   建议初始预算：单次重型任务新增不超过 5 GiB，单阶段累计不超过 15
    GiB；D 盘可用空间低于 40 GiB 时暂停新增大型文件。若现行 AGENTS.md
    更严格，采用更严格值。
-   未经用户针对具体路径和对象明确批准，禁止 Docker prune、卷删除、VHDX
    移动/压缩/删除、WSL unregister、Docker
    reset、跨盘迁移、递归/强制删除。
-   测试证据保存到受控目录并限制大小，不无限保留 runner
    容器或重复下载依赖。
-   失败最多按任务预设次数重试；不能循环重跑直到通过。
-   暂停指令意味着停止新增任务，不代表授权清理。

### 1.3 工程规则

-   一个任务一个明确写入者；公共 Schema、数据库迁移和 reducer 不允许多个
    Worktree 并行修改。
-   每个任务先写失败测试，再实现，再跑定向回归。
-   不允许删除、跳过或弱化现有安全测试来获得绿色结果。
-   不保存模型隐藏推理链；只保存结构化判断、理由、证据引用和必要审计输入。
-   模型可写入内容均视为不可信提议，必须经过服务端校验。
-   交易所/执行账本仍是订单、成交、持仓和费用的权威来源。
-   每个任务完成后报告修改文件、测试命令、退出码、资源变化、未完成项、风险和提交
    SHA。
-   状态必须区分 PASS / FAIL / BLOCKED / NOT_RUN。

## 2. 总体工作包与依赖

  -----------------------------------------------------------------------------------------------------------------
  工作包            名称                       交付                                依赖
  ----------------- -------------------------- ----------------------------------- --------------------------------
  MEM-00            基线校准与现状盘点         能力映射、差距报告                  无

  MEM-01            记忆领域契约与作用域       事件、状态、Thesis、Plan、Context   MEM-00
                                               类型                                

  MEM-02            PostgreSQL 持久化与迁移    Schema、索引、迁移/回滚             MEM-01

  MEM-03            事件写入与状态投影         append-only event、reducer、CAS     MEM-02

  MEM-04            决策历史与上下文检查点     输入/输出/版本/来源可追溯           MEM-01、MEM-02

  MEM-05            交易事实关联投影           订单、成交、保护、人工事件关联      MEM-03、MEM-04

  MEM-06            Strategy Memory Service    统一读取策略状态、计划、假设        MEM-03、MEM-05

  MEM-07            Context Assembler 与       冻结决策上下文                      MEM-04、MEM-06
                    Validator                                                      

  MEM-08            历史检索、摘要和 Token     有界相关记忆选择                    MEM-07
                    预算                                                           

  MEM-09            Prompt Framework           分层指令和记忆使用协议              MEM-01、MEM-07
                    与版本管理                                                     

  MEM-10            单模型决策链集成           单模型使用跨轮上下文                MEM-07～MEM-09

  MEM-11            交叉分析与多模式集成       同快照、同记忆、不同角色            MEM-10

  MEM-12            复查调度与增量事件         变化触发复查，避免空转              MEM-06、MEM-07、MEM-10

  MEM-13            重启、模型切换和任务恢复   持久化恢复与幂等                    MEM-03～MEM-12

  MEM-14            经验记忆与知识治理         审核后的复盘经验                    MEM-05、MEM-10、MEM-13

  MEM-15            UI/API 可观测性            记忆、历史、变化、缺失和冲突展示    MEM-04、MEM-06、MEM-07、MEM-10

  MEM-16            安全、备份与恢复验收       集成验收和运维手册                  MEM-02～MEM-15
  -----------------------------------------------------------------------------------------------------------------

推荐依赖顺序： - 串行：MEM-00 → MEM-01 → MEM-02 → MEM-03。 - MEM-04 在
MEM-02 后可独立开发，但需独立表/文件所有权。 - MEM-05 → MEM-06 → MEM-07
→ MEM-08。 - MEM-09 可在契约稳定后并行设计，最终由 MEM-10 集成。 -
MEM-10 → MEM-11；MEM-12 可并行实现但由单一调度模块负责人集成。 - MEM-13
依赖核心链完成；MEM-14、MEM-15 可在接口稳定后并行。 - MEM-16
最后统一验收。

以上是工程依赖，不是缩水版本；所有工作包共同构成完整目标。

## 3. 详细任务卡

每张任务卡必须交付：实际修改文件、接口/Schema、迁移、测试、执行命令、退出码、资源增量、未完成项、风险和提交
SHA。

### MEM-00：基线校准与现状盘点

**目标：** 防止按旧提交重复开发或覆盖现有改动。

**检查：** - 当前分支、HEAD、工作区、Worktree、未提交变更。 -
`AGENTS.md`、SPEC、ADR、迁移和测试规约。 -
决策快照、模型上下文、决策校验、自动化、模型客户端和仓储。 -
当前决策、执行、策略配置和订单仓储。 - `decision/memory.py`
是否仍是进程内参考实现。 -
`previous_assessment`、`previous_proposal`、策略状态和历史查询的真实接入路径。

**产出：** - `docs/architecture/memory/current-state.md` -
`existing-capability-map.json` - `gap-and-conflict-report.md`

**验收：** 每个已实现能力有源码和测试证据；缺口标记 NOT_IMPLEMENTED /
PARTIAL / IMPLEMENTED_UNVERIFIED / VERIFIED；不改产品代码、不跑重型
Docker 测试。

### MEM-01：记忆领域契约与作用域

**目标：** 定义统一、版本化、与模型供应商无关的记忆对象。

**逻辑类型：**
`StrategyMemoryScope`、`StrategyIdentity`、`StrategyState`、`StrategyMemoryEvent`、`MarketThesis`、`StrategyPlan`、`DecisionHistoryRef`、`ExecutionFactRef`、`ValidatedKnowledge`、`DecisionContext`、`DecisionContextCheckpoint`。

**必须表达：** - tenant/user/account_scope/strategy/product/instrument
作用域。 - `state_revision`、schema revision、策略版本。 - Thesis
状态：ACTIVE / WEAKENED / INVALIDATED / EXPIRED / SUPERSEDED。 - Plan
状态：PROPOSED / ACCEPTED / ACTIVE / REVISED / CANCEL_PENDING /
COMPLETED / EXPIRED / REJECTED。 -
事实、推断、历史判断、过期信息的类型区分。 -
来源引用、发生时间、记录时间、有效时间。 - 模型不可写的授权和风险字段。

**禁止：** 复制第二份订单/成交账本；用一个无类型 JSON
字段承载所有记忆；以 provider conversation ID
作为策略主键；将模型自报信心视为统计胜率。

**测试：** 跨账户/策略/产品隔离；未知 schema
revision；非法状态转换；缺失来源；Decimal/时间/ID
序列化；模型输出不得覆盖授权字段。

### MEM-02：PostgreSQL 持久化与迁移

**目标：** 将记忆状态从进程内存变为可恢复的持久化数据。

**逻辑表（按现有 schema 命名规范调整）：** - `strategy_memory_events` -
`strategy_memory_state` - `market_theses` - `strategy_plans` -
`decision_context_checkpoints` - `memory_knowledge`

决策运行与分析记录优先扩展现有表；只有 MEM-00 证明无法表达时才新增表。

**要求：** - 使用现有 migration 工具和版本分配机制。 -
外键、唯一约束、scope/时间/revision 索引。 -
事件追加写入；状态投影可重建。 -
CAS：`UPDATE ... WHERE state_revision = expected_revision`。 - payload
带 schema revision，保留原始事件。 -
迁移可重复检查、可验证、可回滚；回滚不得删除业务事件。 -
事件和投影同事务更新，或使用可靠 outbox/reducer 防止不一致。

**测试：** 空库升级、现有库升级、重复迁移、中断恢复、原子性、CAS
冲突、备份恢复及事件/hash/revision 校验。

### MEM-03：事件写入与状态投影

**目标：** 以追加事件为依据维护当前策略状态。

**接口建议：** -
`append_event(scope, event, idempotency_key) -> EventReceipt` -
`reduce_event(state, event) -> StateTransition` -
`load_state(scope) -> StrategyState` -
`rebuild_projection(scope, from_event_id=None) -> RebuildReport`

**事件至少覆盖：** - STRATEGY_CREATED / STRATEGY_CONFIG_CHANGED /
STRATEGY_PAUSED / STRATEGY_RESUMED。 - THESIS_CREATED / THESIS_UPDATED /
THESIS_INVALIDATED / THESIS_EXPIRED。 - PLAN_PROPOSED / PLAN_ACCEPTED /
PLAN_REVISED / PLAN_REJECTED / PLAN_EXPIRED。 -
WATCH_CONDITION_TRIGGERED、MANUAL_INTERVENTION。 -
执行事件使用现有订单/成交事件 ID 引用，不复制订单事实。

**要求：** 事件有唯一
ID、scope、source、occurred_at、recorded_at、schema
revision；重复投递幂等；迟到事件按规则处理；投影可重建；事件与 state
revision 原子一致。

**测试：** 重复、乱序、迟到、并发事件；重建前后 state hash
一致；未知事件版本明确拒绝或隔离。

### MEM-04：决策历史与上下文检查点

**目标：** 记录每次模型实际看到什么、使用什么版本、输出了什么。

**扩展现有决策记录：** - run_id、strategy scope、model/provider。 -
Prompt/schema/policy 版本。 - market snapshot ID/hash、account/portfolio
revision。 - memory state revision、引用的 event/thesis/plan IDs。 -
实际最终输入 artifact ref、input hash、token estimate。 -
输出结构化提案、evidence claims、review result。 - 风控和执行结果引用。

**要求：** - 保存脱敏后的实际模型输入，或可确定性重建的完整引用集合。 -
hash 只证明一致，不能代替实际内容。 - 不保存隐藏推理链。 -
拒绝、超时、截断和解析失败也要记录。 - API Key、OAuth
token、签名和账户凭据不得进入 checkpoint。

**测试：** 给定 checkpoint 可还原当时输入；hash
不符标记证据损坏；模型/Prompt/schema
升级后旧记录可读；超长输入不得悄悄截断风险字段。

### MEM-05：交易事实关联投影

**目标：** 将交易生命周期事实接入记忆，但不建立第二份账本。

**关联来源：** Trade
Intent、订单、订单列表、条件单、执行算法、成交、费用、保护状态、撤单、UNKNOWN、人工接管和账户对账。

**要求：** - 保存来源系统主键和 scope，不复制可变订单状态作为权威。 -
以执行事件 ID、订单 ID、成交 ID、allocation ID 等关联。 -
区分命令已提交、交易所已接受、部分成交、完全成交、撤单已确认。 -
人工干预必须形成显式事件。 - 订单/账户事实未对账时标记 UNKNOWN 或
STALE，不让模型将其当成确定事实。

**测试：**
重复成交事件、迟到撤单回报、部分成交、人工减仓、UNKNOWN、交易所对账修正；记忆投影与执行账本对照一致。

### MEM-06：Strategy Memory Service

**目标：** 提供统一、只读为主的策略记忆访问接口。

**接口建议：** - `get_strategy_state(scope) -> StrategyState` -
`get_active_thesis(scope) -> MarketThesis | None` -
`get_active_plan(scope) -> StrategyPlan | None` -
`list_recent_decisions(scope, limit, since) -> list[DecisionHistoryRef]` -
`list_events_since(scope, cursor) -> list[StrategyMemoryEvent]` -
`get_execution_facts(scope, plan_id) -> ExecutionFactSummary`

**要求：** 读取必须按完整 scope
过滤；写操作只通过受控事件服务；返回来源和
revision；缓存只能优化性能，不能成为事实来源。

**测试：** 多策略同标的隔离、跨账户拒绝、revision
一致、服务重启后数据可恢复、缓存清空后结果不变。

### MEM-07：Decision Context Assembler 与 Validator

**目标：** 每轮决策都从可信事实和策略记忆构造统一、冻结的上下文。

**入口：**
`DecisionContextAssembler.build(strategy_id, run_id, as_of) -> DecisionContextCheckpoint`。

**输入：** 1. 当前策略身份、版本和授权摘要。 2.
当前行情快照、来源、时间、完整度。 3.
当前账户、持仓、活动订单、预留资金和保护状态。 4. 当前有效 Thesis 与
Plan。 5. 上一轮决策、等待条件和失效条件。 6.
最近相关决策及计划变化原因。 7. 自上次决策以来的事件摘要。 8.
与本轮相关的已验证知识。 9. 本轮新增证据与旧假设的差异。

**Validator 检查：** - scope 与策略/账户/产品一致； - market/account
数据新鲜度； - 当前策略和 state revision； - 活动订单与保护状态； -
记忆来源可解析； - 过期假设不得作为当前事实； -
历史计划与真实执行状态不冲突； - 必需字段齐全。

**产出：** 不可变 checkpoint，含实际输入制品引用、hash、state
revision、snapshot ID、Prompt/schema/model/policy 版本和记忆条目来源。

**测试：**
同一输入可确定性重建；账户状态变化使旧上下文失效；过期行情不被注入；缺失有效计划/订单事实时正确阻断新增风险；所有模型模式取得相同
checkpoint ID。

### MEM-08：历史检索、摘要与 Token 预算

**目标：** 让模型看到相关历史，而不是无限堆叠所有历史文本。

**检索优先级：** 1. 当前有效计划与 Thesis。 2. 上一轮完整决策。 3.
自上一轮以来的关键事件。 4. 最近若干条与当前标的/假设/计划相关的决策。
5. 已验证知识。 6. 更早历史按需检索。

**规则：** - 先使用 PostgreSQL 结构化查询、索引和全文检索。 -
暂不引入向量数据库；只有离线评估证明语义检索明显改善召回和精度时再提议
pgvector。 - 摘要必须保留来源 ID、时间和原始记录引用。 -
不允许模型摘要覆盖原始事件。 - Token 预算可配置、可观测。 -
压缩顺序：无关历史 → 重复内容 →
旧决策细节；不可压缩风险边界、当前订单、保护责任、当前计划和关键失效条件。 -
必需上下文超预算且无法安全压缩时返回
`CONTEXT_INCOMPLETE`，不得新增风险。

**测试：**
预算边界、相关性排序、过期条目过滤、摘要引用完整性、风险字段保留、不同模型
tokenizer 估算偏差。

### MEM-09：Prompt Framework 与版本管理

**目标：** 将"记忆保存什么"与"模型如何使用记忆"分开。

**分层指令：** - P0 System Constitution：事实/推断区分、权限边界。 - P1
Trading Philosophy：机会评估、风险收益与不交易原则。 - P2 Strategy
Profile：市场、周期、交易风格和授权。 - P3 Market Research
Protocol：趋势、波动、量能、流动性、成本。 - P4 Memory Context
Protocol：比较旧假设与新证据，识别计划漂移。 - P5 Decision
Protocol：维持、调整、减仓、退出、重规划。 - P6 Output
Contract：结构化提案、证据、有效期、观察条件。

**要求：** - Prompt
作为有版本的模板和配置存储，记录版本、hash、适用模式和变量 Schema。 - 旧
Prompt 版本可用于审计，不静默覆盖历史。 -
单模型、复核模型、复盘任务采用不同角色模板，但共享相同核心事实和安全约束。 -
模型必须说明上一轮假设、仍有效证据、新增证据、计划是否仍成立及原因。 -
不强制交易，不把自报信心当统计胜率。 - Prompt
不得赋予模型修改风险预算、账户身份或执行权限的能力。

**测试：**
模板版本固定、变量缺失、注入恶意记忆内容、模型切换、旧决策可重放。

### MEM-10：单模型决策链集成

**目标：** 将新记忆和上下文接入真实单模型分析入口。

**流程：** 1. 创建 run 并锁定策略 scope/revision。 2.
获取当前市场与账户快照。 3. 调用 Context Assembler。 4. 保存
checkpoint。 5. 组装 P0-P6 Prompt。 6. 调用模型并保存原始结构化输出。 7.
校验证据、计划和状态转换。 8. 进入现有确定性风险与执行链。 9.
按实际结果追加事件并更新 Strategy State。 10.
保存本轮与上一轮的差异报告。

**要求：** - 不能只修手动分析；自动调度入口必须调用同一服务。 -
模型调用失败不得伪装成 HOLD。 - 模型输出无效不得写成有效计划。 -
提案未通过风控不能变成 ACTIVE plan。 -
只有执行事实才能更新订单/持仓状态。

**测试：**
连续多轮决策；无新证据时维持逻辑；新证据导致合理修订；模型拒绝/超时/解析错误；同一
checkpoint 重放；自动与手动入口一致。

### MEM-11：交叉分析与多模式集成

**目标：** 单模型、全票、证据复核、RULE、HYBRID 共用同一策略记忆。

**要求：** - 同一轮所有模型使用同一冻结 checkpoint。 -
复核模型额外获得主模型本轮候选提案及证据引用。 -
保存各模型原始意见，不互相覆盖。 - 分歧、失败、数据不足和有效 HOLD
分开记录。 - 交叉裁决不能改变策略授权、风险边界和账户事实。 -
RULE/HYBRID 也从同一 Strategy State 读取当前计划和执行事实。 -
不允许每个模型单独建立一份互相冲突的长期记忆。

**测试：**
同快照、多模型不同意见；某个模型失败；模型切换；全票与证据复核；AI/RULE/HYBRID
同一策略状态；跨策略隔离。

### MEM-12：复查调度与增量事件

**目标：** 让复查基于状态变化，而不是无意义地重复调用模型。

**触发来源：** - 新的已收盘 K 线； - 价格/量能/波动观察条件触发； -
订单部分成交、完全成交、撤单或过期； - 保护状态变化； -
账户或人工干预； - 计划到期或 Thesis 失效条件触发。

**要求：** - 保存 last_processed_event_cursor。 -
同一事件只触发一次有效复查，使用幂等键。 - 合并短时间内大量无意义 tick
触发。 - 区分"需要刷新数据"与"需要重新评估交易假设"。 -
数据过期不能自动变成买卖信号。 - 设置模型调用频率和成本预算；不得因连续
HOLD 反复重问直到 BUY。

**测试：**
重复事件、乱序事件、触发风暴、数据源恢复、事件合并、调度重启、复查取消。

### MEM-13：重启、模型切换和任务恢复

**目标：** 服务重启和模型更换后，策略记忆连续且不会重复交易。

**恢复流程：** 1. 读取未完成 decision_runs 和最新 state revision。 2.
从执行账本/交易所对账恢复订单、持仓、保护责任。 3. 验证策略版本、owner
epoch、授权和状态。 4. 重建 Thesis/Plan 投影。 5.
获取新的行情快照并验证时效。 6. 构建新 checkpoint。 7.
恢复可安全恢复的分析任务；不确定的交易写操作进入 UNKNOWN 查询。

**禁止：** - 复用旧行情作为当前行情； - 因模型超时而重复执行交易； -
未核实 UNKNOWN 就释放预算或重下单； - 模型切换时重置 Strategy State； -
恢复过期或被人工撤销的计划。

**测试：**
模型调用中断、模型返回后数据库写入前崩溃、事件写入后投影更新前崩溃、重启时有
GTC/部分成交/UNKNOWN、切换 provider、owner epoch 改变、数据库备份恢复。

### MEM-14：经验记忆与知识治理

**目标：**
把历史复盘沉淀为有证据、有适用范围的知识，而不是让模型自行形成永久规则。

**输入：** 已完成交易、经过对账的
PnL/费用、历史决策和预先定义的评估窗口。

**状态：** PROPOSED / VALIDATED / REJECTED / EXPIRED。

**要求：** - 单笔交易不能直接形成普适经验。 -
每条经验必须包含市场、产品、周期、策略版本、样本量、统计区间、证据引用和验证时间。 -
区分相关性与因果性。 -
模型可提出复盘假设，但由确定性统计或用户审核批准。 - 已验证知识可被
Context Assembler 按相关性检索。 -
经验过期或适用环境变化时，不得继续作为当前事实。

**测试：**
小样本不通过验证；样本范围不匹配不检索；知识过期；模型不能自行把
PROPOSED 改为 VALIDATED；同一经验去重和版本演进。

### MEM-15：UI/API 可观测性

**目标：** 让用户能看见 Agent
每轮决策到底使用了什么上下文，以及为什么改变判断。

**界面/接口至少展示：** - 当前策略记忆状态和 revision。 - 当前有效
Thesis 与 Plan。 - 上一轮结论、本轮新增证据、变化原因。 -
本轮实际使用的行情快照、账户 revision、Prompt/model/schema 版本。 -
引用的历史记忆条目和来源。 - 上下文完整度、过期项、冲突项、Token
估算。 - 数据阻断、记忆缺失、模型失败、复核失败、风险拒绝的不同状态。 -
历史决策时间线和计划版本差异。

**安全：** 不显示密钥、OAuth
token、签名或其他账户敏感凭据；不同用户/账户/策略必须严格隔离。

**测试：** API scope 授权、跨策略访问拒绝、checkpoint
详情正确、旧版本只读、前端状态不把 DATA_BLOCKED 显示成普通 HOLD。

### MEM-16：安全、备份与恢复验收

**目标：** 证明记忆可以恢复、可以审计且不会影响交易安全。

**验收内容：** - 事件与状态投影一致性。 - 全量/增量备份和 PostgreSQL
恢复演练。 - 服务重启和 provider 切换。 - 多策略同标的隔离。 -
记忆损坏、缺失、超预算和冲突。 - UNKNOWN 订单、部分成交和人工干预。 -
权限、保留策略和敏感数据审查。 - 完整回归和资源预算审查。

**交付：** - `docs/operations/memory-recovery-runbook.md` -
`docs/architecture/memory/acceptance-report.md` -
`docs/architecture/memory/data-retention-policy.md`

数据库灾难恢复必须以实际备份恢复证据为准，不得将进程重启测试冒充灾难恢复通过。

## 4. 公共接口与责任边界

建议的逻辑接口（实际路径按 MEM-00 结果调整）：

``` python
class StrategyMemoryService:
    def get_state(self, scope): ...
    def append_event(self, scope, event, idempotency_key): ...
    def get_active_plan(self, scope): ...
    def list_recent_decisions(self, scope, limit, since): ...

class DecisionContextAssembler:
    def build(self, strategy_id, run_id, as_of): ...

class ContextValidator:
    def validate(self, context): ...

class StrategyStateReducer:
    def apply(self, state, event): ...
    def rebuild(self, scope, cursor=None): ...
```

责任边界： - 模型输出只能成为待校验的 Assessment/Proposal。 -
策略状态由受控服务和 reducer 更新。 -
订单、成交、持仓、费用由现有执行账本负责。 - Context Assembler
只读组合事实，不创建交易订单。 - Provider conversation/session ID
仅可选优化，不作为记忆权威来源。

## 5. 关键验收场景

  ID    场景                 预期
  ----- -------------------- ----------------------------------------------
  T01   连续多轮分析         第二轮能准确引用第一轮有效 Thesis/Plan
  T02   无新证据             不无理由改变方向；允许有证据的重新评估
  T03   新反向证据           识别旧假设弱化/失效并给出原因
  T04   服务正常重启         恢复有效状态、计划和观察条件
  T05   模型切换             DeepSeek/GPT/Claude 使用同一 Strategy State
  T06   同一标的多个策略     账户、计划和风险记忆严格隔离
  T07   限价单未成交         继续管理原计划、有效期和资金占用
  T08   部分成交             以真实净成交量更新计划和保护责任
  T09   人工减仓/暂停        人工事实优先，旧模型计划不能覆盖
  T10   UNKNOWN 订单         对账前不释放预算、不重发
  T11   历史行情过期         不注入为当前市场事实
  T12   上下文超预算         先压缩非关键历史；必需信息丢失则阻断新增风险
  T13   交叉模型             同一轮共享 checkpoint，保留各自原始意见
  T14   数据库恢复           状态投影可从事件重建，订单不重复
  T15   Prompt/schema 升级   旧决策仍可按原版本审计
  T16   记忆来源损坏         标记 CONTEXT_INCOMPLETE，不猜测、不新增风险
  T17   触发事件重复         幂等处理，不重复调用或写入决策
  T18   记忆注入恶意指令     作为不可信数据，不改变系统规则或授权

所有测试需记录固定
commit、数据库版本、测试命令、退出码、测试证据、资源增量和未运行原因。

## 6. 不引入的技术

当前不引入： - 独立向量数据库、图数据库或外部 Memory SaaS； -
Redis、Kafka 或独立 Memory 微服务； - 复杂多 Agent 记忆管理器； -
自动训练/自动修改策略参数； - 无限会话历史和全量历史重放； - 把所有 K
线和订单事件复制到 Prompt。

先用 PostgreSQL
结构化查询、事件日志、状态投影、上下文检查点和有边界的历史检索。未来只有在离线评估证明语义检索有明显收益时，再评估
pgvector。

## 7. Agent 执行与交付流程

每个 MEM 工作包都按以下步骤执行：

1.  检查依赖任务是否已合并且验收。
2.  检查工作区和资源预算。
3.  建立独立 Worktree；公共 Schema/迁移/reducer 同时只允许一个写入者。
4.  先写测试并确认目标行为失败。
5.  实现最小完整任务范围，不扩大到无关重构。
6.  运行定向测试和必要回归；重型测试前取得资源确认。
7.  审查 diff、迁移、数据安全和调用链。
8.  更新任务状态及验收证据。
9.  形成独立 commit，交由主 Agent/用户审查后集成。
10. 不自动清理 Worktree、容器、卷、镜像或测试证据。

每个任务完成报告必须包含： - 实际修改文件； -
新增/变更接口和数据库迁移； -
测试命令、退出码、PASS/FAIL/BLOCKED/NOT_RUN； - 磁盘及 Docker
资源变化； - 未完成项、风险和回滚方式； - commit SHA 和依赖关系。

## 8. Definition of Done

只有同时满足以下条件，记忆体系才算完成：

-   所有决策入口使用统一 Context Assembler。
-   每轮实际输入均有可追溯 checkpoint。
-   Strategy State、Thesis、Plan 和事件在 PostgreSQL 持久化。
-   服务重启和模型切换后可重建上下文。
-   订单/成交/持仓只引用权威执行账本。
-   人工干预和新事实能正确修订旧计划。
-   多策略、多账户隔离通过测试。
-   Token 预算和冲突处理不会丢失关键事实。
-   经验知识有审核和证据，不会由模型自行升级。
-   所有 18 项验收场景有真实测试结果。
-   备份恢复有演练证据。
-   资源治理和主机操作边界通过实际验证。

**最终原则：记忆必须连续，事实必须可验证，计划必须可追溯，执行必须独立受控。**
