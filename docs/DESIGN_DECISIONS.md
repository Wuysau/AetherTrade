# 统一 PAPER 的设计决定

本文记录 2026-10-27 任务 D 的学习要点；是应用责任与证据边界说明，不改变已批准 SPEC、原验收编号或 LIVE-ready 状态。当前源码基线 `97c4f9d`；完整组合验收为 **NOT_RUN**。状态与缺口见 [acceptance.md](acceptance.md)，操作流程见 [TRADE_LIFECYCLE.md](TRADE_LIFECYCLE.md) 和 [FAILURE_PLAYBOOK.md](FAILURE_PLAYBOOK.md)。

## 1. 先 durable，再 side effect

如果先发单再记账，进程在两者之间终止，系统会遗忘一笔可能已经被接受的操作。采用 [ReservationService](../src/trading_system/risk/reservations.py) 的原子业务事务保存 intent、风险、资金预留、immutable command 与 outbox；[Dispatcher](../src/trading_system/execution/dispatcher.py) 在调用引擎前提交 `MAY_HAVE_SENT`，提交后再次检查当前权限、epoch、风险和 deadline。业务数据库事务在外部调用前关闭，避免网络等待占住资金/owner数据库锁。

代价是进程也可能在 durable 后、实际发送前死亡；此时宁可留下保守 UNKNOWN、占用风险预算并查询原 ID，也不能推断未发送然后盲重发。`DURABLE_READY` 与 `MAY_HAVE_SENT` 的边界是持久事实，不由 HTTP handler 的异常位置推算。结果持久化失败也保留原 durable 义务。[test_durable_dispatch.py](../tests/integration/test_durable_dispatch.py) 保留提交 ACK 丢失、强制终止和重投递反例。

## 2. 至少一次 outbox + 对账，不承诺跨交易所 exactly-once

[OutboxConsumer](../src/trading_system/execution/outbox.py) 可以重复收到同一 `COMMAND_READY`，但消息自身没有发送许可；它经 dispatcher读取 durable 命令状态，只有保存了结果才写消费收据。单个 PostgreSQL 原子事务及唯一约束可以保证本库事实的原子性；它无法原子提交另一数据库或交易所的经济效果。

网络超时、ACK丢失、交易所 ID保留窗口、查询覆盖与不同产品的撤改单语义都不受本库事务控制。策略是稳定原 ID、不可变请求 hash、受控 owner、命令结果去重、fill原生 ID去重、归档原始证据和有界 reconcile。重复请求返回原结果不等于重新授予权限；空查询不是已撤单/空仓证明。[Reconciler](../src/trading_system/recovery/reconciler.py) 需要 scope、操作、覆盖、时效与可信 attestation，缺证据保持 UNKNOWN。

PaperEngine 也用独立数据库模拟这一断点：模拟提交与业务命令结果不是同一事务。测试可证明某个注入场景下的应用行为，不能证明跨所有 venue 的 exactly-once。[paper_client_order_id](../src/trading_system/engine/paper.py) 是确定性的 PAPER 编码，不是已完成资格审查的交易所 ID协议。

## 3. 目标、资金与数量分开核算

AI、RULE、HYBRID 和人工表达目标，不直接持有发送能力。[TargetCompiler](../src/trading_system/intents/target_compiler.py) 的安全差量基于 actual与durable在途量；[ReservationService](../src/trading_system/risk/reservations.py) 在同一 collateral 事务重算，防止两个请求都看到相同余额/实际仓位。资金预留是资金义务，不能拿它的数值代替订单数量。

早期 READY 命令、零资金占用的减仓和 UNKNOWN 也占数量义务；终态标签不足以清掉它。[TargetReportService](../src/trading_system/risk/target_guard.py) 校验归档、终态原单、去重fill及实际持仓关系。当前保守的资金占用可能拒绝本可接受的新增目标，但不会低估占资；今后若优化，必须单独证明不会双花。[原子目标的红绿记录](evidence/2026-10-27-paper-atomic/README.md)说明这项取舍。

## 4. 一个执行 owner，当前权限在最终边界再次验证

[ExecutionOwner](../src/trading_system/runtime/execution_owner.py) 管理固定 shard 的互斥 session、guard、故障 latch 与恢复；[ownership registry](../src/trading_system/ownership/registry.py) 管理持仓策略所有权，两者不是同一个概念。lease/epoch 是逻辑证据，不能替代跨主机 fencing。Linux flock 只证明该固定主机/文件系统上的排他，不能证明旧容器/主机已失去凭据。

权限定时失效、数据库锁等待、撤权和所有权切换可能发生在初次校验后。数据库 CAS 和 [PaperEngine](../src/trading_system/engine/paper.py) 在锁后、写入前和最终提交边界重查证据；排他 guard须将撤权与side effect串行化。不能为了让 UI/CLI 工作而提供第二发送通道，也不能让模型或用户提供 trusted reader/permit。

## 5. 保护与退出不是模型的附带动作

[FillLedger](../src/trading_system/execution/fills.py) 的 durable 事件通知保护重查；[ProtectionManager](../src/trading_system/protection/manager.py) 保存 gap、原因锁、命令与恢复事项，然后才调度动作。部分成交马上产生保护责任，模型失败或HTTP客户端离线不能撤销它。

保护创建ACK不表示止损已有效覆盖；[NativeVerifier](../src/trading_system/protection/native.py) 要求相符、当前且已归档的订单/持仓/能力证明。[ExitService](../src/trading_system/protection/exit.py) 在明确 flat proof前保留 EXITING。无法交易的残余、停牌或失败退出须如实显示。[RiskLockService](../src/trading_system/risk/locks.py) 的cause锁不随价格反弹、重启、一次成功保护或换日自动解除；解除有独立授权与证据流程。

