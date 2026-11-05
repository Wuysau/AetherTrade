# 官方 Demo AI 执行候选（2026-11-05）

本轮实现限于 SPOT BTCUSDT LIMIT、现货买入及自有头寸减仓。默认仍不启用账户交易。
离线测试、签名清单和模拟回报不是 PostgreSQL、真实 Binance 成交或持续保护验收。
TESTNET、LIVE、USD-M 和其他订单能力没有启用；既有长期能力目标不变。

## 新增执行路径

独立部署证明 → 签名账户资格 → 物理账户 owner → 专用账户保守对账 →
已有 ModelService / assessment_v1 → 确定性风控 → durable command / MAY →
单次 permit → 官方 Demo POST /api/v3/order → 私有流及原 ID 补查 → 成交/费用入账。

`SerializedAccountPolicy` 不声称 Binance 提供 REST/WS 原子屏障。新策略版本
`serialized-account-v1` 限定已验收的专用账户、独占发送者、已排除外部活动、
初始预算固定且新增余额不扩大预算。新增风险前检查签名账户、全账户挂单、重叠成交、
本地账本与未决资金；只允许一笔新增风险命令在途。未知成交、未归并费用、
UNKNOWN、非匹配余额事件、过期读数和证据变更均阻断。无法证明的旧记录仍保留，
不会通过标记处理完成来恢复交易。

## 部署材料

原 `--demo-config` 五字段配置保持拒绝绑定/执行的兼容行为。
新路径另外需要：

1. 经批准部署并迁移的独立 PostgreSQL（含候选 0038），验证物理锁、sender 锁、角色与进程隔离。
2. 由独立部署身份保护的验证公钥文件，仅 `issuers` 与 `revoked` 字段。每个 issuer 包含
   `key`（Ed25519 公钥十六进制）、允许的 `purposes`、`test_only:false`。服务进程不得持有签名私钥，
   不得能修改信任根；程序读取文件不证明 OS 隔离已经部署。
3. 独立验收进程签名的 admission 文件，包含 `deployment`、`qualification` 和可选 `recovery`。
   部署配置新增 `admission_file` 绝对路径，命令显式指定 `--demo-trust`。文件没有 Binance Key/Secret。

签名载荷字段固定为 `version=1, issuer, purpose, nonce, issued_at, expires_at, bindings, claims`。
签名消息采用 `attestation.canonical(payload)` 的域分隔及规范 JSON；最长有效期 24 小时。
不同用途的绑定与 claims 见 `DeploymentEvidence.verify`、`DemoAuthority.bindings/_claims`、
`consume_recovery` 和 `QualificationExecutor._verify`。运行中重新检查期限和撤销。

运行代码测量包含 src/trading_system 下 Python/schema、全部 SQL 迁移和 pyproject.toml；
`measure_build(root)` 返回实际字节摘要。签名部署证据另需 PG_FENCING、PG_MIGRATIONS、
PROCESS_ISOLATION、IMMUTABLE_BUILD 的独立 PASS 证据哈希。内容摘要不等于 OS 远程证明；
依赖安装和不可变运行环境由独立 IMMUTABLE_BUILD 验收覆盖。

账户证明绑定 UID/epoch 对应 account_key、physical key、generation、凭据修订/指纹、
构建摘要/SHA、SPOT/BTCUSDT。要求逐项真实检查 PG_FENCING、PG_RECOVERY、ACCOUNT_BASELINE、
LIMIT_ACK、LIMIT_FILL、CANCEL、PRIVATE_STREAM、RECONCILIATION、TAKEOVER 全部 PASS。
应用本身没有 HTTP 签发或编辑资格入口，手写 qualification JSON 无效。

## 首次验收与恢复

