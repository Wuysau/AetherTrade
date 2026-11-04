# Binance Demo integration: implementation and remaining gates

基线：`public-snapshot` / `4134fd1ba6ab9152be064fc91ad93c59f61b8eb7`。
设计原文：[`design-2026-11-04.txt`](design-2026-11-04.txt)。

## 交付范围与重要限制

本分支提供 **用户填入式只读 Demo 界面，以及现货 BTCUSDT LIMIT 的独立原生运行时与离线生命周期实现**。
已覆盖真实 ModelService 的 assessment_v1 接入、确定性风控、持久发送、成交入账、保护减仓、撤单与恢复组合。
**完整设计验收仍未完成：PostgreSQL/真实 Demo/连续运行均未资格化，不能把离线通过称为可启用交易。**
没有启用真实资金 LIVE、Testnet、任何 Demo 交易能力或持续运行策略。
本次没有输入真实凭据、连接真实账户、发送交易所订单、创建数据库或部署。
PAPER 原有环境、scope hash、exact-type guard、资产与订单通道保持不变。

### 用户填入 API Key

1. 按项目现有流程构建前端：`npm --prefix web ci`、`npm --prefix web run build`。
2. 启动现有本地控制台：`aethertrade serve`。不需要启动 Docker 或为只读连接应用 Demo 迁移。
3. 登录后打开导航中的 **Binance Demo**。
4. 填入用户自己已有的 **Demo HMAC API Key / Secret**，勾选 Demo 专用凭据确认，点击“验证只读连接”。不要填入实盘密钥。
5. 页面只向固定现货 Demo 端点查询服务器时间、账户余额、BTCUSDT 规则与 BTCUSDT 挂单。
6. 只读验证成功显示 `READ_ONLY`。`exchange_can_trade` 只是官方账户字段，不是 AetherTrade 执行授权。

两个输入框均为密码型，提交时立即清空；不使用 localStorage/sessionStorage、数据库或磁盘保存凭据。
后端凭据绑定当前本地登录会话，最长 15 分钟，定时移除且签名时重新校验有效期；退出、移除或进程结束即失效。
Python 不保证已经释放内存的密码学擦除，因此运行主机与进程内存仍应受保护。
移除凭据不会取消交易所挂单。断开、重启、页面导航都不宣称订单已撤销。
页面每 10 秒读取本地连接状态，不自动向交易所刷新。过期行情/余额不能用于交易准入。

HTTP 路由仅有 `GET /api/demo/status`、`POST /api/demo/connect`、`POST /api/demo/disconnect`。
它们沿用本地登录、Origin、CSRF 和 no-store 防护。没有下单、撤单、任意 URL、持久密钥保存、READY 或能力启用路由。

## 已实现的代码边界

