# Binance Demo integration: implementation and remaining gates

基线：`public-snapshot` / `4134fd1ba6ab9152be064fc91ad93c59f61b8eb7`。
设计原文：[`design-2026-11-04.txt`](design-2026-11-04.txt)。

## 交付范围与重要限制

本分支提供 **可用的只读 Demo 连接界面，以及独立的订单/回报/账本基础代码**。
**它不是已经完成验收的全自动 Demo 交易系统，也不具备完整的端到端订单生命周期。**
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
- `orders.py`：LIMIT/MARKET 纯编译及 Decimal 过滤；未知过滤器拒绝。编译成功不代表能力通过。MARKET 风险/发送准入仍关闭。
- `storage/demo.py`、`0033_binance_demo.sql`：独立表、不可变命令身份、预留、MAY、UNKNOWN、durable inbox/outbox、成交、币种分录、保护责任和恢复游标。SQLAlchemy 元数据用于离线测试；迁移仅由批准的共享 DEV 流程应用。
- `execution/demo_commands.py`：PostgreSQL 会话 advisory lock 覆盖 MAY 提交和外部请求；暂停/轮换/冲突使用同一 sender guard；原 ID、单次 permit、风险快照门禁和模糊结果保留。SQLite 不能创建真实执行 owner。
- `execution/demo_reports.py`：原订单、已发送状态、方向/数量/LIMIT 价格绑定；WS/REST 重放、迟到状态、终态、第三币种手续费、终态先于缺失成交、原子记账和保护责任。
- `stream.py`：当前 `userDataStream.subscribe.signature`，受限 TLS WebSocket、禁止重定向、关闭私有帧日志、先订阅再并行补查、每条执行回报先入 durable inbox、有界运行窗口。未知列表/余额事件触发对账，不冒充已覆盖的能力。
- `execution/demo_reconcile.py`：凭据/账户绑定、原 ID 查询、“未找到”不自动重发、跨策略路由、分页重叠与 CAS 游标、订单查询和页数预算、余额差异冻结。补查完成也不自动 READY。
- `runtime/demo_host.py`：独立准入规则；当前代码、模型来源、报价/账户时效、私有流、UNKNOWN、预算、未保护敞口任何一项不合格均拒绝。

10,000 USDT 是项目预算上限，不是初始化官方 Demo 余额。只读页面展示官方返回的 free/locked；不会复制 PAPER 资金或把余额快照反复记作入金。

## 设计工作项状态

| 工作项 | 当前状态 | 仍需完成 |
|---|---|---|
| D01 契约和边界 | 离线实现与验证 | 官方现行端点行为、真实契约证据固定 |
| D02 凭据与只读接入 | 用户界面/API、签名与安全测试完成 | 用户填入已有专用凭据后实际 Demo 连通验证；没有替用户创建密钥 |
| D03 持久发送 | 基础代码及离线故障路径完成，未运行 PostgreSQL owner | 数据库并发/崩溃/权限、真实发送准入和撤单终态闭环；管理操作未资格化 |
| D04 私有流与归并 | 有界流、durable inbox、归并、补查基础完成 | 常驻消费者调度、自动重连、完整历史窗口证明、运维恢复水位资格 |
| D05 账本与保护 | 独立成交/费用分录和保护责任原子处理完成 | 与原生风控/记忆/财务投影的生产组合，保护创建/修复/解除，数据库恢复资格 |
| D06 策略与界面 | 独立只读界面完成，准入规则基础完成 | 真正的 assessment_v1 Demo host 注册、模型/规则调用、完整风险快照与策略调度；不复用当前聊天助手充当 API |
| D07 能力验收 | 当前仅 COMPILED/禁用能力列表 | 当前 SHA + 当前专用账户 + 官方契约 + 真实 Demo 逐能力证据；没有 ENABLED |
| D08 连续运行 | NOT_RUN | 用户确定窗口、策略参数、费用预算、停止条件并授权后才运行 |

U 本位合约、杠杆、条件单、组合单、cancelReplace、批量、SOR/TWAP/VP 仅保留隔离边界，未实现或未资格化，全部禁用。
不得通过手工写 `READY`、伪造 qualification JSON、修改检查或注入本地事件绕过上述缺口。
库级 `observe`/`register_account` 等是受信任进程内部接口，不是外部数据接入或验收证据签发 API；部署时仍需独立数据库角色、写权限与证据来源治理。

## 验证与后续准入

本次只执行 TASK_LIGHT：新 Demo 离线测试、相关本地 API/PAPER 启动回归、相关前端测试、TypeScript 和构建。
详见 [`../evidence/binance-demo/verification.json`](../evidence/binance-demo/verification.json)。
离线 SQLite/MockTransport 结果不是 PostgreSQL 隔离、真实私有流、Demo 成交或持续运行证明。
PostgreSQL 迁移、sender advisory lock 的跨进程/断线行为、恢复和权限尚未实测，不能宣布交易资格通过。
未运行 Docker、全量集成、数据库资格或真实 Demo 测试。前端构建存在原有大 chunk 警告；FastAPI 测试存在上游 httpx 兼容弃用警告。

下一步先按治理流程完成最小共享 DEV PostgreSQL 资格，再补齐运行组合与保护责任流程；取得用户对具体 Demo 测试窗口/策略的许可后，使用用户填入的专用 Key 做真实场所验收。

## 官方参考（来自批准设计的链接，本次未实时验收）

- https://developers.binance.com/en/docs/products/spot/demo-mode/general-info
- https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade
- https://developers.binance.com/en/docs/products/spot/user-data-stream
- https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-api/user-data-stream
- https://developers.binance.com/en/docs/products/spot/rest-api
- https://developers.binance.com/en/docs/products/spot/web-socket-streams
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info
