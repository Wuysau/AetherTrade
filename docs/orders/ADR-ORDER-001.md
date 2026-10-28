# ADR-ORDER-001 — 原生子单与本地写命令边界（待确认）

状态：PROPOSED，尚未更改冻结契约或写入权限。

## 需要明确的冲突

批准包 ARCHITECTURE_DECISIONS.md ADR-005 要求每个算法子单覆盖 durable 边界。
当前 qualification/nautilus/write-path-inventory.json:76–81 把 ALGORITHM_CHILD
描述为 native algorithm child submission，且 independent_command_required=true。
新交接包 §8.2 要求交易所生成子单只摄取，不能再次提交。这会改变命令/permit
覆盖单位，不能只扩大 operation enum 或删除旧要求。

## 建议采用的明确规则

1. 所有由 AetherTrade 产生的外部写动作，继续逐次 durable command + 单次 permit。
   包含本地算法子单、原生母单提交、子单撤改（若平台允许）、保护变更。
2. 交易所根据已获授权母单生成的子单，只作为 VENUE_CREATED_CHILD 事件摄取。
   摄取前验证账户、产品、服务命名空间、母单关联、数量/资产/预算约束；无法关联的
   子单进入 UNKNOWN/对账阻断，不认领、不重复提交、不释放预留。
3. 母单提交前固化完整 OrderProgram、最坏情形预算、期限、委托模式和权限。
   母单 ACK/停止不代表全部子单成交/撤销；未核清子单保持资金和保护责任。
4. local_child 与 venue_created_child 不得由模型自由声明。由受信任编译器依据
   冻结能力快照和产品模板绑定；模型不能通过指定native逃避每次本地写检查。
5. 更新写路径清单为 LOCAL_ALGORITHM_CHILD_WRITE 与 VENUE_CHILD_OBSERVATION，
   旧原始记录/hash保持不动；旧 UNKNOWN 继续通过原 codec、原ID查询。

这不是允许绕过执行所有者的例外。它把“平台生成事件”与“应用发起写动作”的
边界明确化，以免重复发送原生子单。建议批准以上语义后才冻结OP-B新契约。

## OP-B 可实施边界

- 新增版本化 AtomicOrderSpec / OrderProgram / CapabilitySnapshot / RuleSnapshot
  及其严格codec；产品/账户/owner仍由服务端绑定。price、quantity、trigger、TIF、
  session、display、STP、routing采用互斥tagged variants，不允许自由HTTP字典。
- ordinary/list/conditional_algo/execution_algo四个身份空间独立；显式母子关系、
  FULL_FILL激活、互斥、净到账派生和native/local/PAPER模型模式。
- proposal_valid_until、venue_expiry、management duty、单次write permit独立。
- 原2026-10-20四份公共Schema与打包副本字节不改；新修订使用新文件与版本路由。
  历史codec只读恢复/查询，未知版本拒绝；不得将旧UNKNOWN升级为新订单。
- 迁移只扩展现有ledger：计划/关系/组状态/撮合状态/市场事件消耗记录；身份与
  已有command、intent、reservation相关联，不建第二套资金账本。明确唯一键和
  append-only权限，迁移角色与运行角色分离；不得给运行角色DDL权限。
- 回填只补可证的版本/关联，不补造stop/trigger/TIF。先双读，测试后切新写；
  回滚时先停新写并清点新版本未终态责任，不能让旧worker接管新版本UNKNOWN。
- OP-B只证明表达、codec、组状态与迁移兼容；不声明撮合或native功能完成。

## Native构建建议

现有lock没有部署artifact，当前venv未安装Nautilus。latest官方文档已面向2.x RC，
默认稳定安装仍是1.x，低层Rust客户端不通过Python暴露。建议先对选定精确构建
建立wheel/源码/依赖/镜像/ABI锁与导入/写路径probe，再冻结OP-E接口映射。
若需2.x RC，仅标隔离研究候选，不能因此获得Testnet或LIVE资格。
