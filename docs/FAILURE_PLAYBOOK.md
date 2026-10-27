# PAPER 故障重现与恢复手册

用途：2026-10-27 工程任务 D 的学习/排障入口。这里列出的 test 函数是现有源码入口；列出它们不表示本次已执行，更不表示真实交易所通过。当前组合验收见 [acceptance.md](acceptance.md)，统一profile/终端/网页操作见 [PAPER_DEMO.md](PAPER_DEMO.md)。只操作明确隔离的测试 PostgreSQL；不使用旧控制台数据或真实账户。AI/RULE/HYBRID 与 Spot/USD-M/Stocks 保持原范围和独立能力门禁。

## 先保存哪些证据

固定 `git rev-parse HEAD`、`git status --short`、测试原始命令/退出码、Linux/Python/PostgreSQL版本、迁移校验和；保存故障前后 scope、command/client ID、outbox/receipt、原始事件、fill、reservation、owner epoch、risk locks、protection/exit 与 reconciliation case。凭据不进入日志。若查询失败，显示缺失事实，不能编成零余额/空仓。

## 故障与恢复判据

| 场景 | 已有可重现入口 | 观察与恢复 |
| --- | --- | --- |
| HTTP/ACK 结果未知 | [test_lost_ack_restart_query_original_and_never_resend](../tests/integration/test_paper_engine_postgres.py)、[test_lost_commit_acknowledgement_never_calls_send](../tests/integration/test_durable_dispatch.py) | MAY_HAVE_SENT/UNKNOWN 保留；查原 clientOrderId 与 operation-specific 结果。查询空列表、超时或重发 HTTP 请求不能证明 NOT_SENT；禁止换新 ID 重发。提交 ACK 丢失与发送 ACK 丢失分别排查 |
| 40% 部分成交、60% 撤单未知 | [test_partial_fill_cancel_unknown_duplicate_and_restart](../tests/integration/test_paper_engine_postgres.py)、[test_forty_percent_fill_cancel_unknown_keeps_remainder_and_denies_resend](../tests/integration/test_paper_duplicate_target.py)、[test_partial_fill_then_cancel_unknown](../tests/integration/test_protection_lifecycle.py) | 40% 成交立即进入保护责任；60% 在途数量和风险预留不误释放。接收迟到 fill，按原生成交 ID 去重；撤单未知继续查原单。不能等整单完成才保护 |
| 止损失败 + 紧急退出失败 | [test_failed_protect_and_failed_exit](../tests/integration/test_protection_lifecycle.py)、[test_repository_persists_gap_before_real_dispatcher_denial](../tests/integration/test_protection_lifecycle.py) | durable GAP/锁/退出与恢复事项继续可查，新风险禁止；显示 EXITING 和失败原因。先对账原保护/退出 ID，再决定受权修复；不能把异常处理返回当成功退出 |
| DB 故障/commit 不确定 | [test_repository_commit_failure_never_dispatches](../tests/integration/test_protection_lifecycle.py)、[test_database_query_failure_produces_no_facts](../tests/integration/test_paper_engine_postgres.py)、[test_db_down_emergency_only](../tests/faults/test_owner_and_storage_failures.py) | 无 durable 记录就不产生普通 side effect；已 durable 未确认则查询。紧急 journal 仅在独立当前权限、数量与 fsync 等全部证据成立时可尝试，当前 owner 的离线实现不代表在线紧急退出可用；存储都失败则不发新许可 |
| 双 owner / stale lease / epoch | [test_linux_flock_excludes_second_process_and_releases_on_close](../tests/faults/test_owner_and_storage_failures.py)、[test_postgres_concurrent_cas_advances_exactly_one_epoch](../tests/integration/test_ownership_transfer.py)、[test_old_host_not_fenced](../tests/faults/test_owner_and_storage_failures.py) | 第二 owner 应拒绝；过期 lease 不是旧主已被外部隔离的证据。禁止删 lock inode、凭 PID 接管或直接改 epoch；需要可信 fencing、当前授权与恢复检查，缺证据保持不就绪 |
| 人工改仓/删保护/切模式 | [test_manual_reduction_not_refilled](../tests/integration/test_ownership_transfer.py)、[test_old_proposal_cannot_replenish_after_manual_position_change](../tests/integration/test_paper_duplicate_target.py)、[test_manual_deletion_and_expiry_create_gap_without_model_call](../tests/integration/test_protection_lifecycle.py) | [ManualInterventionService](../src/trading_system/ownership/manual.py) 保存 EXTERNAL_CHANGE 锁和对账事项；总量观察不是额外仓位，也不是新策略目标。不自动认领外部仓位或补旧目标；先校验订单/持仓/保护/owner、明确干预决定和 target rebase |
| 日亏损后价格回升/重启 | [test_durable_hwm_and_no_automatic_lock_release](../tests/integration/test_persistent_risk_locks.py)、[Store](../src/trading_system/server/store.py) | durable cause lock 保留，价格回升不恢复开仓。按配置风险日与证据执行独立解除；合法减仓/修复仍需授权、数量、方向与UNKNOWN校验 |
| AI 超时、过期输出、乱序行情 | [PriorityInbox / ExecutionOwner](../src/trading_system/runtime/execution_owner.py)、[ProposalIngress](../src/trading_system/intents/ingress.py)、[test_model_failure_does_not_disable_protection_planning](../tests/integration/test_protection_lifecycle.py) | 新风险按时效/数据健康拒绝；已确认订单和保护维护独立于模型继续。研究事件可过期/合并，安全事件不能因此丢失；安全队列过载 latch 需恢复，清空队列不自动解除 |