`NO_NEW_RISK` 允许经验证的保护修复与减仓，`EXIT_ONLY` 仅允许真实减险/对账。[LockedActionVerifier](../src/trading_system/risk/locked_actions.py) 检查当前归属、数量、方向、授权、UNKNOWN冲突和动作证据；“叫做CLOSE”不是减少风险的证明。

## 6. Nautilus 与应用各自负责什么

| 边界 | 当前责任与限制 |
| --- | --- |
| 应用 | 账户/环境/能力与授权、证据新鲜度、目标编译、风险/资金预留、durable命令/outbox、执行所有权、业务事件与fill账本、保护责任、退出与恢复、UI/CLI投影 |
| EnginePort / adapter | 在受控边界翻译已授权命令、稳定原ID、产品订单编码、接收/查询/规范化引擎事实；将来合格适配器的签名/传输也只能在此边界 |
| 当前 [NautilusEngine bridge](../src/trading_system/engine/nautilus/bridge.py) | 显式离线记录边界，没有已资格审查的native客户端/签名/传输；`live_enabled=False`，`enable_native_path`保留`NATIVE_PATH_NOT_QUALIFIED` |
| 当前 [PaperEngine](../src/trading_system/engine/paper.py) | 无交易所网络/账户凭据的独立durable模拟场所，明确事件注入与独立角色；不能证明流动性、滑点、真实保护或交易所资格 |

即使未来 Nautilus接入成功，也不会替应用决定风险、持仓所有权或证明退出完成。不得把native bypass当PAPER修复。Spot、USD-M和Stocks仍在最终范围，能力矩阵、持仓方向、最小数量、保护、交易时段/停牌、结算等要求分别审查；一个产品的结果不能替另一产品准入。

## 7. 旧控制台数据保留真实来源

旧 `Store._order` 的立即FILLED记录是本地合成兼容账本；不能回填为新统一链的venue/fill证据。迁移必须标注legacy来源、保存原账与映射、对账后显式转移控制，不能静默伪造已验证保护/账户覆盖。统一控制台接线未通过时，应清楚显示 pending/unavailable及旧投影来源，不兜底走旧快捷写入。

## 8. 分开“存在测试”和“已通过资格”

已提交的 [atomic evidence](evidence/2026-10-27-paper-atomic/README.md) 与 [PaperEngine provisioning/security evidence](evidence/2026-10-27-paper-engine-fresh-provisioning.md) 是真实Linux/PostgreSQL上的**独立子任务**记录，各自有固定源提交和范围。它们不自动证明新集成树、控制台/CLI完整链或任务C组合通过。[ci_paper_postgres.py](../tools/ci_paper_postgres.py) 对skipped安全项拒绝资格；完整C还须实际进程kill矩阵、前后账本和UI/CLI演示。

Testnet/Demo是下一个独立连接器资格任务，需核对当时官方环境能力与独立虚拟余额凭据；目前未执行。真实资金须另行准入与明确授权，本文和愿意承担本金损失都不授予实盘许可。保留全部既定产品/模式范围，不以缩小范围换取全绿。

## 9. 首次保护待确认与已知保护失败分开

本轮风险语义裁决：新增成交立即保存 `ProtectionPlan.GAP` 和保护责任；首次
保护尚在安装/验证时，以 `PROTECTION_PENDING / NO_NEW_RISK` 阻止新增风险，
允许验证过的有界保护安装和减仓。这使任务 A 要求的保护修复能够通过同一
owner/数量/授权门禁。明确拒单或失败后升级为原有的
`PROTECTION_GAP / EXIT_ONLY`，不放宽 EXIT_ONLY；只允许可证明的退出和对账。
其他 UNKNOWN/恢复锁的权限仍取交集，不能借 PENDING 绕过它们。

参见 [cause policies](../src/trading_system/risk/locks.py)、
[action verifier](../src/trading_system/risk/locked_actions.py) 和
[PAPER composition](../src/trading_system/server/paper_runtime.py)。模拟保护 ACK
不构成覆盖证据，必须查询当前精确 stop 参数和已成交数量，并收到独立受权
控制器的验证事件。冻结契约中的 `VERIFIED_NATIVE` 在此仅表示模拟场所中的
相应订单形态；用户投影显式输出 `VERIFIED_SYNTHETIC`。这不是 Nautilus、真实
交易所止损或任何 Testnet/LIVE 资格，原 native 禁用门禁保持不变。

## 10. PAPER 多次数据库校验使用有界有效期

领域 UnitOfWork 默认发送许可仍为 1000ms。独立 PAPER 组合选择最多 5000ms，
覆盖受限角色连接、风险/权限重读和独立模拟数据库提交；每次检查仍受更早的
grant/intent/quote 截止时间限制。副作用 guard 等待后再次检查，超过 5 秒的
故障注入必须拒绝写入并保留 UNKNOWN/预留。此配置没有打开 native 或延长
原用户授权，也不构成跨宿主的外部 fencing 保证。

## 11. 旧即时成交测试迁移到实际事件链

原有金额、手续费、重复请求、并发预算、权限、重启和回滚断言不以假成交替代。
执行场景标为 `paper_runtime`，缺少真实隔离数据库即失败；普通测试作显式筛选，
另一个必须通过的 PostgreSQL job 运行这些场景且不允许跳过。测试控制器先保留
PENDING 收据，再显式提交/注入成交；SQLite 只验证交互、拒绝写入和历史只读投影。
控制台事务失败不能回滚另一数据库已 durable 的命令，因此相应反例验证的是
资金零变化、命令可查询和原 ID 恢复，不能再声称跨库原子回滚。
