# Binance订单语义新增验收矩阵

**全部为待执行设计：NOT_RUN。** 本次未运行这些产品、数据库、平台测试。它们补充原有安全矩阵，不替换或删改旧场景。

每项证据绑定：代码提交、native build/digest、环境、账户/产品（脱敏）、规则/能力快照、测试输入、故障时间线、原始事件、前后资金及持仓、操作命令与退出码。

| ID | 场景 | 输入/故障 | 必须观察到的结果 | 工作包 |
|---|---|---|---|---|
| ORD-01 | 原提案限价不被改写 | 输入4000 USDT目标、LIMIT 81490、LAST止损81330；账户/标的/时间窗口为可控测试夹具。 | 编译链和原生/模拟请求保留限价、止损及预算；卖盘高于限价时不成交；不能因移除guard而发MARKET。 | OP-C |
| ORD-02 | 普通市价与限价双产品路由 | 同名BTCUSDT分别SPOT与USD_M_PERP，MARKET/LIMIT输入。 | 身份、数量单位、普通订单路由和费用/保证金语义各自正确，不能按symbol误路由。 | OP-E |
| ORD-03 | 现货post-only不吃单 | LIMIT_MAKER买价会与已有卖价立刻成交。 | 按平台语义拒绝，不静默改普通LIMIT；未穿价则挂单。 | OP-D |
| ORD-04 | 现货条件单四种映射 | 对不同方向分别设置STOP_LOSS/STOP_LOSS_LIMIT/TAKE_PROFIT/TAKE_PROFIT_LIMIT。 | 触发前不成交，触发后市价/限价区别正确；trigger与limit不是同一价格；域名不能原样套USD-M。 | OP-C |
| ORD-05 | USD-M条件类型走algo端点 | 提交STOP、STOP_MARKET、TAKE_PROFIT、TAKE_PROFIT_MARKET、TRAILING_STOP_MARKET。 | 新条件单使用fapi algo端点，普通order和batch不得接纳不允许的条件类型；普通子单ID与algoID关联。 | OP-E |
| ORD-06 | Spot追踪止损激活与单位 | trailingDelta的BIPS输入、可选stopPrice、极值变化和重启。 | 严格区分BIPS/百分比，按方向/条件类型跟踪，未激活不使用错误极值，恢复不丢高低水位。 | OP-D |
| ORD-07 | Futures跟踪止损上限差异 | 在规则快照中分别测试callbackRate 0.1、10、20、越界值；锁定adapter旧文档范围。 | 依据有效平台规则和精确build判断；20不能仅因过时文档拒绝，也不能无测试放行；记录差异与修复证据。 | OP-A |
| ORD-08 | 触发源与priceProtect | LAST/CONTRACT_PRICE与MARK_PRICE不同步、保护价差条件不满足。 | 触发源不丢失、不替换；priceProtect条件延迟触发时可解释，不能把域BID_ASK静默转LAST。 | OP-C |
| ORD-09 | Spot GTC/IOC/FOK | 有限流动性，只能立即成交一部分。 | GTC保留合法余量、IOC余量按平台终态退出、FOK无法全成则无部分fill；allow_partial布尔不冒充TIF。 | OP-D |
| ORD-10 | USD-M GTX/GTD/RPI | post-only会吃单、GTD时点、RPI适用账户与对手渠道不同。 | 按类型合法组合处理；不默默GTD→GTC；RPI资格/数据不明不声称普通队列等价仿真。 | OP-D |
| ORD-11 | 数量与quoteOrderQty | 现货MARKET以quoteOrderQty或base quantity下单，含费用和可用余额不足。 | 区分目标notional与线上quoteOrderQty，正确预留和实际数量；不对不支持类型填字段。 | OP-C |
| ORD-12 | 真实深度与单次流动性消耗 | 同一市场成交事件被多个模拟订单看到、同事件重复到达。 | 分配一次可见流动性，不无限量全成；事件去重；市价按对手价及声明模型，不用last直接FILLED。 | OP-D |
| ORD-13 | 挂钩价格与偏移限制 | PRIMARY_PEG/MARKET_PEG，不合法LIMIT_MAKER MARKET_PEG或组合。 | 依官方支持组合检查；挂入簿/触发定价与后续价格变更正确，不臆造每tick自动追价。 | OP-D |
| ORD-14 | 冰山展示与预算 | 总量与icebergQty、非GTC或超ICEBERG_PARTS。 | 限制按当前规则；隐藏量仍计资金风险；模拟揭示/排队模型公开。 | OP-D |
| ORD-15 | SOR路由与allocation | 跨允许场所/报价资产分配、部分成交与撤销。 | 母单/分配/成交/费用一致，不能只保存最终symbol总量；不把SOR当第二账户独立余额。 | OP-E |
| ORD-16 | OCO互斥与部分成交语义 | 任一腿触发或执行、取消另腿及事件乱序。 | 严格按当前官方OCO状态转换测试，不能简单假设只有FILLED才产生互斥效果；原生与软件OCO不同。 | OP-D |
| ORD-17 | OTO等待工作单全成 | 工作LIMIT部分成交、撤销，随后全成两条场景。 | 只在官方定义工作单全成条件满足后激活pending，不因部分成交就宣称已触发。 | OP-D |
| ORD-18 | OTOCO部分仓位保护冲突 | 入场仅部分成交且策略要求即时保护。 | 明确原生列表未激活的暴露与政策冲突；不虚报保护，选替代计划须先有授权。 | OP-C |
| ORD-19 | OPO净到账、费用与锁币 | BUY工作单、基础币费用/可用余额费用不同，LOT_SIZE调整。 | 派生pending数量来自真实净到账与官方规则；工作资产锁定、不重复花费，dust正确解锁。 | OP-D |
| ORD-20 | OPOCO取消与动态数量 | 工作单部分成交后取消列表、全成后触发两个pending。 | 锁定资金与余量按真实事件解锁；无自己猜测的pending qty；列表/child关联可恢复。 | OP-D |
| ORD-21 | Spot keep-priority改量 | 仅减量与尝试加量/改价/受限pending改量。 | 只执行允许减量并保留优先级；不合法修改拒绝/按明确cancel-replace政策处理；组合单联动正确。 | OP-E |
| ORD-22 | cancel-replace的部分成功 | cancel失败/new成功、cancel成功/new失败、任一结果UNKNOWN。 | 两结果分别记录与查询，按当前cancelReplaceMode处理，不能原子化成全成功或全失败。 | OP-E |
| ORD-23 | STP非成交失效 | 现货和USD-M不同STP模式/同组账户自匹配。 | 严格区分prevented quantity、真实Fill和终态；资金不以不存在成交记账；模式只允许官方支持集合。 | OP-D |
| ORD-24 | 动态执行价格区间 | 吃单越过当前执行price-range，或者参考价更新。 | 处理EXPIRED及expiryReason；不同于下单过滤器，不能无限滑点填完订单。 | OP-D |
| ORD-25 | USD-M减仓与hedge字段 | one-way/hedge、reduceOnly、closePosition+quantity、不同leg。 | 域减仓意图经当前实仓验证；线上互斥字段按平台规则，不发送hedge禁用组合，不关掉外部腿。 | OP-C |
| ORD-26 | priceMatch与固定价互斥 | 同时给price和priceMatch，或枚举已变化。 | 拒绝非法组合；以当前目录/锁定版本映射合法值；编译不丢价格模式。 | OP-C |
| ORD-27 | USD-M LIMIT modify与条件单未触发 | 改普通LIMIT与尝试直接改未触发algo。 | 普通改单优先级变化准确；不支持的algo修改明确处理为授权撤旧建新或拒绝，保留暴露窗口。 | OP-E |
| ORD-28 | USD-M批量部分成功 | 批内部分拒单、单项ACK丢失。 | 逐单身份/资金/状态；不假设事务性all-or-none；UNKNOWN项不换ID重发。 | OP-E |
| ORD-29 | Spot原生TWAP | 母单受理、子单部分成交、母单停止但子单尚未核清。 | 提交母单前durable；原生子单仅摄取不再次发出；停止非全撤；剩余责任保持。 | OP-E |
| ORD-30 | Spot原生VP | 成交量变化、urgency、母子查询和报告。 | 参数合法性与算法命名空间正确；PAPER模型假设明示，不声称精准重建专有算法。 | OP-E |
| ORD-31 | USD-M原生TWAP | futures TWAP母单、合约资金/子单/撤单。 | 走sapi执行算法而非fapi条件算法；正确跟踪母子/保证金及所有权。 | OP-E |
| ORD-32 | USD-M原生VP | futures VP与条件algo相同整数algoId冲突。 | ID包含服务命名空间和账户产品，不误查误撤另一algo；子单成交对账完整。 | OP-E |
| ORD-33 | 本地算法子单授权 | 本地拆单在计划到期/epoch变化/暂停后继续产生子单。 | 每次本地平台写动作独立durable/permit；未发停止，已发核查，不能照搬原生子单豁免。 | OP-C |
| ORD-34 | Stocks MARKET不对称字段 | BUY MARKET使用notional、SELL MARKET使用quantity，反例混入price/tradingSession。 | 对应合法组合，tokenize=false，真实持股产品；不是SPOT或token替代。 | OP-E |
| ORD-35 | Stocks LIMIT与份额 | LIMIT BUY/SELL price+quantity+tradingSession，交易所手续费冻结。 | 参数、数量、费用和可售份额正确；无隐式融资/额外划转；请求S不入账成交。 | OP-E |
| ORD-36 | Stocks DAY/GTC与交易会话 | 零碎股GTC、RTH/EXTENDED/24H、休市/停牌/夏令时。 | 按当前规则与资格校验；托管触发但未成交为待市场，不假报止损。 | OP-D |
| ORD-37 | Stocks普通/全部撤销与查询 | 请求确认、已部分成交、再次撤销、历史分页。 | 确认不是终态；保留数量、费用、在途和实际退出责任，批量全撤需明确账户级授权。 | OP-E |
| ORD-38 | 订单/列表/算法身份与查询 | 同client字符串在不同账户/产品/服务复用、API和WS重复回报。 | 按真实命名空间正确路由和去重，查询/撤销只作用合法owner，绝不symbol误撤他人。 | OP-E |
| ORD-39 | 未知结果跨传输禁止重试 | REST请求已接受但ACK丢失，尝试改用WS发送。 | 只查询原ID/订阅证据，不因换协议或新key再发；预算持续占用。 | OP-G |
| ORD-40 | 部分成交后保护与减仓竞争 | 入场余单仍可能成交、保护更新、人工减仓并发。 | owner/epoch及实际量核验；按真实可管理量覆盖，不把退出拒绝升级为放宽权限。 | OP-G |
| ORD-41 | 保护失败且紧急退出失败 | 保护ACK未知/拒单，然后减仓失败。 | 保留风险锁与恢复事项；原proposal过期不结束责任；不继续新开仓。 | OP-G |
| ORD-42 | 普通GTC与短期提案TTL | 30秒后报价/提案过期，挂单仍有效。 | 禁止旧建议再增险，但保持合法查询/撤单/保护管理，新的写动作重新授权；不自动丢单。 | OP-C |
| ORD-43 | 滚动限价预算和重叠委托 | 目标4000，已有持仓、挂单、quote数量及非报价币费用并存。 | 按最坏合法执行价和费用预留；净目标不重复，UNKNOWN余量不释放给第二策略。 | OP-C |
| ORD-44 | 原生与软件OCO差异 | 本地两腿各独立发送，在取消前另一腿也成交。 | 承认非原子并处理双成交/资产约束；用户未允许software模式时拒绝，不标native支持。 | OP-G |
| ORD-45 | 能力向模型与执行同步 | 模型快照显示LIMIT可用，执行前规则/资格修订改变。 | 引用capability snapshot并发送前重验，报告重新规划或资格原因，不改MARKET、不误报余额不足。 | OP-F |
| ORD-46 | 字段丢失的编译回归 | 给MARK_PRICE/trailing/peg/iceberg/session/TIF等非默认值。 | 逐字段从Proposal到Plan/Command/平台payload/归档双向核验；未知或丢失字段拒绝，不默认降级。 | OP-C |
| ORD-47 | 旧schema与UNKNOWN迁移 | 升级时有旧版未终态订单与旧提案hash。 | 旧codec/原ID恢复，历史hash不改；新写用明确修订，不重编旧UNKNOWN为新订单。 | OP-B |
| ORD-48 | 重启恢复列表和追踪状态 | 在部分成交、列表激活、trail极值更新或algo产生子单时进程中断。 | 恢复后真实证据重建，不重复激活/提交，保护、预留和算法进度一致。 | OP-G |
| ORD-49 | 不同产品账户语义 | Spot余额、USD-M保证金/资金费、Stocks可卖份额/结算资产同时存在。 | 不套SPOT+USDT+24H常量，不把标的字符串当账户/产品唯一身份。 | OP-C |
| ORD-50 | 只用OHLCV的路径歧义 | 同一bar里高低价同时穿止盈止损，缺逐笔顺序。 | 细粒度回放或标记歧义并采用预声明保守假设；不能一律挑盈利退出。 | OP-D |
| ORD-51 | 隔离PAPER合成证据 | 市场驱动模拟/手工事件回归产生SYNTHETIC状态。 | 统一领域摄取但证据来源保留；不能输出交易所已接受或已验证native protection。 | OP-G |
| ORD-52 | API测试端点不冒充撮合验收 | 调用/order/test得到成功或采用Mock校验。 | 只标参数验证，实际挂单/触发/撤销/恢复仍NOT_RUN，不自动解锁LIVE。 | OP-G |
| ORD-53 | 未文档化字段和文档分歧 | quoteNotionalLimit正文不可用，Stocks流出现未支持stop，native文档限制不一致。 | 保留DOC事项，不猜参数、不从读取类型推下单权限；查明且回归后再改目录。 | OP-A |
| ORD-54 | 短期模型指令不能越权 | 模型要求绕过unsupported、加杠杆、直接调用任意路径或全symbol撤单。 | 只产生typed Proposal；服务端权限和owner检查不因文字改变，所有真实写由同一受控客户端完成。 | OP-G |
| ORD-55 | 完整目录防虚假缩范围 | 适配器只暴露MARKET，试图把LIMIT/OPO/VP等从最终需求删除。 | 46项target_required保持跟踪；未实现和未准入区分；不把限制AI词汇当成订单全覆盖。 | OP-G |
| ORD-56 | 用户界面和API错误分类 | 执行能力不足、规则不合法、资金超限、数据不足、未成交挂单。 | 分别显示能力/数据/风险/执行阶段，展示原计划、受理与真实成交、父子单状态，保留具体未满足字段。 | OP-F |

