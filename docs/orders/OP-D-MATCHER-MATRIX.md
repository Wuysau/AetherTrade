# OP-D source-specific 56-row matrix

This retains the full original matrix. Status describes only this branch at
`1b12ed9` (21 V2 PG +19 V1-on0014 PG +59 legacy PG +1525 Python). It is not
the final integrated matrix, native qualification, or completion of any entire
cross-package scenario. Other package evidence is maintained by its owner.

| ID | Original scenario | OP-D component status | Remaining requirement / evidence scope |
| --- | --- | --- | --- |
| ORD-01 | 原提案限价不被改写 | V1 component GREEN | 4000/LIMIT81490/STOP81330 and native quantity preserved; combined host/protection still due |
| ORD-02 | 普通市价与限价双产品路由 | PARTIAL | Spot BASE MARKET and USD-M fixed/GTD; complete product routing/fees/native adapter still due |
| ORD-03 | 现货post-only不吃单 | V1 component GREEN | Real post-only crossing rejection/resting fill; no native qualification |
| ORD-04 | 现货条件单四种映射 | PARTIAL | Actual Spot stop-limit LAST path; every direction/type and conditional membership still due |
| ORD-05 | USD-M条件类型走algo端点 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-06 | Spot追踪止损激活与单位 | PARTIAL | SELL BIPS extrema/recreated worker and complete causal samples; full matrix variants still due |
| ORD-07 | Futures跟踪止损上限差异 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-08 | 触发源与priceProtect | PARTIAL | LAST versus MARK separation; USD-M priceProtect and BID_ASK actual combinations still due |
| ORD-09 | Spot GTC/IOC/FOK | V1 component GREEN | GTC/IOC/FOK shared depth and FOK preflight; final host integration still due |
| ORD-10 | USD-M GTX/GTD/RPI | PARTIAL | GTD replay clock plus V1 post-only; RPI source/qualification remains unavailable |
| ORD-11 | 数量与quoteOrderQty | PARTIAL | BASE MARKET and fixed-limit quote target; native MARKET quote-budget branch still due |
| ORD-12 | 真实深度与单次流动性消耗 | COMPONENT GREEN | Finite shared depth, exact replay/no double supply, multilevel/FOK, concurrency/fork; declared model only |
| ORD-13 | 挂钩价格与偏移限制 | PARTIAL | PRIMARY_PEG offset1 actual latching; MARKET_PEG/full combinations still due |
| ORD-14 | 冰山展示与预算 | PARTIAL | Actual iceberg hidden budget/reveal/FIFO; full queue/native qualification still due |
| ORD-15 | SOR路由与allocation | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-16 | OCO互斥与部分成交语义 | PARTIAL | Native OCO trigger cancellation and partial fill; list placement filters/cancel/race cases still due |
| ORD-17 | OTO等待工作单全成 | PARTIAL | OTO/OTOCO actual full working fill; cancellation/late recovery cases still due |
| ORD-18 | OTOCO部分仓位保护冲突 | PARTIAL | Pending stays inactive at40% actual fill; protection conflict/host duty qualification remains separate C/G work |
| ORD-19 | OPO净到账、费用与锁币 | NOT_RUN | Actual net receipt/base versus external fees/LOT_SIZE/dust/locked receipt model still due |
| ORD-20 | OPOCO取消与动态数量 | NOT_RUN | OPOCO dynamic receipt sizing and cancellation/lock accounting still due |
| ORD-21 | Spot keep-priority改量 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-22 | cancel-replace的部分成功 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-23 | STP非成交失效 | PARTIAL | Same-account three expiry modes and DECREMENT actual; USD-M/group/TRANSFER/combinations still due |
| ORD-24 | 动态执行价格区间 | NOT_RUN | Actual published execution price-range rules/expiryReason still due |
| ORD-25 | USD-M减仓与hedge字段 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-26 | priceMatch与固定价互斥 | COMPILER ONLY | Typed priceMatch resolver exists; actual complete priceMatch execution cohort still due |
| ORD-27 | USD-M LIMIT modify与条件单未触发 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-28 | USD-M批量部分成功 | NOT_RUN | Actual batch individual accept/reject/UNKNOWN execution still due |
| ORD-29 | Spot原生TWAP | NOT_RUN | Actual native TWAP parent/child/future/stop model still due |
| ORD-30 | Spot原生VP | NOT_RUN | Actual native VP volume/urgency parent/child model still due |
| ORD-31 | USD-M原生TWAP | NOT_RUN | Actual USD-M native TWAP service/contract/child model still due |
| ORD-32 | USD-M原生VP | NOT_RUN | Actual USD-M VP versus conditional service identity model still due |
| ORD-33 | 本地算法子单授权 | BLOCKED DEPENDENCY | Actual local activation exists; independent current child-stage financial admission/command interface still needed |
| ORD-34 | Stocks MARKET不对称字段 | NOT_RUN | Actual equity MARKET BUY native notional and SELL quantity execution still due |
| ORD-35 | Stocks LIMIT与份额 | PARTIAL | Actual equity SELL LIMIT/DAY; BUY wallet/fees/full share semantics still due |
| ORD-36 | Stocks DAY/GTC与交易会话 | PARTIAL | Real RTH/DAY shared market plus typed IANA DST validation; halt/EXTENDED/24H/DST execution still due |
| ORD-37 | Stocks普通/全部撤销与查询 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-38 | 订单/列表/算法身份与查询 | PARTIAL | Original command/account/product/member IDs and real venue IDs; all service identities/WS duplicates still due |
| ORD-39 | 未知结果跨传输禁止重试 | PARTIAL | Single-use actual consume/replay denial preserves original IDs; full cross-transport UNKNOWN remains G |
| ORD-40 | 部分成交后保护与减仓竞争 | NOT_RUN | Actual combined protection/manual reduction concurrency remains G |
| ORD-41 | 保护失败且紧急退出失败 | NOT_RUN | Actual combined failed protection/emergency exit recovery remains G |
| ORD-42 | 普通GTC与短期提案TTL | PARTIAL | Proposal/permit/GTD clocks distinct and legacy GTC retained; full maintenance authorization integration remains C/G |
| ORD-43 | 滚动限价预算和重叠委托 | PARTIAL | Actual financial hold/remaining source retained; quote-native budget/all assets/target concurrency integration still due |
| ORD-44 | 原生与软件OCO差异 | NOT_RUN | Local OCO race must use independent real child commands; no native model exemption |
| ORD-45 | 能力向模型与执行同步 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-46 | 字段丢失的编译回归 | PARTIAL | Complete original fields and execution source contexts retained; every modifier/transport/consumer still due |
| ORD-47 | 旧schema与UNKNOWN迁移 | PARTIAL | Frozen0013/old DTO hashes and legacy regression retained; final combined UNKNOWN migration remains B/G |
| ORD-48 | 重启恢复列表和追踪状态 | PARTIAL | Actual V1 fork/replay and V2 extrema/persistent list state; list/algo process interruption full gate still due |
| ORD-49 | 不同产品账户语义 | PARTIAL | Explicit scope/native units and equity USD/AAPL/USDM fixtures; complete margin/fee/settlement model still due |
| ORD-50 | 只用OHLCV的路径歧义 | NOT_RUN | No OHLC path inference exists; full ambiguity regression/declared conservative model still due |
| ORD-51 | 隔离PAPER合成证据 | PARTIAL | Exact SYNTHETIC raw bytes/source hashes retained; V2 normalizer/ledger source qualification still due |
| ORD-52 | API测试端点不冒充撮合验收 | NOT_NATIVE_QUALIFIED | Real isolated PG tests are component evidence; no /order/test or fake HTTP promoted to execution qualification |
| ORD-53 | 未文档化字段和文档分歧 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-54 | 短期模型指令不能越权 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |
| ORD-55 | 完整目录防虚假缩范围 | CATALOG RETAINED | All46 capabilities and all56 rows retained; unsupported/unfinished statuses remain explicit |
| ORD-56 | 用户界面和API错误分类 | OTHER PACKAGE | No OP-D completion claim; retain original C/E/F/G requirement and independent source evidence |

Source requirements: `TradingHandoff/aethertrade-binance-orders/ACCEPTANCE_MATRIX.md`
and architecture report section10, both read without changes. Full46 catalog
remains unchanged. Raw source-bound evidence is in
[OP-D V2 evidence](../evidence/binance-orders/op-d-matcher-v2/README.md).
