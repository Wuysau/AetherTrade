# 主分支真实行情 PAPER 自测

用户已明确选择“连接真实行情，使用 PAPER 虚拟资金自测”。

- `codex/unified-paper-execution` 已快进合并到本地 `public-snapshot`，代码为 `d2ec4f7`。
- `paper-execution` worktree 已移除；其他 worktree 与分支保留。
- 移除前完整保留源文件、配置与原始证据（排除可重建依赖/缓存），1116份文件逐项
  SHA256校验通过。私有归档：`TradingHandoff/WorktreeArchives/paper-execution-2026-10-28.zip`，
  配套manifest同目录。归档包含私有配置，不作为公开测试附件。
- 当前运行目录：`D:/Projects/Python/AetherTrade/AetherTrade`。
- 网页：`http://127.0.0.1:8787`；实际独立 owner 同时运行。
- 新的三库虚拟账户配置：`.runtime/live-market-paper/profile.json`。
  独立审核配置：同目录 `profile.resolver.json`。未覆盖旧账户/模型配置。
- 登录名 `paper-selftest`；随机密码在本地忽略文件 `.runtime/live-market-paper/login.json`。

## 已执行检查

合并后前端构建通过，启动/打包/CI门禁相关24项回归通过。实际HTTP自测确认：
Binance官方行情、WebSocket LIVE、BTC新鲜报价；按约20USDT名义金额提交虚拟委托，
PENDING回执幂等重放一致，独立owner提交到模拟平台后为SUBMITTED。显式注入
EXPIRED/累计成交0，最终现金100000、持仓空、活动风险锁空。没有自动伪造成交。
首次提交曾返回409，原始子原因未保存，标为NOT_RECORDED；后续完整自测通过，
不能将第一次拒绝改记为成功。结果详见 `evidence/2026-10-28-real-market-paper.json`。

API/owner进程信息保存在 `.runtime/live-market-paper/processes.json`。本次使用的是
真实公开行情和虚拟执行，未发送真实交易所订单，也没有运行付费模型。没有push，
远端CI/Testnet/真实资金准入仍沿用上一交付报告的NOT_RUN边界。

## 从主目录重新启动

两个终端分别运行（当前已启动，不要重复占用8787）：

```powershell
.venv/Scripts/python.exe -m trading_system.server.cli --paper-config .runtime/live-market-paper/profile.json serve --port 8787
.venv/Scripts/python.exe -m trading_system.server.cli --paper-config .runtime/live-market-paper/profile.json paper-run
```

沿用同一profile可恢复同一虚拟账本；不要重新provision或使用旧控制台启动脚本替代
统一profile。PAPER成交、撤单确认与保护验证仍由显式合成事件驱动，行情变化本身
不会被当作成交证明。