恢复统一遵循 [Reconciler](../src/trading_system/recovery/reconciler.py) 与 [RecoveryGate](../src/trading_system/recovery/gate.py)：查询原 ID，验证完整 scope、时效、身份、覆盖和已归档证据，再分别更新命令、订单、持仓、预留和锁。模型、用户 payload、HTTP 收据及 PaperEngine 的不完整查询不能充当可信证明。即使显式零仓位通过，历史经济数据缺口与 cause lock 仍可能需要独立处理。

## 进程强制终止边界

以下是必须审查的恢复矩阵。已有单项测试只覆盖所描述边界；整套控制台组合的真实 kill 矩阵仍为 NOT_RUN。

| 中断点 | 重启时的合法动作 | 现有证据入口 / 缺口 |
| --- | --- | --- |
| intent/reservation/command/outbox 事务提交前 | 验证事务全部回滚，没有残留预留/可发送命令 | [test_reservations.py](../tests/integration/test_reservations.py)、[test_ledger_transactions.py](../tests/integration/test_ledger_transactions.py)；不以异常注入等同所有 OS kill |
| DURABLE_READY 已提交，尚未 MAY_HAVE_SENT | 原命令经重新授权/风控/owner检查后可调度；不造第二命令 | [test_durable_dispatch.py](../tests/integration/test_durable_dispatch.py)；完整组合 kill 待记录 |
| MAY_HAVE_SENT 已提交，调用/ACK 前后 | 保守进入 UNKNOWN，查询原 ID；不能断言未发送 | [test_crash_before_wire_unknown](../tests/integration/test_durable_dispatch.py)是真实SIGKILL；[test_separate_process_query_after_forced_post_commit_exit](../tests/integration/test_paper_engine_postgres.py)覆盖模拟库提交后退出 |
| 结果 durable，outbox receipt 前 | 重复投递读取已有结果，不能再次发送 | [test_terminal_outcome_and_receipt_survive_consumer_restart](../tests/integration/test_durable_dispatch.py)、[test_outbox_repeat_no_second_send](../tests/integration/test_durable_dispatch.py)；该边界完整组合OS kill仍待记录 |
| fill durable，保护命令/收据之前 | durable 通知驱动重查，原保护命令 ID 复用，gap不消失 | [test_crash_between_fill_and_protection_receipt_reuses_durable_identity](../tests/integration/test_protection_lifecycle.py)；控制台真实 kill 组合待验证 |
| emergency journal fsync 前后 / 业务库恢复 | 不重放 permit；按原 ID查询，验证 journal完整性、合并和平台事实 | [test_fsync_failure_after_append_never_issues_permit](../tests/faults/test_owner_and_storage_failures.py)、[test_crash_restart_queries_original_never_replays_permit](../tests/faults/test_owner_and_storage_failures.py)；离线边界不代表真实平台恢复 |

