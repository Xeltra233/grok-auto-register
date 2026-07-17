# Plan: 删除跨进程误杀的僵尸监控 + 确定性浏览器关闭 + goal 文档

## 背景
goal-1 已完成分支版（Web 面板 + 内嵌 GoProxy + 账号池自动注册等）。
本轮是 goal-1 后的缺陷修复与策略纠正，不是重做整个分支。

## 用户本轮核心诉求（按优先级）
1. 验证码页闪退、被判注册失败：根因是 `panel/browser_monitor.py` 跨进程误杀活跃 Chromium。
2. 删除僵尸监控：不要继续维护“僵尸浏览器”前端/后端监控方案。
3. 从根源修浏览器残留：注册结束仍有浏览器挂着，应在关闭链路做确定性 PID 树清理。
4. 两个注册流程都修：GUI 与 CLI 共用关闭函数，一处修复覆盖两者。
5. 通览查 bug：相关路径通查，避免残留引用与竞态。
6. 写 goal 文档：按 goal-mode 建 `goal-2/`。
7. 截图控制体积：先看文件大小，再决定是否读图；优先小图/压缩图。

## 已有工作证据（当前 worktree）
- `panel/browser_monitor.py` 已删除。
- 代码侧已无 `browser_monitor` / `/api/browsers` / 僵尸 UI 引用（除 README 测试命令名与 goal-1 历史文档）。
- `grok_register_ttk.py` / `cpa_xai/browser_confirm.py` 已实现 quit 前 PID 树快照 + 强杀残留。
- `stop_browser` 已避免在 quit 前清空 root PID 兜底。
- `panel/pool_autoreg.py` 已修 refresh 结果被外层覆盖的竞态。
- 相关单测 23 项已通过：
  - `tests.test_browser_lifecycle`
  - `tests.test_browser_monitor_and_logs`
  - `tests.test_run_branch`
  - `tests.test_panel_server`
  - `tests.test_pool_autoreg`

## 默认假设
1. 范围仅本分支 `feature/web-panel-goproxy`，不改 main 独立分支仓库。
2. 用户说的“两个分支的 bug”在本上下文中按“两个注册流程（GUI+CLI）”处理（用户此前确认）。
3. 删除监控后，不再做定时杀历史残留；依赖本次关闭确定性清理。
4. 历史截图若过大，只读小体积 review 图；必要时重新压缩后再读。
5. 不擅自提交生产密钥/认证配置；提交仅限本次相关代码与文档。

## 实施顺序
1. 初始化 goal-2 文档（本轮）。
2. 静态/单元验证：确认监控删除完整、关闭链路正确、测试全绿。
3. 前端验收：确认无僵尸 UI；如需截图，先查体积再读小图。
4. 端到端冒烟：panel status/overview + 关闭链路单测；真实注册若环境允许再补。
5. 提交相关代码变更。
6. 最终 review 与收尾。

## 验证方式
- 代码搜索：无 `browser_monitor` 运行时依赖。
- 单测：浏览器生命周期 / 面板 / pool_autoreg 相关全绿。
- 面板：overview/index 无僵尸页与清理按钮。
- 关闭链路：stop 时对 sticky browser 走 PID 快照强杀。
- 截图：仅在需要视觉确认时读取小体积图片。

## 回滚
- 删除监控与关闭链路改动可按 git diff / commit 回退。
- goal-2 文档独立，不影响运行时。