- `engine/binance_demo/contracts.py`：独立 `BINANCE_DEMO` scope，包含产品、账户、epoch、策略；账户级去重不含策略，防止同一场所成交跨策略重复入账。
- `credentials.py`、`transport.py`：短期 HMAC 凭据、确定性签名、固定端点、TLS、无代理环境继承、无重定向、时间校验、429/418/模糊发送分类、带签名 HTTP 日志抑制。写请求不做通用重试。
- `orders.py`：LIMIT/MARKET 纯编译及有界 Decimal 过滤；支持带新鲜平均价/挂单数量/账户持仓证据的百分比价格、订单容量与最大持仓过滤；未知过滤器拒绝。编译成功不代表能力通过。MARKET 风险/发送准入仍关闭。
- `storage/demo.py`、`0033_binance_demo.sql`、`0034_binance_demo_runtime.sql`、`0035_binance_demo_exit_memory.sql`：独立表、不可变命令身份、预留、MAY、UNKNOWN、durable inbox/outbox、成交、币种分录、保护责任和恢复游标。SQLAlchemy 元数据用于离线测试；迁移仅由批准的共享 DEV 流程应用。
- `execution/demo_commands.py`：PostgreSQL 会话 advisory lock 覆盖 MAY 提交和外部请求；暂停/轮换/冲突使用同一 sender guard；原 ID、单次 permit、风险快照门禁和模糊结果保留。SQLite 不能创建真实执行 owner。
- `execution/demo_reports.py`：原订单、已发送状态、方向/数量/LIMIT 价格绑定；WS/REST 重放、迟到状态、终态、第三币种手续费、终态先于缺失成交、原子记账和保护责任。
- `stream.py`：当前 `userDataStream.subscribe.signature`，受限 TLS WebSocket、禁止重定向、关闭私有帧日志、先订阅再并行补查、每条执行回报先入 durable inbox、有界运行窗口。未知列表/余额事件触发对账，不冒充已覆盖的能力。
- `execution/demo_reconcile.py`：凭据/账户绑定、原 ID 查询、“未找到”不自动重发、跨策略路由、分页重叠与 CAS 游标、订单查询和页数预算、余额差异冻结。单纯补查完成不自动 READY；独立恢复门禁还校验当前资格证据、原始余额基线、完整历史覆盖、挂单一致性、私有流、空 inbox、无 UNKNOWN 与有效保护，并原子记录恢复证据。
- `runtime/demo_host.py`：独立准入规则；当前代码、模型来源、报价/账户时效、私有流、UNKNOWN、预算、未保护敞口任何一项不合格均拒绝。
- `server/demo_snapshot.py`、`runtime/demo_frame.py`：独立 Demo 快照 schema；固定 Demo 行情/已闭合 K 线/账户费用读取，账户与策略自有持仓分开，策略/模型/规则/账户/凭据/授权/代码全部绑定。
- `runtime/demo_strategy.py`：RULE、AI、HYBRID；复用真实 ModelService、共识与原有 assessment_v1 校验，不伪装 PAPER；持久决策抢占、回执验证、目标差量、滑点/费用/日损/计划亏损/预算检查，原生命令及保护责任关联。
- `runtime/demo_service.py`：内部会话 lease、签名 UID 与可信 epoch 映射、显式运行时组合；有界私有流、inbox 消费、对账和独立保护监督。模型超时/无效计划仅暂停新风险，不终止既有保护监督。没有自动启动、付费调用、启用或资格写入接口。

10,000 USDT 是项目预算上限，不是初始化官方 Demo 余额。只读页面展示官方返回的 free/locked；不会复制 PAPER 资金或把余额快照反复记作入金。

## 设计工作项状态

| 工作项 | 当前状态 | 仍需完成 |
|---|---|---|
| D01 契约和边界 | 离线实现与验证 | 官方现行端点行为、真实契约证据固定 |
| D02 凭据与只读接入 | 用户界面/API、签名与安全测试完成 | 用户填入已有专用凭据后实际 Demo 连通验证；没有替用户创建密钥 |
| D03 持久发送 | 原生 owner、单次 MAY、UNKNOWN 保留、独立撤单与终态闭环的离线实现 | PostgreSQL 跨进程并发/崩溃/权限；真实发送/撤单逐能力证据 |
| D04 私有流与归并 | 有界流、durable inbox 消费、跨策略归并、重叠补查与证据绑定 READY 门禁 | 场所历史覆盖与断线行为验收；无人值守长期重连未启用 |
| D05 账本与保护 | 成交/手续费分录、净仓保护责任、日损估算、LAST/持有时限触发的 hosted LIMIT 减仓、撤单优先、责任解除 | 保护真实成交/延迟/断线/小额残仓与第三币种费用估值验收；原生条件单/OCO 未实现 |
| D06 策略与界面 | 只读界面、Demo schema、真实 assessment_v1 服务、RULE/AI/HYBRID、风控及显式 PG 运行时 factory 组合完成 | 部署绑定/策略授权由内部配置提供，无浏览器启用按钮；UPDATE_EXIT 与独立 Demo 记忆投影已接入；真实场内资格与部署验收未运行 |
| D07 能力验收 | 逐能力 evidence map、代码/账户/凭据/契约绑定门禁实现；默认仍 COMPILED/禁用 | 当前 SHA + 专用账户 + 官方契约 + 真实 Demo/PG 证据；没有伪造 ENABLED |
| D08 连续运行 | 受控、最长 900 秒且小于凭据 TTL 的运行窗口代码；NOT_RUN | 用户确定窗口、策略参数、模型费用预算、停止条件与具体执行授权后才运行 |

U 本位合约、杠杆、条件单、组合单、cancelReplace、批量、SOR/TWAP/VP 仅保留隔离边界，未实现或未资格化，全部禁用。
不得通过手工写 `READY`、伪造 qualification JSON、修改检查或注入本地事件绕过上述缺口。
库级 `observe`/`register_account` 等是受信任进程内部接口，不是外部数据接入或验收证据签发 API；部署时仍需独立数据库角色、写权限与证据来源治理。