`QualificationExecutor` 是独立受控验收工具的库入口，不由 serve 或 AI 自动调用。
使用另一用途 `qualification_run` 的签名授权，绑定精确请求哈希、原始 client ID、
最多 4 个命令、最多 100 USDT（包含 1% 费用预留）的累计额度和过期时间。
它在任何 IO 前持久消费额度，崩溃后不自动重试；实际执行仍要求 PostgreSQL 物理与
sender 锁、绑定的私有流和独立 PG fencing 证明。它不签发普通策略资格，ACK 不算成交。
包含动态价格过滤器时必须另行提供合格编译上下文，当前保守编译失败即停止该验收项。
当前未提供生产私钥或真实原始验收产物；不得把测试私钥复制为正式信任根。

跨进程恢复必须有独立签名的旧进程终止及旧凭据撤销证据，绑定旧 generation 与新凭据指纹。
持有新的物理锁后，恢复证明原子消费，generation/凭据修订递增；保留旧命令、UNKNOWN、
预留、账本与所有观测，撤销旧资格并进入 RECOVERY_REQUIRED。中断的 IN_FLIGHT 决策记为
FAILED/DEMO_RECOVERY_INTERRUPTED，禁止重用决策 ID 自动重试。仍待核对的旧 inbox 不会删除。
恢复证明可用 `claims.observation_hashes` 精确绑定已独立审阅的 observation_id/payload_hash；
已批准的订单/成交事实创建新 generation 的观测并重新经过原回报验证器，成功后记录原/新事实
关联并结束旧 inbox 的待处理状态。原始观测和代际不改写。余额帧仍须通过当前快照或已验证
订单/成交历史解释；未审阅、来源冲突、缺失历史和未映射的历史账户仍阻断。

正常的锁资/解锁历史帧不必等于最新 REST 余额：只有对应更新时刻存在已验证的原订单回报，
且已验证成交/手续费分录和剩余订单锁资能够精确解释该帧时才处理并记录支持事实。
同一时间的矛盾回报或缺失成交时间不做猜测，保持 BLOCKED；这不是 WS 原子序列保证。

## 运行与停止

用户在现有界面临时输入专用 Demo 凭据，绑定后配置已有模型与预算，再单独确认启动。
新组合将每个执行窗口末尾 30 秒保留给退出处理（执行窗口必须大于 30 秒）；
截止后拒绝新增 ENTRY，撤销已知入场订单并尝试对自有敞口执行受授权的 LIMIT 减仓。
限价单可能无法成交，凭据过期后不能继续发送；这些情况保留 NEEDS_OPERATOR 与原始责任。

停止及工作线程落定后，可读取当前订单/责任清单，显式确认接管。凭据过期或断开后，
已认证操作人仍可通过部署固定的账户范围查看并接管责任；这不依赖可交易的凭据 lease。
提交绑定清单摘要，
变化或仍有运行中任务即拒绝；确认原子撤销该账户所有策略执行/模型授权并持久审计。
接管不表示订单已撤销、账户已平或恢复交易资格，重新启动需要新的策略授权。

## 已核对官方来源

2026-11-05 通过官方仓库核对的版本：
`b044f231aeed080ed57ee36e9485a3899103f9dc`。

- [Demo endpoints](https://github.com/binance/binance-spot-api-docs/blob/b044f231aeed080ed57ee36e9485a3899103f9dc/demo-mode/general-info.md)
- [REST orders / UNKNOWN / order-test](https://github.com/binance/binance-spot-api-docs/blob/b044f231aeed080ed57ee36e9485a3899103f9dc/rest-api.md)
- [WebSocket signed subscription](https://github.com/binance/binance-spot-api-docs/blob/b044f231aeed080ed57ee36e9485a3899103f9dc/web-socket-api.md)
- [Filters](https://github.com/binance/binance-spot-api-docs/blob/b044f231aeed080ed57ee36e9485a3899103f9dc/filters.md)

官方 Demo 使用 demo-api.binance.com。order/test 不进入撮合，不能用来证明自动模拟成交。
仍须把实际采用的合同版本和真实验收产物哈希写入对应签名证据。