## 能力到验收映射

| 能力ID | 功能 | 覆盖场景 |
|---|---|---|
| SPOT-MARKET | MARKET | ORD-02, ORD-11, ORD-12, ORD-51 |
| SPOT-LIMIT | LIMIT | ORD-01, ORD-02, ORD-43, ORD-45, ORD-56 |
| SPOT-LIMIT_MAKER | LIMIT_MAKER | ORD-03 |
| SPOT-STOP_LOSS | STOP_LOSS | ORD-01, ORD-04, ORD-40, ORD-41, ORD-50 |
| SPOT-STOP_LOSS_LIMIT | STOP_LOSS_LIMIT | ORD-04 |
| SPOT-TAKE_PROFIT | TAKE_PROFIT | ORD-04, ORD-50 |
| SPOT-TAKE_PROFIT_LIMIT | TAKE_PROFIT_LIMIT | ORD-04 |
| SPOT-LIST-OCO | OCO | ORD-16, ORD-44 |
| SPOT-LIST-OTO | OTO | ORD-17 |
| SPOT-LIST-OTOCO | OTOCO | ORD-18, ORD-44 |
| SPOT-LIST-OPO | OPO | ORD-19 |
| SPOT-LIST-OPOCO | OPOCO | ORD-20, ORD-48 |
| SPOT-TRAILING | trailingDelta | ORD-06, ORD-46, ORD-48 |
| SPOT-PEG | PRIMARY_PEG / MARKET_PEG | ORD-13, ORD-46 |
| SPOT-ICEBERG | icebergQty | ORD-14, ORD-46 |
| SPOT-SOR | Smart Order Routing | ORD-15 |
| SPOT-QTY | quantity / quoteOrderQty | ORD-11, ORD-43, ORD-49 |
| SPOT-TIF | GTC / IOC / FOK | ORD-09, ORD-42 |
| SPOT-AMEND | Order Amend Keep Priority | ORD-21 |
| SPOT-CANCEL_REPLACE | Cancel Replace | ORD-22 |
| SPOT-STP | selfTradePreventionMode | ORD-23 |
| SPOT-CANCEL_QUERY | Cancel / query / user events | ORD-38, ORD-39, ORD-47, ORD-52, ORD-54 |
| USDM-MARKET | MARKET | ORD-02, ORD-12 |
| USDM-LIMIT | LIMIT | ORD-02 |
| USDM-STOP | STOP | ORD-05 |
| USDM-STOP_MARKET | STOP_MARKET | ORD-05, ORD-08, ORD-40, ORD-41 |
| USDM-TAKE_PROFIT | TAKE_PROFIT | ORD-05 |
| USDM-TAKE_PROFIT_MARKET | TAKE_PROFIT_MARKET | ORD-05 |
| USDM-TRAILING_STOP_MARKET | TRAILING_STOP_MARKET | ORD-05, ORD-07, ORD-48, ORD-53 |
| USDM-TIF | GTC / IOC / FOK / GTX / GTD / RPI | ORD-10, ORD-42 |
| USDM-TRIGGER | CONTRACT_PRICE / MARK_PRICE + priceProtect | ORD-08, ORD-46 |
| USDM-REDUCTION | positionSide / reduceOnly / closePosition | ORD-25, ORD-49 |
| USDM-PRICE_MATCH | priceMatch | ORD-26 |
| USDM-STP | selfTradePreventionMode | ORD-23 |
| USDM-AMEND | LIMIT modify / batch modify | ORD-27 |
| USDM-BATCH | batch submit / cancel | ORD-28 |
| USDM-CANCEL_QUERY | ordinary / algo query-cancel and user events | ORD-38, ORD-39, ORD-47, ORD-54 |
| SPOT-EXEC-TWAP | TWAP | ORD-29, ORD-33, ORD-48 |
| SPOT-EXEC-VP | VP | ORD-30 |
| USD_M_PERP-EXEC-TWAP | TWAP | ORD-31, ORD-33 |
| USD_M_PERP-EXEC-VP | VP | ORD-32 |
| EQUITY-MARKET | MARKET | ORD-34, ORD-53 |
| EQUITY-LIMIT | LIMIT | ORD-35 |
| EQUITY-SESSIONS | DAY / GTC; RTH / EXTENDED / 24H | ORD-36, ORD-46, ORD-49 |
| EQUITY-CANCEL_QUERY | Cancel / query / private orderReport | ORD-37, ORD-39 |
| SPOT-EXECUTION_RULES | Dynamic price-range execution rules / expiryReason | ORD-24 |
