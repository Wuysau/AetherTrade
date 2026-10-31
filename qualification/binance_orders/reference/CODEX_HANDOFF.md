# Codex交接｜订单全覆盖定向重构

**本文件是待评审改造方案的实施交接条件，不表示已经获得实盘授权，也不表示代码已实现。**

## 先读与固定基线

阅读本包主报告、订单语义目录、源码发现、文档差异和验收矩阵。审查基线是`public-snapshot/b168966d350ed60fe8a92792f9519f4227054bc5`。开始实施前固定本地当前提交，与基线比较；使用最新代码事实，保留原有统一PAPER链，不能按早期`acbdcb2`的独立即时成交实现覆盖新代码。

本地存在AGENTS、Harness、已批准SPEC/Schema时先读取其入口，按需加载规则。无需每轮加载无关技能，但契约/资金/命令安全要求必须遵守。当前目录/远端/未提交修改不擅自重置。

## 架构决定

推荐方案是保留执行所有者、PostgreSQL账本、原子预留、持久化命令、UNKNOWN、锁内减险及保护责任；重构AtomicOrderSpec、OrderProgram、PlanCompiler、CapabilityRegistry、产品规则、市场驱动PAPER撮合和受控Binance适配。

只扩订单enum或删`order_preference != MARKET`拒绝条件是不合格修复：下游intent和command仍写死MARKET，必须端到端保留所有参数。不能为了“必成交”改写订单或删除风控。

## 实施前闸门

1. 用户评审主报告；涉及既有冻结契约时提交清晰的ADR与schema修订、历史兼容方案。不要静默修改原始记录/hash。
2. 以本包46项能力作为完整目标目录，不将高级单长期删掉；官方新参数变化以更新目录+回归的方式管理。
3. 核验官方文档、账户/标的规则、精确Nautilus构建。文档有冲突的字段先取证；本包DOC-01..04不能被猜测替代。
4. 无资金写权限和付费模型调用。PAPER为本地合成测试；Testnet/Demo/LIVE各自需要独立授权与资格，不能从PAPER全绿自动升级。

## 工作包与边界

OP-A目录/规则/版本兼容 → OP-B typed契约/组状态/迁移 → OP-C编译/风险/资金。契约冻结后OP-D模拟器与OP-E受控native adapter可以独立worktree开发；OP-F UI/API以同一契约工作；OP-G集成原有安全矩阵和本包56场景。

复杂契约、编译、风险、撮合、重启、native adapter交给Codex；OpenCode仅在契约固定后负责限定展示和文档。一个worktree只允许一个写入者，公共Schema/迁移/锁文件串行管理。不能让不同Agent分别再建一套资金账本。

## 关键实现要求

- ordinary order、order list、conditional algo、execution algo分命名空间；原生list/algo母单durable后才提交，平台生成的子单只归档/摄取，不再次下发。本地拆单则每个子单都单独durable。
- 发单前动态规则和授权重验；模型读取的capability snapshot与实际编译链一致，仍允许数据/风险/账户原因拒绝。
- 目标名义金额、已持有、活动余单、未知命令、基础币费用/报价币费用正确计算；OCO互斥与OPO派生数量不是普通单简单求和。
- 原生订单与本地合成模式明确区分，类型不能静默降级；触发源、TIF、peg/trailing/iceberg/session等参数不能丢。
- 首次提交建议有效期、交易所在场期限、持续管理授权、单次write permit分开；旧proposal到期不遗失GTC/止损责任。
- 保留手工事件注入端，新增有明确数据/队列/滑点假设的market-driven matcher。没有市场路径证据不得把bar触价当无限量完整成交。
- 真正的Spot/Futures/Stocks使用各自账户和原生字段；股票tokenize=false，不以bStocks/股票永续替代真实股份。
- 同一execution-owner是唯一密钥持有和写入口。Nautilus缺少某特性时扩展已锁定客户端并归一事件，不从server/AI旁路请求交易所。

## 测试与交付

遵循测试先行：为每项语义写失败用例，运行并保存真实RED原因，实现后GREEN与负例，再做独立审查。按56场景逐项记录，不能用只验证请求的`/order/test`或Mock替代撮合/数据库/重启证据。

使用用户给出的4000 USDT、LIMIT 81490、STOP 81330作为固定回归夹具，不当作当前行情交易建议。证明limit未变market、挂单/部分成交/保护/取消/过期正确，并保留成本、数量和规则修订。

最终交付：完整能力矩阵、实现与未准入分开、文件与提交清单、测试命令/退出码/环境/原始证据、订单生命周期示例、迁移/回滚说明、文档差异解决情况。凡未运行仍NOT_RUN；不能宣称“支持所有订单”只因为Schema枚举齐全。
