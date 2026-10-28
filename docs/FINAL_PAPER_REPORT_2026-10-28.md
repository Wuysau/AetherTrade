# 统一 PAPER 工程交付 — 2026-10-28

**结论：本轮统一 PAPER 实现与本地工程验收通过；不授予任何真实平台写入权限。**
实施工作区 `TradingHandoff/AutoDispatchWorktrees/paper-execution`，分支
`codex/unified-paper-execution`。主目录 `AetherTrade` / `public-snapshot` 未合并，
未推送，保留全部 worktree。原批准 SPEC、契约 Schema 与既定任务编号未改动；
AI/RULE/HYBRID 和 Spot/USD-M/Stocks 的最终产品范围不变。

## 子任务结果

| 任务 | 本轮结果 / 主要实现 |
| --- | --- |
| A.1–A.2 原 Python 失败与生产/夹具分类 | 已完成，原 20 失败及 Windows 4 个错误阶段的原始证据、最小修复和反例均保留；见 [P0结果](../evidence/paper/2026-10-27-p0/RESULTS.md) |
| A.3–A.4 风险锁与合法维护 | 已完成：NO_NEW_RISK/EXIT_ONLY 取权限交集；有界减仓/保护证明不绕过归属、数量、方向、授权和 UNKNOWN；日亏损持久锁跨价格回升/重启/标的；次配置风险日且全账户已清仓后才支持独立审核解除 |
| B 共同 PAPER 路径 | 已完成：人工与三模式通过同一 durable 命令/风险/预留路径，独立受限 PaperEngine；提交 PENDING/SUBMITTED 不直接成交，事件需显式注入；旧账本只读，不冒充新成交 |
| C PostgreSQL 故障与组合回归 | 本地固定源门禁通过；20 个必需 Linux/PostgreSQL suite，802 passed，0 failures/errors/skips；完整原始命令/退出码、迁移/镜像/依赖/源码哈希、进程强杀前后账本均归档 |
| D 学习交付与真实界面示例 | 源码跳转文档、序列图、failure playbook、ADR、CLI/API 配方，以及真实 HTTP+CLI+PG+桌面/手机演示完成 |
| E 下一环境 | 仅完成出口判断；远端 GitHub Actions、Binance Spot Testnet/Demo、USD-M Demo、真实证券/实盘均 NOT_RUN，不自动进入下一任务 |

## 验证出处与固定源

- 最终后端固定源 `6cbc870`：**802 必需 PG 通过、0 跳过**；普通 Linux Python
  **2373 passed / 362 skipped / 90 deselected**，exit 0。普通 job 中未运行的可选
  项明确保留，必需数据库安全测试由另一 job 实际运行，不能用 skips 冒充通过。
  门禁包含领域逻辑回归与实际 SQL/并发/进程故障场景，各类来源在原始日志中分开，
  不把其中的内存逻辑用例解释为真实平台或数据库故障证明。
- 最终前端代码 `c4520da`：**108 passed**；独立 typecheck、tests、build 均 exit 0。
  它与后端固定源的差异仅为前端/文档；后端、Python 测试、迁移、工具源码 blob
  一致性已校验。不同命令的实际提交范围分开记录，不声称同时运行于同一个提交。
- 独立审查曾真实复现并修复：跨标的发送前风险漏检、保护持仓减仓阻断、抢先成交
  造成的调整卡死、过期未发送保护阻断维护、交易后日亏损审核误读 raw 状态。
  新代码区分 raw durable 快照与 effective outcome；当前审查范围无未解决 P0/P1/P2。
- [完整测试归档及校验器](evidence/2026-10-28-final-qualification/README.md)：固定源、
  命令/退出码、JUnit、故障注入 JSON 与 SHA256。
- [真实终端/网页演示](evidence/2026-10-28-real-demo/README.md)：真实生产 API 与新三库，
  行情/成交明确为受控合成输入，没有 Playwright 响应 mock、付费模型或真实订单。
  最终现金/权益 **9998.921 USDT**、手续费 **0.079**、持仓 **0**、
  `FLAT_VERIFIED`、独立审核后活动锁 **0**。独立 owner 在控制 API 重启期间存活。

## 可审查提交与代码入口

此前 P0、fixture、原子目标、owner CAS、PaperEngine、CI 及精度证据按独立子任务
提交保留。本轮关键集成提交：

| 提交 | 意义 |
| --- | --- |
| `ddbd9ab` / `ad271f4` / `a997d17` | 共同控制台路径、跨库 fencing、事件账本、模拟保护与显式三库 provision |
| `e58368f` / `b655b6a` | 每个增险发送边界重估全账户；已知结果/UNKNOWN 原 ID 恢复不重复开仓审批 |
| `055c47a` / `a7ca95e` | 有审核记录的保护调整及有界减仓；抢先成交时废弃旧请求，恢复剩余保护责任 |
| `2f5ee3d` / `6de6ed5` / `f1809e7` / `7136ac5` | 独立维护、次日审核、旧证据真实查询更新、仅对证明未发送的过期工作退役，按 effective 结果解除已履行锁 |
| `b0a01d2` / `01b7350` | 受认证网页续接、幂等与 UNKNOWN 审核分离、ABORTED_UNSENT 不显示为在途未知 |
| `c7ce6a4` / `6cbc870` | 真实 CLI 的 package helper 修复；止损成交也纳入费用与已实现盈亏 |
| `21ebabd` / `2d09801` / `3cb41a7` / `c4520da` | 真实 PG 测试迁移、显式 HYBRID 绑定、规则详情兼容与正确来源、收据/最终执行区分 |

入口：[共同服务](../src/trading_system/server/paper_runtime.py)、
[全账户发送风险](../src/trading_system/server/paper_account_risk.py)、
[保护调整证明](../src/trading_system/risk/protection_adjustment.py)、
[独立维护与审核](../src/trading_system/server/paper_maintenance.py)、
[provision](../src/trading_system/server/paper_provision.py)、
[CLI](../src/trading_system/server/cli.py)、[受控 demo](../tools/demo_paper_server.py)。

## 明确限制

1. 本控制台组合目前是 Binance 现货形态的合成 PAPER（包含 bStocks 的既有现货
   交互范围）。领域契约中的 USD-M/实际 Stocks 范围没有删除，但它们的真实连接器、
   保证金、证券时段/结算、特殊保护及账号资格必须分别准入，不以现货结果代替。
2. `NautilusEngine.live_enabled=False` 与 `NATIVE_PATH_NOT_QUALIFIED` 保留。
   内部冻结枚举中的 NATIVE 形态在用户投影中明确标为 VERIFIED_SYNTHETIC，
   不证明真实止损、滑点、流动性或跨宿主 fencing。
3. 日亏损解除是保守的 **次配置风险日 + 全账户 flat/无在途 + 独立 reviewer +
   显式 operator review id**；不提供同日/持仓中自动解锁。
4. 需要按 [启动与恢复配方](PAPER_DEMO.md) 运行独立 owner。无 owner 或证据/授权
   过期时命令可以保持待恢复/被拒绝，不能把 API 成功当成交；诊断拒绝记录未删除。
5. 本轮没有远端 push，GitHub Actions 新提交未触发；本地执行的是所存固定源的
   等价测试。Testnet/Demo 需另一个明确连接器资格任务，真实资金还需独立准入与授权。

学习资料：[交易生命周期](TRADE_LIFECYCLE.md)、[故障手册](FAILURE_PLAYBOOK.md)、
[设计决策](DESIGN_DECISIONS.md)、[保护调整](PROTECTED_REDUCTION.md)、
[owner/审核](PAPER_OWNER_REVIEW.md)。
