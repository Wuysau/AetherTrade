# 统一 PAPER：终端与网页复现步骤

此recipe按集成源码 `21f9ddf` 的 [CLI](../src/trading_system/server/cli.py)、[provision](../src/trading_system/server/paper_provision.py)、[runtime](../src/trading_system/server/paper_runtime.py)、[API](../src/trading_system/server/app.py) 与 [输入schema](../src/trading_system/server/validation.py) 编写。**本文档提交时演示尚未执行：原始终端输出、网页截图、全C组合结果为NOT_RUN**；最终证据见 [acceptance.md](acceptance.md)，不能把下面的预期检查点当执行记录。远端CI及Testnet/LIVE均NOT_RUN。

仅用一次性本地PostgreSQL、虚拟余额和明确注入的合成事件。公开报价是入场校验输入，模拟成交价格是测试人员显式给定；模拟不证明真实滑点、流动性或保护。AI/RULE/HYBRID共享控制契约；本控制台模拟组合为Spot，不证明USD-M或真实Stocks适配资格。bStocks现货也不是实际证券适配器的资格证据。

## 1. 新建独立profile，不触碰旧账户

从仓库根目录、已安装项目的Python环境运行；前端依赖与构建命令见 [README](../README.md)。准备明确专用的本地PostgreSQL17+，由运行环境安全注入 `PAPER_RUNTIME_ADMIN_DSN`。这个管理员连接只用于本次显式provision，不传给 `serve` 或 `paper-run`，不输出或提交其内容。此recipe不猜测端口、账号或启动已有用户数据库。

```powershell
git rev-parse HEAD
git status --short
aethertrade paper-provision --isolated-paper --initial-cash 100000 --output .runtime/unified-paper.json
Remove-Item Env:PAPER_RUNTIME_ADMIN_DSN
aethertrade --paper-config .runtime/unified-paper.json serve
```

provision生成随机后缀的新 `paper_control_*`、`paper_runtime_*`、`paper_engine_*` 三库，应用业务迁移并初始化虚拟资金；**不迁移、重置或删除旧SQLite/console账本**。它为console库、business的 `ledger_owner` 访问、模拟venue运行和独立event控制分别创建连接身份；业务/engine/event运行角色不获得数据库管理员身份，console身份仅拥有新console库。profile保存四个连接值与journal路径和初始资金，不保存provision管理员密码；文件包含密码，应保留在忽略目录及受限本地权限下。已存在的输出文件会拒绝覆盖。

