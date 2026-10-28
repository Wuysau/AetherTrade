# 统一 PAPER：证据索引与未完成项

## 当前判定：本地统一 PAPER 工程验收通过（2026-10-28）

最终后端固定源 `6cbc870`，最终前端代码 `c4520da`（前端/文档差异，后端源码
blob 未改变）。[最终交付报告](FINAL_PAPER_REPORT_2026-10-28.md)、
[原始测试及校验器](evidence/2026-10-28-final-qualification/README.md)、
[真实 HTTP/CLI/PG/手机演示](evidence/2026-10-28-real-demo/README.md)。

- 必需 Linux/PostgreSQL：20 suites，**802 passed / 0 failed / 0 errors / 0 skipped**。
- 普通 Python：**2373 passed / 362 skipped / 90 deselected**，exit 0。可选项未运行
  状态保留，不以它们替代另一必需 job 的实际数据库安全验证。
- 前端：**108 passed**；typecheck/tests/build 原始退出码均 0。

| 原任务 C 要求（原顺序） | 本地结果与来源 |
| --- | --- |
| 隔离真 PostgreSQL 并发/恢复，skip 不绿 | PASS：固定源完整门禁及 JUnit；所有必需 suite 无 skip |
| 相同目标/竞争预算/人工减仓不补回 | PASS：duplicate-target、reservations、runtime-faults、三模式 marked server；手动 override 持久化 |
| 接单 ACK 丢失，原 ID 查询不重发 | PASS：实际独立模拟 venue 提交与 Linux kill，UNKNOWN/MAY 保持原身份 |
| 40% 保护责任，60% 撤单未知不释放 | PASS：runtime 部分成交/精确模拟保护、CANCEL_UNKNOWN；实际 demo 全100.200预留保持，再确认/审核 |
| 止损及紧急退出失败，重启锁与事项保留 | PASS：protection 与统一 runtime 的 REJECTED/EXIT_ONLY/restart 反例 |
| 日亏损反弹仍锁，合法减仓/保护修复 | PASS：全账户多标的发送校验、各迟发边界、scope权限交集及独立日审核；不自动解锁 |
| durable/提交/ACK/保护写入边界强制终止 | PASS：runtime-faults 的5个实际 Linux SIGKILL边界及原始前/后/恢复账本 |
| AI 超时/过期/乱序拒增险，维护继续 | PASS：marked server 过期与权限反例；独立维护在旧入场授权/行情过期后仍须真实受权、有界、VENUE_CONFIRMED；应用/UoW时钟注入明确记录，PG时钟未伪造 |
| 手机/终端五类状态，200/202不等于完成 | PASS：真实浏览器对生产 API 操作，CLI+SQL，同一三库；桌面/390px截图、HTTP202待成交、ACK非验证、FLAT_VERIFIED与费用对齐 |
| 三种模式同一单笔风险与所有权 | PASS：三模式实际 PG 正/反例；RULE零模型调用、HYBRID显式绑定和规则；原 Schema 未改 |

这是明确范围的合成 PAPER 验收，不是所有原 T/Q 平台资格通过。控制台组合为既有
现货形态；USD-M/真实 Stocks 最终产品范围保留、真实连接器资格独立。远端 GitHub
Actions、Binance Testnet/Demo、真实云模型、滑点/流动性、真实证券/合约/LIVE均
**NOT_RUN / NOT QUALIFIED**。native 禁用门禁没有解除。下一任务必须另行明确发起。

<details>
<summary>早期验收矩阵历史（当时的 NOT_RUN 不能覆盖上方最新固定源结果）</summary>

> **2026-10-28 用户已恢复任务。** 以下矩阵是早期文档基线，局部执行证据已增加，
> 最新状态、未提交测试及两项 P1 见 [暂停交接](HANDOFF-2026-10-28.md)。
> 最终固定集成树的全套门禁尚未运行，不能把本矩阵或各批局部绿灯当作完整通过；
> TESTNET/LIVE 均未准入。本轮继续实施与验证，完整通过仍需固定树证据。

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

</details>
