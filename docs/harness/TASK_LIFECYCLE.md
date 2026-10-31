# 任务状态、证据和恢复契约

新增治理工作流应采用显式新版本元数据（拟定 version=3，workflow=task-light-batch-full），不得静默改写历史 v1/v2 生命周期、旧命令哈希、旧事件或测试结论。现有解析器未适配前不得将新状态写入旧记录或伪造 MERGED_VERIFIED。

正常任务流：REGISTERED → ACTIVE → LIGHT_VERIFIED → INTEGRATION_QUEUED → MERGED_QUICK_VERIFIED → ARCHIVED → CLEANED。任意门槛失败可进入 BLOCKED，保留上一步和具体原因；恢复需绑定修复/核验记录，过期租约不能自动夺取所有权。

批次流：OPEN → ALL_TASKS_INTEGRATED → FULL_ACCEPTANCE_QUEUED → FULL_ACCEPTANCE_PASSED；失败进入 REPAIR_REQUIRED，登记修复任务，再冻结新的候选进行 FINAL_REGRESSION。未执行、环境阻断和实际测试失败分别记录。任务 CLEANED 不是批次 FULL_ACCEPTANCE_PASSED。

持久记录至少包括任务与依赖、身份、原/目标/候选提交、队列顺序、锁令牌、环境指纹、测试层级与精确选择、预算与次数、原始结果摘要/证据哈希、归档路径、清理步骤和时间。记录 append-only 的状态事件；崩溃后对照 Git 引用、进程、租约及真实资源恢复，不盲目重发合并、测试或删除。

交易执行架构不变：PostgreSQL 账本、唯一执行所有者、durable command、permit、UNKNOWN 原 ID 恢复、资金预留和保护职责全部保留。应用发起外部写操作独立命令/授权；交易所原生子单仅可信回报/查询及父子关系验证后摄取，不重复发送。工程通过不授予交易权限。

当前本规范仅定义目标协议；调度器、删除控制、OS 权限隔离及真实环境恢复未由本次文档变更实现或验证。