浏览器打开 [本地控制台](http://127.0.0.1:8787)，建立新的本地用户，初始虚拟资金**填写100000，与provision一致**。不一致时新请求将被 `PAPER_CAPITAL_MISMATCH` 拒绝。原SQLite/PostgreSQL历史账本标为 `LEGACY_LOCAL_PAPER` 且交易写入关闭；直接运行旧启动脚本或不带profile的 `serve` 不会获得统一执行链。

公开行情必须显示新鲜可用；`--no-market`只适合只读检查，不能作为入场绕过。此步骤不配置/启用付费模型。`serve`读取profile覆盖console与三个PAPER连接配置；`AETHERTRADE_DATABASE_URL`单独只选console库，不足以启用统一执行。

## 2. 同一投影：PENDING和SUBMITTED都不是成交

另开终端，使用相同Python环境与profile：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

网页创建一笔小额BTCUSDT手动买入（例如数量`0.001`，必须在当时新鲜报价、虚拟资金及风险限额内）。保存返回的 `order.command_id`（也出现在执行投影），以及收据的 `price`。不要用屏幕猜测成交：初始收据是 `PENDING`、零成交。也可在已登录网页开发者控制台用以下同源API，数值均为十进制字符串，无交易所/模型请求：

```javascript
globalThis.paperDemo = {
  session: await (await fetch('/api/session')).json(),
  post: async function (path, payload, key) {
    const response = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json',
        'X-CSRF-Token': this.session.csrf_token, 'Idempotency-Key': key},
      body: JSON.stringify(payload)
    });
    return {http_status: response.status, body: await response.json()};
  }
};
paperDemo.entry = await paperDemo.post('/api/paper/orders',
  {symbol: 'BTCUSDT', side: 'BUY', quantity: '0.001'}, 'demo-entry-1');
console.log(paperDemo.entry);
```

HTTP成功只是请求已处理。若返回风险/过期等拒绝，保存 `error.code/message`，未得到command则停止后续事件步骤。重复相同key与payload只返回原收据；不换key逃过拒绝/未知。成功后在终端复制实际ID与收据price：

```powershell
$entryId = Read-Host '实际 order.command_id'
$fillPrice = Read-Host '实际收据 price（本次明确选择的合成成交价）'
aethertrade --paper-config .runtime/unified-paper.json paper-dispatch $entryId
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

及时调度：当前入场grant上限30秒，且受原报价90秒默认时效限制；超时保存拒绝，不改时间或绕过。检查 `VENUE_CONFIRMED` command和 `SUBMITTED` order，filled应为0，positions仍空。若后台已提交，显式dispatch读取原结果，不重复经济副作用。

## 3. 40%部分成交与保护责任

显式注入0.0004/0.001成交，而不是根据行情自动FILLED：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-event $entryId --event-id demo-fill-40 --status PARTIALLY_FILLED --cumulative 0.0004 --price $fillPrice
aethertrade --paper-config .runtime/unified-paper.json paper-status
$protectReceipt = aethertrade --paper-config .runtime/unified-paper.json paper-protect $entryId --key demo-protect-1 | ConvertFrom-Json
$protectId = $protectReceipt.command_id
aethertrade --paper-config .runtime/unified-paper.json paper-dispatch $protectId
```

保存 `target_quantity`、`in_flight_quantity`、`actual_quantity`、`protected_quantity`、`protection_state`、`risk_locks`。部分成交实际量0.0004，余量0.0006；保护计划GAP和 `PROTECTION_PENDING/NO_NEW_RISK` 必须存在。原计划受控保护修复可执行，创建ACK仍不是保护验证。若自动责任处理已创建原保护命令，先从 `paper-status`取得其真实ID，复用/查询，不能手动添加第二张全额保护单。

下面两分支**择一**执行；另一分支使用新profile独立复现，避免将已失败/终态单强行改写。

成功分支：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-event $protectId --event-id demo-protect-verified --status PROTECTION_VERIFIED --cumulative 0
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

只有查询精确stop条款和当前数量相符后，网页/终端才显示 `VERIFIED_SYNTHETIC`。它不是 `VERIFIED_NATIVE` 交易所资格，原因锁也不会因此自动解除。

失败分支：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-event $protectId --event-id demo-protect-failed --status PROTECTION_FAILED --cumulative 0
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

检查 `PROTECTION_GAP/EXIT_ONLY`、GAP、实际暴露及退出/恢复责任，新增BUY应被拒绝，不能以新保护key绕过EXIT_ONLY。

## 4. 撤单未知保留60%义务

```powershell
$cancelReceipt = aethertrade --paper-config .runtime/unified-paper.json paper-cancel $entryId --key demo-cancel-1 | ConvertFrom-Json
$cancelId = $cancelReceipt.command_id
aethertrade --paper-config .runtime/unified-paper.json paper-dispatch $cancelId
aethertrade --paper-config .runtime/unified-paper.json paper-event $cancelId --event-id demo-cancel-unknown --status CANCEL_UNKNOWN --cumulative 0.0004
aethertrade --paper-config .runtime/unified-paper.json paper-reconcile $cancelId
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

保留原CANCEL command UNKNOWN、0.0006在途/预留义务、0.0004实际仓位及保护责任。不能通过新ID重新BUY/SELL覆盖这个未知。保存此检查点后，可明确结束本次模拟撤单：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-event $cancelId --event-id demo-cancel-confirmed --status CANCELED --cumulative 0.0004
```

若后台责任处理已生成撤单，应先查询它而不是制造第二撤单。真实平台not-found/分页缺口不等于此模拟控制器的明确CANCELED。

## 5. 退出失败、规则缘由与重启观察

若选择保护失败分支，并且入场余单已对账终结，在同一登录页面请求实际可减量的SELL：

```javascript
paperDemo.exit = await paperDemo.post('/api/paper/orders',
  {symbol: 'BTCUSDT', side: 'SELL', quantity: '0.0004'}, 'demo-exit-1');
console.log(paperDemo.exit);
```

只使用真实成功收据中的退出command ID；若自动退出责任已生成命令，复用其ID。调度后注入明确拒单，检查实际暴露与锁仍在：

```powershell
$exitId = Read-Host '实际退出 command_id'
aethertrade --paper-config .runtime/unified-paper.json paper-dispatch $exitId
aethertrade --paper-config .runtime/unified-paper.json paper-event $exitId --event-id demo-exit-rejected --status REJECTED --cumulative 0
aethertrade --paper-config .runtime/unified-paper.json paper-status
```

网页的整体退出入口是 `POST /api/paper/actions`，payload为 `action: 'exit_owned'`、当前账户 `expected_revision` 与明确 `confirmation: 'EXIT PAPER'`；还需幂等key/会话/CSRF。其返回 `APPLIED` 只说明退出请求持久化，不说明成交/空仓。仍有活跃保护、未知订单或数量不符时，按拒绝原因对账，不绕过。

不调用付费模型也可展示规则决策缘由：在策略页新建 `RULE/PRICE_THRESHOLD`，明确price/operator与stop百分比，以 `auto_execute=false` 激活并勾选PAPER自动化授权；该模式不选模型。自动化job应保存服务端“价格…LTE/GTE…”的决策summary及未执行/拒绝原因。AI/HYBRID须另用受控本地provider测试证据；本recipe不启用供应商调用，也不以显示三种模式就宣称全部通过。对应源码见 [automation](../src/trading_system/server/automation.py) 与 [validation](../src/trading_system/server/validation.py)。

独立owner进程用另一个终端启动，控制端关闭后它仍可调度/查询既有durable工作：

```powershell
aethertrade --paper-config .runtime/unified-paper.json paper-run
```

它不调用模型、不注入自动成交，复用已有request原报价/时间；报价过期不会延长授权。控制台和worker使用同一业务库互斥。先保存状态，再停止/重启API与worker，最后用 `paper-status`与网页刷新比对原command ID、成交、锁、保护/退出事项；不能以普通重启代替 [FAILURE_PLAYBOOK](FAILURE_PLAYBOOK.md) 的强制kill矩阵。

## 保存演示记录

保留固定commit、迁移hash、原始命令/退出码、带来源的API/CLI投影与网页截图；排除profile、密码、cookie和CSRF。至少捕获PENDING、SUBMITTED、部分成交/UNKNOWN、保护验证或失败、退出未完成/风险拒绝，以及重启后相同义务。缺失一项写NOT_RUN；普通演示不代替真实隔离PostgreSQL全部C断言、远端CI或venue资格。
