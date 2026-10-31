# OP-A 审查结果 — 2026-10-28

本次完成目录/代码/依赖审查及可执行审计工具，不是订单功能实现完成。

- 主目录public-snapshot与基线b168966d350ed60fe8a92792f9519f4227054bc5一致，
  tracked diff为空；未跟踪evidence/worktrees-archive/原样保留。
- 实施worktree：TradingHandoff/AutoDispatchWorktrees/binance-orders，
  分支codex/binance-order-capabilities；无适用AGENTS.md发现。
- 交接目录11文件已阅读，原始字节副本及hash保存在qualification/binance_orders/reference
  与docs/evidence/binance-orders/op-a/checkout-audit.json。
- 当前四份公共wire Schema与src打包副本hash一致，仍为2026-10-20；没有修改。

## 代码差异与复用边界

报告A01–A11仍成立。paper_runtime.py:400拒绝非MARKET，:559/:580继续硬编码
MARKET，所以不能删guard。analysis_evidence只有能力布尔值。protection manager
缺触发源表达，当前PAPER账户/规则是SPOT/USDT合成模型。PaperEngine手动事件
注入端要保留；新市场撮合只产生明确标记且可重放的事件。

已存在accounts/capabilities.py的四方交集检查和engine/nautilus/binance_products.py
离线产品规则；后续扩展这两处，不创建第二套授权/资金/账户系统。storage.models
对Intent/Command直接调用旧ContractCodec，迁移必须先做按版本双读；只改Schema
会导致旧恢复或新记录解码失败。现有owner、预留、UNKNOWN与LockedActionVerifier保留。

## 官方接口复核

全部46项目标保留，不以当前MARKET-only能力缩减范围。逐项完整矩阵在审计JSON。
该矩阵的official_claim源于交接；本轮来源复核记录见official-verification.json，
文档支持不等于账户/标的允许或执行准入。

- DOC-01确认：USD-M五类条件单走/fapi/v1/algoOrder，普通/batch禁止。
- DOC-02确认：实际algo callbackRate为0.1–20；Nautilus latest仍写到10；
  同一Trade汇总页测试接口还存在旧max5，必须按端点定位。algo字段为
  triggerPrice/activatePrice，不能混用测试接口stopPrice/activationPrice。
- DOC-03确认：真实股票提交只有MARKET/LIMIT，用户流更宽枚举不能作为提交许可。
  BUY MARKET用notional；SELL MARKET用quantity；fractional GTC须EXTENDED/24H。
- DOC-04未解决：quoteNotionalLimit FAQ正文仍获取失败，不能猜测新请求字段。
- Spot peg、OPO全成/净到账/锁币、SOR、四种TWAP/VP及执行price-range语义保留。
- 官方最新安装说明明确2.x文档与稳定1.x API不兼容；本地Python3.14.2，
  nautilus_trader distribution不存在，lock只有review_ref，部署/wheel/container未解析。
  尝试review_ref的根pyproject.toml URL返回404；只证明该路径未取得，不证明ref无效。

## 实施闸门结论

发现真实命令/permit边界冲突，详见[ADR-ORDER-001](ADR-ORDER-001.md)：
旧清单要求native算法子单独立本地命令，新设计要求平台子单仅摄取。
按用户“重大冲突先报告”的要求，OP-B写契约及OP-C–G不在此轮擅自展开。
建议采用ADR中的“每次本地外部写都durable；平台生成子单仅关联摄取”规则。
Native精确构建仍BLOCKED；这不会删除任何最终能力目标。

## 当前真实验收状态

- OP-A工具测试6项通过；连同既有wire语义、身份能力与离线Nautilus边界回归共406 passed。
  原包24项结构检查通过；这些不是ORD交易场景通过。
- 56项ORD均NOT_RUN；ORD-07的native分支受未锁定build阻断，ORD-53的DOC04
  子项文档未解决。它们不能据此把整个场景标PASS。原始设计状态保留。
- 本轮没有数据库迁移、撮合、平台请求、账户查询、付费模型调用或运行服务变更。
- OP-B具体契约、数据库扩展、双读、新写切换与恢复/回滚边界在ADR中已列明。


实际命令（解释器为主目录既有 .venv/Scripts/python.exe）：

```powershell
python tools/audit_binance_orders.py --output docs/evidence/binance-orders/op-a/checkout-audit.json
python qualification/binance_orders/reference/validate_design.py
python -m pytest tests/qualification/test_binance_order_audit.py tests/contracts/test_wire_semantics.py tests/unit/test_identity_and_capabilities.py tests/qualification/test_nautilus_bridge.py -q --tb=short
```

均exit0，审计命令exit0仅表示报告成功生成，绝不是原生交易准入。
官方引用及端点差异来源在 `official-verification.json`。Nautilus代际差异另见
[官方安装说明](https://nautilustrader.io/docs/latest/getting_started/installation/)。