## 实际存在的命令

从仓库根目录运行。下列 Linux shell 示例假设已由工程运行环境安全注入**一次性隔离测试集群**的变量；不会提供猜测凭据、自动选择数据库或启动真实账户。测试依赖见 [pyproject.toml](../pyproject.toml)。

```sh
git rev-parse HEAD
git status --short
# 无数据库的有界单元检查
python -m pytest -q tests/unit/test_target_compiler.py tests/unit/test_locked_actions.py
# Linux + 明确一次性 PostgreSQL 17+，全套 gate；任一 safety skip 不通过
# 环境需预先设置 PAPER_CI_ISOLATED=YES、PAPER_CI_ADMIN_DSN、PAPER_CI_MARKER
python tools/ci_paper_postgres.py --initialize
python tools/ci_paper_postgres.py --evidence-dir .runtime/paper-pg-ci
# 仅用于定位 adapter 的诊断，不构成全套资格
python tools/ci_paper_postgres.py --suite paper-engine --evidence-dir .runtime/paper-pg-diagnostic
```

[ci_paper_postgres.py](../tools/ci_paper_postgres.py) 检查显式本地地址、专用 `paper_ci_admin`/`paper_admin`、标记、版本及无应用对象；为每模块建新库并保留。完整 run 要求源树干净，记录固定 commit、迁移 hash、命令、退出码、原始日志/JUnit和 summary。单 `--suite` 的 qualification 仍是 NOT_RUN。初始化只标记经检查的空专用库，不迁移/重用应用库。

另有已经存在的原子目标回归脚本（Linux Python 3.12、预先设置 `IP07_ISOLATED_DATABASE=YES` 与 `ATOMIC_TEST_ADMIN_DSN`）：

```sh
python scripts/verify_atomic_target.py
```

它为两个模块创建并保留新 `ip07_test_atomic_*` 库，输出到新的 `.runtime/atomic-*`。详情与历史原始结果见 [atomic evidence](evidence/2026-10-27-paper-atomic/README.md)。PAPER角色的独立安全验证见 [fresh provisioning evidence](evidence/2026-10-27-paper-engine-fresh-provisioning.md)，不是 Testnet/实盘验证。

## 统一控制台的操作入口

[CLI源码](../src/trading_system/server/cli.py) 支持全局参数 `--paper-config PATH`，必须放在子命令前。profile含密码，不能附进验收日志。下列命令的ID必须来自同一新profile的真实收据，不能填旧console订单ID。完整recipe及合成事件分支见 [PAPER_DEMO.md](PAPER_DEMO.md)。

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-status
aethertrade --paper-config .runtime/unified-paper.json paper-dispatch $entryId
aethertrade --paper-config .runtime/unified-paper.json paper-reconcile $entryId
aethertrade --paper-config .runtime/unified-paper.json paper-protect $entryId --key demo-protect-1
aethertrade --paper-config .runtime/unified-paper.json paper-cancel $entryId --key demo-cancel-1
```

未知状态先 `paper-reconcile` 原ID；不要换key重发买单。`paper-event` 是独立受限event角色的显式虚拟平台故障控制入口，非交易所回执。`PROTECTION_PENDING/NO_NEW_RISK` 与已知失败的 `PROTECTION_GAP/EXIT_ONLY` 分开：前者允许有证据的原计划保护修复；后者保持原减险/恢复约束，失败退出不会解锁。当前用户CLI没有原因锁resolver；`resume_new`只恢复控制台开仓开关，不能清除durable风险锁。

启动 `paper-run` 可在控制API停止时继续调度/查询已有命令；保存原报价会按原时间过期，不能用worker伪造新报价。app与独立worker是否均已实际完成故障演示、保护/退出自动责任推进及重启结果，须查固定提交的验收输出，不由命令存在推断。远端CI、Testnet/LIVE在本recipe中均NOT_RUN。
