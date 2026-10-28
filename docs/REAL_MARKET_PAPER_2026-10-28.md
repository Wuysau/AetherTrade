# 主分支真实行情 PAPER 自测

用户已明确选择“连接真实行情，使用 PAPER 虚拟资金自测”。

## 持续测试运行中（13:16）

用户要求持续运行，暂停时间由用户决定。BTC每300秒使用DeepSeek分析，策略预算
20USDT；页面最新保存的自动PAPER委托开关为开启。周期分析已执行两轮，均HOLD。
派发的资金重新认证与账户锁竞争问题已修复，独立数据库33项回归通过；真实行情
复测PENDING→SUBMITTED→显式EXPIRED通过，幂等一致、无成交，余额100000。
前述失败与原ID未发送终止证据均保留，不能把失败历史改记为成功。

运行状态每30秒更新：[当前状态](../.runtime/live-market-paper/continuous-status.json)。
变化历史：`.runtime/live-market-paper/continuous-events.jsonl`。
这是本机持续进程，没有设置结束时间；电脑休眠或进程退出会中断运行。
自动策略仍受行情、授权及账户风控约束；模拟成交/保护确认需要显式合成事件，
不会将真实价格变化自动当作成交证明。本轮成功复测使用零成交到期事件。
详见[启动状态](evidence/2026-10-28-continuous-start.json)与
[实际复测](evidence/2026-10-28-continuous-probe-result.json)。

## 13:03 续测：真实模型通过，PAPER 派发未通过

用户配置 DeepSeek 后授权继续测试。`deepseek-flash` / Responses 的真实 BTC 分析
HTTP200，耗时5.6秒，输入210/输出657 tokens，返回HOLD；幂等重放一致。目录测试
也通过。此记录更新下方早期“没有运行付费模型”的历史边界，本轮确实调用一次云模型。

独立约20 USDT人工虚拟委托两次被行情检查拒绝，后续接单成功但停留在
DURABLE_READY/PENDING。owner持续提示需审查；后续诊断发现请求保存行情已过期，
被发送前账户风控拒绝，初次未提交原因仍待查。没有注入到期/成交事件，也没有强制
清理该命令。现金100000、持仓空、成交0，仍有待处理命令，不能宣称本轮完整通过。
API/行情和诊断后恢复的owner均运行，无启用策略。原始证据与命令ID见
[续测证据](evidence/2026-10-28-deepseek-paper-selftest.json)。

## 原始启动与首次自测记录

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