## 验证与后续准入

本次只执行 TASK_LIGHT：新 Demo 离线测试、相关本地 API/PAPER 启动回归、相关前端测试、TypeScript 和构建。
详见 [`../evidence/binance-demo/verification.json`](../evidence/binance-demo/verification.json)。
离线 SQLite/MockTransport 结果不是 PostgreSQL 隔离、真实私有流、Demo 成交或持续运行证明。
PostgreSQL 迁移、sender advisory lock 的跨进程/断线行为、恢复和权限尚未实测，不能宣布交易资格通过。
未运行 Docker、全量集成、数据库资格或真实 Demo 测试。前端构建存在原有大 chunk 警告；FastAPI 测试存在上游 httpx 兼容弃用警告。

下一步按治理流程完成共享 DEV PostgreSQL 资格与部署绑定；取得用户对具体 Demo 测试窗口/策略/模型费用的许可后，使用用户填入的专用 Key 做真实场所验收。不需要在聊天中提供 Key/Secret。

## 内部运行时组合与运维边界

`create_app(..., demo_runtime_factory=...)` 是显式部署注入点，默认 `None`。factory 接收 `connections` 与真实 `model_service`，构造 `DemoRuntimeService`，另外提供独立已迁移的 `DemoRepository`、可信 UID/epoch/strategy 映射、当前构建 SHA 和经验证的规则函数。不会创建数据库或自动迁移。

- `bind_session` 使用当前会话的内部 lease 和签名账户 UID，不能用浏览器给定 scope、密钥指纹或客户端布尔值冒充账户身份。
- `save_strategy` 是受信任进程配置接口：显式记录 revision、授权到期、`allow_model_calls`、`allow_execution`、模型与 policy revision、预算和亏损约束。存储配置不意味着能力通过。
- `DemoMemoryProjector` 从 durable outbox 建立独立、scope/epoch 隔离的记忆读模型，不修改账本，不消费其他投影的 delivery 标记。模型收到有界、hash 绑定的历史和当前责任摘要；真实 ModelService input_hash 必须匹配，原 PAPER memory 写入明确关闭。
- `UPDATE_EXIT` 使用实际成交建立的计划 head 和 revision，不选择任意“最新模型意见”。无活动订单、UNKNOWN、待归并回报或正在减险责任时，才可原子收紧全部 HOSTED/LAST 责任；保持原始 opened_at、持仓量与责任身份，禁止放宽止损或延长持有期。只产生持久管理回执，不发新增交易所订单。
- `assess_once` 使用持久 run ID，IN_FLIGHT/失败调用不盲目重放。Demo 模型结果带环境标签，排除在 PAPER 决策列表之外。
- `run_window(execute=False)` 仅观察；`execute=True` 还要求 PG owner、存储的明确执行授权、有效凭据和所有资格门禁。保护监督与模型调用并行，所有发送仍由唯一 owner 执行。
- `reconcile_and_resume` 不接受外部 READY 开关。缺少真实基线/完整历史/逐能力证据仍拒绝。
- 同一 target 的 MAY/UNKNOWN/ACK 撤单不会另造身份重发；已证明从未发送且失效的命令才可释放预留。原订单终态用于解决撤单歧义，不把单次未找到当成未发送。
- Hosted 保护是每 5 秒到期的运行责任，不是交易所保证止损。保护 LIMIT 不保证成交；费用缓冲和过滤网格残仓仍保留为待处理责任。模型故障不停止监督，但凭据/授权撤销、窗口结束或网络故障不能保证继续管理场内敞口，必须由操作人按计划接管。
- MARKET、USDM、原生 OCO/条件单、批量和高级算法能力仍关闭。没有宣称这些未实现能力已完成。

## 官方参考（来自批准设计的链接，本次未实时验收）

- https://developers.binance.com/en/docs/products/spot/demo-mode/general-info
- https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade
- https://developers.binance.com/en/docs/products/spot/user-data-stream
- https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-api/user-data-stream
- https://developers.binance.com/en/docs/products/spot/rest-api
- https://developers.binance.com/en/docs/products/spot/web-socket-streams
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info
