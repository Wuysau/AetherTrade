# AetherTrade｜Binance 订单能力全覆盖审查与架构改造方案

**日期：2026-10-28｜状态：设计建议，待评审；本次未修改产品代码。**

## 1. 结论

需要对订单领域、订单计划编译、PAPER 撮合和产品适配进行**有边界的重构**；不应推翻新完成的统一执行链，也不应只增加枚举、放开 LIMIT 判断或暗中改成 MARKET。

目标是：在已批准市场范围内，币安已文档化、目标账户/交易对允许且通过相应环境验证的订单功能，都能由 AetherTrade 正确表达、下发、观察、撤改和恢复。不得把尚未实现作为永远缩小范围的理由；同样不能把“全覆盖”解释成任何 AI 输出都必须获准下单。

**审查基线已更新：** `public-snapshot` / `b168966d350ed60fe8a92792f9519f4227054bc5`，提交时间 2026-10-28 09:15:04 UTC（北京时间17:15:04）。此前 `acbdcb2` 的直接模拟成交结论已不适用于这条新路径。[C10](https://github.com/Wuysau/AetherTrade/commit/b168966d350ed60fe8a92792f9519f4227054bc5)

本次依用户要求先查 Binance 官方文档，再固定新提交阅读关键代码。阅读了运行时、自动策略、wire契约、PaperEngine、保护编译、分发器、Nautilus bridge及项目报告。容器拉取源码因DNS解析失败；代码通过GitHub连接器读取。**没有本地编译、运行项目测试、验证实盘或重放用户这笔订单。**仓库报告的历史测试结果不冒充本次重跑结果。见下列 C01–C10 固定提交源码索引。

## 2. 覆盖范围与“全部支持”的定义

本方案以既有SPEC的 `SPOT / USD_M_PERP / US_EQUITY` 为产品范围，覆盖普通单、条件单、组合单、价格/数量/TIF修饰符、路由、执行算法及完整生命周期。TWAP和VP纳入完整目标，不只列基础七种订单。REST作为明确核验的主路径；WS交易接口与FIX属于传输能力，不能在未知结果时通过换协议重发。

币安另有COIN-M、Margin、Options、Portfolio Margin、Alpha、Convert等产品/账户接口。它们出现在官方目录中，但加入它们需要新产品身份、资产/保证金/结算模型和独立授权，不能将现货接口改前缀冒充覆盖。设计保留产品插件边界；本报告**不宣称覆盖币安全部金融业务**。[B17](https://developers.binance.com/en/docs/catalog)

每项功能需要同时给出：

- `documented`：官方端点/FAQ/变更日志是否支持。
- `symbol_eligible / account_eligible`：交易对和账户是否允许。
- `implemented`：契约、编译、事件、查询/撤改/恢复是否实现。
- `qualified_by_environment`：PAPER语义、测试环境协议、LIVE账户资格分别有无证据。
- `execution_mode`：VENUE_NATIVE / LOCAL_SYNTHETIC / PAPER_MODEL，不能互相冒名。

最终功能缺口应逐项清零；权限不足、标的限制、数据无效、风险超限、受限专业功能仍可拒绝。支持 LIMIT 不保证成交，支持 stop 不保证触发价成交。

## 3. 官方订单清单

### 3.1 Spot

| 类别 | 应覆盖能力 | 说明/来源 |
|---|---|---|
| 基础订单 | MARKET、LIMIT、LIMIT_MAKER、STOP_LOSS、STOP_LOSS_LIMIT、TAKE_PROFIT、TAKE_PROFIT_LIMIT | `/api/v3/order`；参数按类型校验。[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)[B02](https://developers.binance.com/en/docs/products/spot/enums) |
| 订单列表 | OCO、OTO、OTOCO、OPO、OPOCO | 独立list identity和子单状态，不是把它们塞入普通type。[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)[B05](https://developers.binance.com/en/docs/products/spot/faqs/opo) |
| 追踪条件 | trailingDelta | 适用四种条件单，单位BIPS，激活价与极值轨迹需保存。[B03](https://developers.binance.com/en/docs/products/spot/faqs/trailing-stop-faq) |
| 价格修饰 | PRIMARY_PEG、MARKET_PEG、偏移参数 | 只对许可的限价类生效；在放入订单簿/触发时确定价格，不能臆造持续追价行为。[B04](https://developers.binance.com/en/docs/products/spot/faqs/pegged_orders) |
| 数量/显示 | quantity、quoteOrderQty、icebergQty | 按type/标的合法组合，冰山显示量不是额外可花资金。[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)[B07](https://developers.binance.com/en/docs/products/spot/filters) |
| 有效期 | GTC、IOC、FOK | 不能用allow_partial一个布尔值代替。[B02](https://developers.binance.com/en/docs/products/spot/enums) |
| 路由 | SOR | 支付/成交资产和allocation需对账；不是普通单成功之后补一句SOR。[B06](https://developers.binance.com/en/docs/products/spot/faqs/sor_faq) |
| 执行规则 | 动态价格范围、参考价格、expiryReason | 与下单价格过滤器分开；吃单越界可EXPIRED，不保证MARKET全部成交。[B19](https://developers.binance.com/en/docs/products/spot/faqs/price_range_execution_rules) |
| 改撤查 | 取消、查询、cancel-replace、keep-priority amend、用户事件 | keep-priority仅减量；cancel-replace需处理两项结果。[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)[B08](https://developers.binance.com/zh-CN/docs/products/spot/faqs/order_amend_keep_priority)[B18](https://developers.binance.com/en/docs/products/spot/rest-api) |

**OPO/OPOCO的关键区别：**待执行数量来源于工作单实际收到的资产，受费用和数量过滤器影响，收到的资产可处于锁定中；并且仍须工作单完全成交才激活后续。不能把它们理解成“部分成交就自动止损”。[B05](https://developers.binance.com/en/docs/products/spot/faqs/opo)

### 3.2 USD-M

| 类别 | 应覆盖能力 | 说明/来源 |
|---|---|---|
| 普通单 | MARKET、LIMIT | `/fapi/v1/order`。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)[B10](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log) |
| 条件单 | STOP、STOP_MARKET、TAKE_PROFIT、TAKE_PROFIT_MARKET、TRAILING_STOP_MARKET | `/fapi/v1/algoOrder`、algoType=CONDITIONAL；普通下单端点不再接收该组类型。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)[B10](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log) |
| TIF | GTC、IOC、FOK、GTX、GTD、RPI | 逐端点/类型/账户核验，不默认所有组合都合法。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)[B11](https://developers.binance.com/zh-CN/docs/products/derivatives-trading-usds-futures/common-definition) |
| 触发/价格 | CONTRACT_PRICE、MARK_PRICE、priceProtect、priceMatch | 与固定price、持仓模式等有组合约束。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) |
| 仓位 | positionSide、reduceOnly、closePosition | 尤其hedge与close-all字段互斥；内域只减仓意图不等于线上总能发送reduceOnly字段。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) |
| 生命周期 | 普通/算法查询、撤销、LIMIT改单、批次、ALGO_UPDATE与普通订单事件 | 未触发条件单不可假设支持直接AMEND；改单方式显式选择。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)[B10](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log) |

**近期文档变化：**2026-10-23起，USD-M条件跟踪止损的callbackRate上限从10提升到20；本次看到的Nautilus latest文档仍表述0.1%–10%。这是文档层面的不一致，需要核对将部署的精确源码/版本，不能仅加参数到20就宣布集成通过。[B10](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)[N01](https://nautilustrader.io/docs/latest/integrations/binance/)

### 3.3 原生执行算法

Spot和USD-M均有TWAP和VP，使用 `/sapi/v1/algo/{spot|futures}/newOrderTwap`、`newOrderVp`，并有母单、子单、查询和撤销接口。它们与USD-M“条件算法单”不是同一类服务。[B15](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo)[B16](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo)

PAPER需实现声明过假设的TWAP/VP调度模型；平台专有执行算法的真实选价/队列行为不能凭公开资料精确复刻。必须区分“接口和状态语义支持”“模拟绩效假设”“平台真实执行表现”。

### 3.4 真实股票/ETF

当前下单API明确提供 MARKET/LIMIT；DAY/GTC和RTH/EXTENDED/24H有组合限制。真实证券应显式tokenize=false。BUY MARKET使用notional，SELL MARKET使用quantity，LIMIT使用price/quantity/tradingSession，不能复用现金加密订单的所有字段。[B12](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)[B13](https://developers.binance.com/en/docs/products/stocks/common-definition)

用户流schema能出现stop/trailing等上游报告字段，不足以证明本账户的下单端点允许提交这些类型。接收事件能力与发单能力分开，未文档化股票原生止损不假装支持；本地托管保护保持明确标签与单独授权。[B12](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)[B14](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/ws-streams/user-streams)

### 3.5 官方文档不能只抓一个enum

1. USD-M下单页面的共享枚举仍列条件类型，但变更日志明确这些类型应改走algo端点。端点语义与有效日期优先，不仅看type枚举。[B09](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)[B10](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)
2. 股票事件能解析某类型不意味着下单端点接受它。[B12](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)[B14](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/ws-streams/user-streams)
3. 账户/标的的exchangeInfo、filters和权限是动态事实。API文档给出的范围不等于BTCUSDT当前每项能力已获准。[B07](https://developers.binance.com/en/docs/products/spot/filters)
4. `/order/test`只是验证请求，不进入撮合引擎，不能充当部分成交、撤单竞态或保护验证。[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)

**未取得完整内容的项目：**Spot官方导航列有`quoteNotionalLimit`，但此次该FAQ正文不可获取，已读Trade页面也未找到同名参数。本报告将其列入`documentation-discrepancies.json`待核验，不臆造其可下发语义。实施前须复核官方schema/changelog；本清单是当前已确认的目标，不是对未来接口变更的永久保证。

## 4. 新架构有哪些已经做对

新的 `UnifiedPaperRuntime` 已将业务账本作为执行事实来源，使用独立PaperEngine数据库；提交不直接改成FILLED，事件由明确的合成控制输入推动。自动提案已进入统一accept_proposal，不再是旧版store直接即时成交。[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)[C02](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/engine/paper.py)[C04](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/automation.py)

分发器已引入 `LockedActionVerifier`，不应继续把旧版“有锁就把所有退出一起拦截”作为未修复结论。计划保留现有授权、资金预留、命令持久化、UNKNOWN恢复、所有权和有界减险能力。[C06](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/execution/dispatcher.py)

这些是静态看到的结构与代码行为，不等于本次已验证其数据库或交易所正确性。

## 5. 必须改的架构边界

### A. 限价拒绝只是表面，下面仍强制生成市价

`accept_proposal()`约398–401行要求MARKET、可部分成交、LAST。`_accept()`随后又把TradeIntent与ExecutionCommand的订单类型写死MARKET。**直接删除入口条件会把LIMIT悄悄变成MARKET。**必须用统一编译器端到端保留价格、触发、TIF和有效期。[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)

### B. 当前PaperEngine不是行情驱动的撮合器

它明确要求受权合成事件注入，适合验证ACK丢失、崩溃和状态恢复。这个设计应保留；新增市场驱动模拟器，而不是让submit()自己生成FILLED。[C02](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/engine/paper.py)

### C. 提案与执行契约不足

entry只有MARKET/LIMIT；底层只有少数普通/条件枚举和TIF。列表关系、trailing、peg、iceberg、触发源、算法母子单均缺完整表达。`parent_command_id`不能替代正式的订单列表/算法计划模型。[C03](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/contracts/wire.py)

### D. 能力布尔值不足以约束模型

`analysis_evidence()`返回execution_supported/protection_supported=True，却没有告诉模型只接受MARKET和LAST。模型看到合法LIMIT选项、执行时才被拒绝是当前设计不一致的直接结果。[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)

### E. 报价和产品模型是合成现货模板

`_spec()`使用合成tick/step/minimum，`_inputs()`把bid/ask/last设成同价；账户、USDT、SPOT与24H会话固定。这是合成测试设定，不应拿来宣称Binance参数校验已完成。[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)[C09](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/decision_execution.py)

### F. 时间与订单责任必须解耦

现有请求授权和intent默认受30秒和quote有效期限制。新方案需分别保存建议可提交期限、订单在场有效期、保护责任、管理授权以及每次操作permit的短期有效期。旧建议过期不能让仍挂在交易所的GTC或stop失去撤改/保护能力。[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)[C06](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/execution/dispatcher.py)

### G. MARK/CONTRACT触发源和产品账户边界

保护编译明确承认命令缺trigger source，非LAST拒绝。当前现货库存/现金核算不能直接拿来处理USDM保证金或证券结算。需产品语义组件，不能只扩Product enum。[C05](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/protection/manager.py)[C01](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)

### H. 候选Nautilus不是完整订单覆盖的自动保证

当前bridge仍声明没有准入原生clients/签名/传输。上游文档列Spot OCO和iceberg，但Bracket与批量修改仍有限制。原生列表/算法/SOR等需逐项检查精确构建并扩展受控执行客户端，不能从UI或AI加旁路REST。[C07](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/engine/nautilus/bridge.py)[N01](https://nautilustrader.io/docs/latest/integrations/binance/)

## 6. 方案比较与推荐

| 方案 | 判断 |
|---|---|
| 在当前if分支中逐个放行类型 | 不推荐：会丢参数，形成多处规则、模拟不匹配和安全旁路。 |
| 重新实现整个交易系统/换一套机器人 | 不推荐：丢掉刚完成的统一持久化与恢复能力，扩大工程风险。 |
| 保留统一内核，重构订单模型/编译器/能力注册/PAPER模拟及适配 | **推荐。**改造集中在订单语义边界，与既定AI/规则/混合和多市场设计一致。 |

这是本次建议，不表示用户已经批准新契约或代码变更。

## 7. 目标结构

```text
官方能力目录 + 标的/账户规则 + 已锁定适配器资格
                     ↓
           CapabilityRegistry / RuleSnapshot
                     ↓
 AI / 规则 / 交叉决策 → Proposal + OrderProgram
                     ↓
            SemanticValidator / PlanCompiler
                     ↓
        统一风险 + 原子预留 + DurableCommand
                     ↓
              ExecutionOwner
          ┌──────────┴─────────────┐
  PaperExecutionClient         LiveExecutionClient
  ├ 手工合成事件测试端           └ Nautilus + 受控Binance扩展
  └ 市场驱动PaperMatcher             ├ Spot普通/列表/SOR
          │                          ├ USDM普通/条件
          │                          ├ 原生TWAP/VP
          │                          └ Stocks
          └──────────┬─────────────┘
             统一事件摄取/账本/保护/对账/UI
```

有多种执行客户端不代表有多套资金系统。一个真实账户产品域仍只有一个execution-owner和统一写权限；任何扩展只作为该owner内部受控客户端，不给AI密钥或任意端点选择。

## 8. 领域模型改造

### 8.1 AtomicOrderSpec：拆开正交维度

- product/account/instrument/side/position_leg；由服务端绑定身份。
- quantity：BASE_QUANTITY / QUOTE_NOTIONAL / CLOSE_ALL / DERIVED_RECEIPT。后三者各有市场与权限约束，不用0占位冒充全部仓位。
- price：MARKET / FIXED_LIMIT / PEG / PRICE_MATCH；各variant字段白名单，禁止无类型自由dict。
- trigger：NONE / STATIC / TRAILING；trigger source、activation、offset及单位显式保存，BIPS不与percent混用。
- TIF：GTC/IOC/FOK/GTX/GTD/DAY/RPI等仅作为词汇表，合法组合由product profile检查。
- display、STP、protection scope、trading session、路由、费用资产与最大预算等类型化扩展。

统一域语义不能直接当作原生字段名。比如统一的TAKE_PROFIT_MARKET在Spot对应TAKE_PROFIT，统一TAKE_PROFIT_LIMIT在USD-M对应TAKE_PROFIT。禁止逐字复制同名枚举。

### 8.2 OrderProgram：列表和算法是有关系的计划

新增列表/算法聚合ID、叶子节点、激活条件、互斥关系、预算来源、原生/本地执行模式。OCO是互斥组；OTO/OTOCO是工作单全成激活；OPO/OPOCO还包含实际到账资金派生；TWAP/VP是算法母单及子单集合。

只允许白名单模板和受限无环图，限节点数，不允许模型生成Python/HTTP/任意脚本。某图不能无损编译到交易所时，不静默拆成几个普通订单：需要用户已授权的软件合成模式，或者明确返回语义不匹配。

**本地与交易所生成子单的区别：**本地TWAP/拆单器每次向平台发送的子单，都必须先有本地durable Command与permit。原生TWAP/VP或原生列表的子单则由平台在已授权母单内部生成：本地应在提交母单前持久化完整计划/预算/权限，之后观察并归档子单身份、成交和预算消耗；不能要求平台在生成每个子单前等待本地事务，也不能再次提交这些子单造成重复。

### 8.3 状态分层

业务目标、提交命令、普通订单、列表、条件算法单、执行算法母单、真实持仓与保护分别有状态。`PENDING_NEW`、TRIGGER_PENDING、触发后普通orderId、列表REJECT/ALL_DONE、STP失效必须可保存；总状态不能覆盖子单细节。

`SUBMIT_UNKNOWN/CANCEL_UNKNOWN/REPLACE_PARTIAL`不等于未发送或全失败。listClientOrderId/clientOrderId/clientAlgoId、orderListId/orderId/algoId建立多对多证据关联表；按实际作用域去重。

**兼容迁移：**新OrderSpec/Program采用明确wire修订；原始已批准Schema、原提案和命令hash不重写。旧记录只读显示和按原codec恢复；旧UNKNOWN保持原ID查询，不转换成新计划重新下单。数据库采用先扩展、回填可证字段、双读、切换新写、最后退役旧写路径的步骤；合约字段变更不等于产品功能分期。

### 8.4 时间模型

`proposal_valid_until`只决定还能否首次提交；`venue_expiry`决定在场订单期限；`manage_until_flat`维护责任延续；每次新写动作重新获得短期permit。GTC不会因为模型不再在线而无人管理，GTD也不从proposal过期自动推导。

## 9. CapabilityRegistry：不让模型做出当前环境根本不能执行的计划

注册表键至少包含 venue、product、instrument、account、environment、transport、adapter_build 和 rule_revision。记录官方来源日期、实现状态、各测试证据、支持的操作组合和明确拒绝原因。

向模型传入冻结的、与授权绑定的capability snapshot，给出适用类型/模板与约束，而不是原始密钥、任意REST参数或几千行API文档。模型提案引用snapshot id；发送前重新验证，能力修订变化后返回重新规划而非偷偷改类型。

必须区别：

- `FEATURE_NOT_IMPLEMENTED`：产品缺口，进入交付清单。
- `ADAPTER_NOT_QUALIFIED`：代码存在但该环境证据不足。
- `VENUE_NOT_SUPPORTED / SYMBOL_NOT_ALLOWED / ACCOUNT_NOT_ELIGIBLE`：外部能力限制。
- `INVALID_PARAMETER_COMBINATION / RULE_REVISION_CHANGED`：请求或规则冲突。
- `RISK_REJECTED / SAFETY_POLICY_CONFLICT`：资金/保护政策不允许。
- `SIMULATION_DATA_INSUFFICIENT`：缺行情深度/有效交易流，不能编造成交。

短期按能力约束AI并不能代替最终实现全目录；验收要追踪所有target_required功能，不得通过不暴露LIMIT来宣称全覆盖。

## 10. PAPER必须增加什么

保留现有受限PaperEngine、独立DB和手工事件注入作为故障验证端。新增市场驱动Matcher，以同一订单规格和规则快照接收市场事件，用唯一、可重放事件产生状态，再经现有账本摄取。

1. MARKET按对手报价/可见深度和明确滑点模型撮合，不能bid=ask=last且无限流动性。
2. LIMIT未穿价时保留在簿；穿价也不是“看到最后价格就整单成交”，消耗可用流动性、考虑排队假设。单一市场成交不能为多个模拟订单重复提供无限量。
3. IOC部分成交后余量失效；FOK全量可满足才产生fill；post-only会吃单时拒绝；GTC/GTD/DAY各自生命周期正确。
4. Spot动态price-range execution rule、参考价和EXPIRED原因应参与模拟，不能仅在接收时验一次tick/step；平台规则不可得时记录模型假设与缺失。
5. stop必须先由指定价格源触发，再变成对应普通订单；trailing持久保存激活状态与高/低水位。订单簿不同层、MARK与LAST不能混用。
6. 列表激活/互斥/到账数量/锁币遵循官方语义；列表接受不等于全部保护已工作。
7. Keep-priority减量、cancel-replace和USDM限价modify分别实现队列与两阶段结果。
8. 原生TWAP/VP模拟器采用公开假设；母单接受/停止不意味着子单全成/全撤。未知子单继续核验。
9. 重启恢复挂单、触发高水位、列表激活、算法进度、事件去重、资金预留和未明命令，不重放旧买入。

只有OHLCV、尤其一根bar同时触发止盈和止损时，路径不可知：用更细事件回放，或标记歧义并采用事先声明的保守规则；禁止总选有利路径。没有RPI对手盘/队列数据时不能宣称精确复现；官方订单语义支持不等于收益仿真精确。

## 11. 资金和保护重构

挂单用limit最坏支付价和费用上限预留；挂钩/匹配价格用明确预算边界和有效规则检查。模型给4000 USDT目标，不代表可以在不受控价格下无限增大支出。quoteOrderQty、证券notional、期货张数与base quantity需分别映射。

OCO预算按不能同时履约的原生互斥语义分组，不简单双扣；本地软件OCO不具备原生原子性，需处理两腿竞态。OPO使用净到账量，入场手续费以基础币扣除时保护量不能大于可售量。native算法母单与子单的预留按“已被场所反映”和“尚未反映”分开，避免重复扣或提前释放。

**不能为了“所有订单都能用”牺牲部分成交保护：**OTOCO/OPOCO在工作单完全成交后才激活后续保护。若策略要求首笔部分成交立即受保护，原生组合可能与安全政策不兼容，且OPO到账资金锁定也可能阻止额外保护。系统应明确拒绝该组合或在获得授权后选择独立限价入场+按成交量管理保护，不能谎称原生列表已覆盖部分仓位。[B05](https://developers.binance.com/en/docs/products/spot/faqs/opo)[B01](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)

改撤旧保护不盲目删除；条件未知先查询；合法减仓仍要验证当前量、腿和owner。closePosition按真实腿归属，不得平掉另一策略或人工的净仓。

## 12. 对这笔 4000 USDT / LIMIT 81490 / STOP 81330 的验收

这组数值只作为用户提供的回归案例，不作为当前交易建议。

- 明确是Spot或USD-M、买入腿、TIF、提交有效期、保护策略、费用口径；缺失关键语义要求结构化补正，不能偷设高风险默认值。
- 若从名义金额转成限价数量，用批准的limit/预算口径与动态step、实际已有仓位/在途订单计算。不得仍用任意latest price转换后遗漏limit。
- 保留81490限价，计算费用预留；卖盘高于买限价时挂单等待，不自动追成市价。
- 部分成交只给实际可管理数量建立保护，并继续管理余单风险。
- LAST到81330且已建立相应止损责任时，执行stop-market的模拟触发；实际成交价格由撮合模型决定，不强行设为81330。
- 计划到期只处理该计划的余单与授权动作；已成交仓位的保护/对账不随proposal一起失效。

## 13. 代码结构调整清单

| 现有位置 | 调整方向 |
|---|---|
| contracts/wire.py与四份契约Schema | 新修订类型化OrderSpec/OrderProgram；旧版本只读/精确迁移；不用扩旧enum重解释旧指令 |
| server/paper_runtime.py | 拆出编译器、能力读取、规则快照、产品账户语义；保留统一事务与owner组合入口 |
| server/automation.py | 提交完整计划，不负责自己选择平台参数或降低订单类型；同一入口服务单模型/交叉/规则 |
| engine/paper.py | 保留合成后端；关联新的matcher/event producer，不在submit时编造成交 |
| execution/（拟新增order_plan.py/compiler.py/order_groups.py） | 原子单/列表/算法状态机与原生字段映射中间结果 |
| accounts/capabilities.py（已有则扩展） | 四层能力与资格、来源修订、组合规则，不只boolean |
| market/（拟新增instrument_rules.py） | exchangeInfo/filter快照、时钟/交易日历/报价币；合成夹具明确标记 |
| risk/reservations及exposure | 挂单/互斥/派生金额/算法子单/不同费用资产风险核算 |
| protection/manager.py及replacement | 触发源、trailing、组关系、真实覆盖与部分成交保护 |
| engine/nautilus/ | 锁定build能力映射；扩展原生client而非开旁路；不同订单服务命名空间 |
| server API / DB / Web / CLI | 多级状态、拒绝阶段、父子单与证据、明确支持矩阵、取消与调整语义 |

文件名标“拟新增”的是设计提议，实施前以本地实际结构对齐。不能为了符合目录表复制第二份已有功能。

## 14. 建议工作包（同一最终成果，非产品版本分期）

- **OP-A 官方目录与兼容闸门**：固化本次目录来源、动态产品规则、能力状态；核验Nautilus精确build的每项映射。
- **OP-B 契约与迁移**：OrderSpec/Program、权限/期限模型、历史记录双读和hash保持，禁止旧UNKNOWN自动重新编译。
- **OP-C 统一计划编译与风险**：去除MARKET硬编码、目标金额/费用/互斥预留、引用唯一能力快照。
- **OP-D PAPER市场撮合**：市场/限价/TIF、条件/追踪/挂钩、列表、SOR/算法模型的重放与明确假设。
- **OP-E 原生端点与生命周期适配**：普通/列表/条件算法/执行算法/股票分流，查询/撤改/回报/恢复全覆盖。
- **OP-F 管理与观测**：AI能力上下文、控制API、Web/CLI、父子单状态及拒绝分类。
- **OP-G 故障与语义验收**：官方案例、项目原安全矩阵、新订单目录逐项证明，限制与未准入状态如实报告。

依赖：A→B→C；B后D/E可在独立worktree并行但共享合同串行；F依赖冻结契约，G汇总所有实际证据。合并按项目Harness执行，Codex负责契约/资金/执行，OpenCode只做已冻结接口下的展示或文档。不得让一个Agent边改Schema边让另一个按旧Schema写撮合。

所有完整目标都保留；可按依赖交付，不把剩余高级订单写成可永久忽略的“以后再说”。没有Testnet/LIVE权限时仍交付实现与本地证据，但相应场所资格继续NOT_RUN；不删除能力项或伪造通过。

## 15. 必须保留的安全和兼容边界

- 不移除风控、owner锁、durable-before-send或UNKNOWN规则来解决unsupported。
- 不自动把LIMIT改MARKET、MARK改LAST、原生组合改本地组合、真实股票改代币、GTD改GTC。
- 新增native与合成写路径仍由唯一owner掌握密钥与permit；AI只提交结构化意图。
- 适配器支持enum不等于支持全生命周期；每一项至少有下发、回报、查询、撤改适用性、重启恢复和资金保护证据。
- 上游动态schema或文档变化只生成差异报告，不在生产自动授权新功能或重写策略。
- 旧SPOT_ONLY和合成费率夹具继续服务回归，不用它们替代实际symbol规则。
- 本次设计需用户评审；批准原SPEC需正式修订并记录兼容策略，不能靠“不改Schema”拒绝完整功能，也不能静默篡改已存历史。

## 16. 本次交付及验证状态

本目录包含主报告、46项订单语义能力清单（不是46种独立订单type）、11项源码发现、56项新增验收设计、文档差异清单与Codex实施交接。所有新增交易验收为NOT_RUN。

本次只对文档/JSON、引用和覆盖完整性做本地检查；没有执行产品编译、数据库场景、模型调用或平台订单。仓库最新读到的报告记载一些此前本地测试已完成，那是项目内证据，不能推断本次订单全覆盖已经通过。[C08](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/docs/FINAL_PAPER_REPORT_2026-10-28.md)

## 17. 来源索引

- [B01 · Binance Spot 交易接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)
- [B02 · Spot 枚举](https://developers.binance.com/en/docs/products/spot/enums)
- [B03 · Spot 跟踪止损 FAQ](https://developers.binance.com/en/docs/products/spot/faqs/trailing-stop-faq)
- [B04 · Spot Pegged Orders](https://developers.binance.com/en/docs/products/spot/faqs/pegged_orders)
- [B05 · Spot OPO / OPOCO 语义](https://developers.binance.com/en/docs/products/spot/faqs/opo)
- [B06 · Spot SOR](https://developers.binance.com/en/docs/products/spot/faqs/sor_faq)
- [B07 · Spot filters](https://developers.binance.com/en/docs/products/spot/filters)
- [B08 · Spot 保留优先级改单](https://developers.binance.com/zh-CN/docs/products/spot/faqs/order_amend_keep_priority)
- [B09 · USD-M 交易接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
- [B10 · USD-M 变更日志](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)
- [B11 · USD-M 通用定义](https://developers.binance.com/zh-CN/docs/products/derivatives-trading-usds-futures/common-definition)
- [B12 · Stocks 下单/查询/撤单](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)
- [B13 · Stocks 枚举与状态](https://developers.binance.com/en/docs/products/stocks/common-definition)
- [B14 · Stocks 用户推送](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/ws-streams/user-streams)
- [B15 · Spot TWAP / VP](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo)
- [B16 · USD-M TWAP / VP](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo)
- [B17 · Binance 产品目录/范围](https://developers.binance.com/en/docs/catalog)
- [B18 · Spot 请求超时与接口通则](https://developers.binance.com/en/docs/products/spot/rest-api)
- [N01 · Nautilus Binance 适配说明（latest 文档，不等于项目锁定构建）](https://nautilustrader.io/docs/latest/integrations/binance/)
- [C01 · 统一 PAPER 运行时](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/paper_runtime.py)
- [C02 · 受控合成 PaperEngine](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/engine/paper.py)
- [C03 · 当前 wire 契约](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/contracts/wire.py)
- [C04 · 自动策略调度/执行](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/automation.py)
- [C05 · 保护指令编译与检查](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/protection/manager.py)
- [C06 · 命令分发与锁内动作验证](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/execution/dispatcher.py)
- [C07 · Nautilus bridge](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/engine/nautilus/bridge.py)
- [C08 · 现有 PAPER 工程报告（仓库自述）](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/docs/FINAL_PAPER_REPORT_2026-10-28.md)
- [C09 · 当前目标数量辅助函数](https://github.com/Wuysau/AetherTrade/blob/b168966d350ed60fe8a92792f9519f4227054bc5/src/trading_system/server/decision_execution.py)
- [C10 · 提交元数据](https://github.com/Wuysau/AetherTrade/commit/b168966d350ed60fe8a92792f9519f4227054bc5)

- [B19 · Spot价格范围执行规则](https://developers.binance.com/en/docs/products/spot/faqs/price_range_execution_rules)
