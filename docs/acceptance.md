# 统一 PAPER：证据索引与未完成项

> **2026-10-28 用户暂停。** 以下矩阵是早期文档基线，局部执行证据已增加，
> 最新状态、未提交测试及两项 P1 见 [暂停交接](HANDOFF-2026-10-28.md)。
> 最终固定集成树的全套门禁尚未运行，不能把本矩阵或各批局部绿灯当作完整通过；
> TESTNET/LIVE 均未准入。未经用户明确恢复，不继续实施、测试或启动服务。

对应2026-10-27下一项工程任务A–E；不是原SPEC状态变更。审阅文档基线`97c4f9d`。以下“存在”只表示源码/历史记录存在；**本文未执行测试，不将历史单项结果计作新集成验收通过**。最终验收须固定集成commit及原始命令/退出码、迁移hash、故障前后账本。

| 任务 | 当前可核对来源 | 本轮判定 / 必须补充 |
| --- | --- | --- |
| A 阻断缺陷、锁语义 | [dispatcher](../src/trading_system/execution/dispatcher.py)、[locked action verifier](../src/trading_system/risk/locked_actions.py)、[persistent locks tests](../tests/integration/test_persistent_risk_locks.py)、[Store durable daily-loss](../src/trading_system/server/store.py) | 源码已存在；新集成树完整Python/web/远端CI证据 **NOT_RUN**；不能由文档宣称P0全绿 |
| B 共同PAPER执行路径 | [proposal ingress](../src/trading_system/intents/ingress.py)、[reservations](../src/trading_system/risk/reservations.py)、[dispatcher/outbox](../src/trading_system/execution/outbox.py)、[PaperEngine](../src/trading_system/engine/paper.py) | 领域子链存在；控制台/CLI共同服务接线、账户/保护完整投影、legacy迁移与组合实测 **NOT_RUN** |
| D 学习文档与最小用户演示 | [lifecycle](TRADE_LIFECYCLE.md)、[failure playbook](FAILURE_PLAYBOOK.md)、[design decisions](DESIGN_DECISIONS.md) | 三份文档提供源码解释；统一用户CLI/网页的一笔完整提案/风险拒绝/退出演示 **NOT_RUN** |
| E Testnet/Demo出口 | [native bridge](../src/trading_system/engine/nautilus/bridge.py)、[PaperEngine native gate](../src/trading_system/engine/paper.py) | **NOT_RUN / NOT QUALIFIED**。本任务不打开TESTNET写入，不做真实订单；现货结果不代替USD-M/Stocks资格 |

## C 十条硬性验收

每项均须在最终集成树上记录证据。下面是回归入口，不是执行结果。

| C要求（保持原顺序） | 可用源码/单项回归入口 | 完整组合证据 |
| --- | --- | --- |
| 真隔离PostgreSQL并发与恢复，skip不绿 | [mandatory runner](../tools/ci_paper_postgres.py)、[CI](../.github/workflows/tests.yml) | **NOT_RUN**；保留固定commit、版本/迁移、全部日志/JUnit |
| 相同目标不重复、共享预算、人工减仓不补回 | [duplicate target tests](../tests/integration/test_paper_duplicate_target.py)、[reservations tests](../tests/integration/test_reservations.py)、[manual ownership tests](../tests/integration/test_ownership_transfer.py) | 历史领域证据见[atomic record](evidence/2026-10-27-paper-atomic/README.md)；新共同入口 **NOT_RUN** |
| 接单ACK丢失，UNKNOWN/原ID查询不重发 | [PaperEngine lost ACK](../tests/integration/test_paper_engine_postgres.py)、[durable dispatch](../tests/integration/test_durable_dispatch.py) | adapter历史记录见[provisioning followup](evidence/2026-10-27-paper-engine-fresh-provisioning.md)；完整链 **NOT_RUN** |
| 40%保护责任，60%撤单未知预留保留 | [partial/protection tests](../tests/integration/test_protection_lifecycle.py)、[target reconciliation](../tests/integration/test_paper_duplicate_target.py) | 各层反例存在；真实共同链及前后账本 **NOT_RUN** |
| 止损与紧急退出失败，重启后锁/事项可查 | [protection tests](../tests/integration/test_protection_lifecycle.py)、[owner/storage failures](../tests/faults/test_owner_and_storage_failures.py) | **NOT_RUN**（完整组合重启） |
| 日亏损反弹仍锁，合法减仓/修复可校验 | [risk locks](../tests/integration/test_persistent_risk_locks.py)、[locked actions](../tests/unit/test_locked_actions.py)、[Store](../src/trading_system/server/store.py) | **NOT_RUN**（共同入口+PostgreSQL组合） |
| durable/发送/ACK/保护边界强制终止，先查不重发 | [dispatch kill](../tests/integration/test_durable_dispatch.py)、[engine forced exit](../tests/integration/test_paper_engine_postgres.py)、[playbook matrix](FAILURE_PLAYBOOK.md) | 部分实际process测试存在；全边界控制台组合kill记录 **NOT_RUN** |
| AI超时/过期/乱序拒新增，维护继续 | [ingress](../src/trading_system/intents/ingress.py)、[owner priority inbox](../src/trading_system/runtime/execution_owner.py)、[model-independent protection test](../tests/integration/test_protection_lifecycle.py) | **NOT_RUN**（最终组合故障注入） |
| 手机/终端分清目标、在途、成交、保护、锁与API收据 | [contracts](../src/trading_system/contracts/types.py)、[state explanation](TRADE_LIFECYCLE.md) | UI/CLI最小演示及浏览器检查 **NOT_RUN** |
| AI/RULE/HYBRID同一单笔风险和owner控制 | [ingress](../src/trading_system/intents/ingress.py)、[reservations](../src/trading_system/risk/reservations.py)、[dispatcher](../src/trading_system/execution/dispatcher.py) | 三模式最终共同入口验证 **NOT_RUN** |

历史230通过/8 deselected的adapter记录与原子目标29/预留67/单元189通过只覆盖其记录的固定树及场景，不是当前完整C的总计。测试用synthetic证据不代表真实平台成交、滑点、保护、Testnet或LIVE资格。旧console434/server98/web等历史检查也不证明统一链。完整验收缺口不能通过删反例、改SPEC、开启native或减少AI/RULE/HYBRID、Spot/USD-M/Stocks范围消除。
