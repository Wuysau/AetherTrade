# AetherTrade Binance订单改造审查包

基线：`b168966d350ed60fe8a92792f9519f4227054bc5`，2026-10-28。状态：设计建议，未修改产品、未运行交易测试。

阅读顺序：

1. [主报告](AetherTrade_Binance_Order_Architecture_2026-10-28.md)：官方能力、最新代码发现、定向重构和完整目标。
2. [能力目录](binance-order-capability-catalog.json)：46项订单语义能力，不是46种独立订单类型；全部原生资格NOT_RUN。
3. [新增验收矩阵](ACCEPTANCE_MATRIX.md)：56项待执行场景，以及每项能力的独立映射。
4. [Codex交接](CODEX_HANDOFF.md)：评审后的实施要求、依赖和禁止的替代修复。
5. [源码发现](architecture-findings.json)、[来源](sources.json)、[文档差异](documentation-discrepancies.json)。

运行`python validate_design.py`只能验证本报告结构、引用、功能覆盖和状态，不能证明产品、模型、数据库或交易所已通过测试。

本次未创建/修改远程仓库，未安装依赖、访问交易账户或发送订单。当前新版代码已有统一PAPER路径，旧版直接模拟成交结论不作为本次新发现。
