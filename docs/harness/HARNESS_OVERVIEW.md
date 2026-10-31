# AetherTrade Harness 规范

生效日期：2026-10-31。本文及链接规范记录用户批准的工程治理要求，适用于所有 Agent、Integration Manager、测试调度器和环境运维入口。

必读：

- [环境与资源边界](ENVIRONMENT_POLICY.md)
- [分层测试调度](TEST_SCHEDULING.md)
- [Worktree 生命周期与串行集成](WORKTREE_PROTOCOL.md)
- [状态、证据与恢复](TASK_LIFECYCLE.md)

本补充取代旧 Harness 中逐任务启动独立测试环境、逐次合并运行完整回归的调度要求，以及仅保留工作树而无安全回收的生命周期要求。交易 SPEC、安全约束、历史证据和历史生命周期记录不因此改写。dev 分支上的旧文档尚需在治理实现集成时同步；不能认为本次文档提交已经部署了权限控制或调度服务。

当前执行状态：POLICY_APPROVED / ENFORCEMENT_IMPLEMENTATION_PENDING。Docker 数据盘曾不可恢复丢失，恢复和隔离准入未完成；初期仅允许代码修改及不需要 Docker 的安全验证。本任务分支中的 qualify_paper_docker.py 已退役为 BLOCKED/NOT_RUN 兼容入口，不再创建容器；统一调度器仍未实现，其他分支旧脚本不得执行。禁止把文档要求当作已实现功能。

本任务是工程治理，不恢复 Binance 订单功能开发。2026-10-31 向 public-snapshot 的一次性未测试汇总合并不是长期集成策略，也不满足自动清理门槛。
