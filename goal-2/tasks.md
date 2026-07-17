# Tasks

## Task 1: 初始化 goal-2
- [x] 已完成

## Task 2: 核验监控删除与关闭链路
- [x] 已完成

## Task 3: 前端无僵尸 UI
- [x] 已完成

### 大型全面检查/debug循环 1
- [x] 已完成
- 范围：Task 1-3。
- 结果：
  - 需求对齐：删除僵尸监控 / 确定性关闭 / GUI+CLI 共享 / goal文档 / 截图体积控制 = PASS。
  - 代码：browser_monitor.py GONE; runtime files clean of monitor/zombie APIs/UI。
  - 关闭链路：quit-before PID tree snapshot + force-kill; stop keeps root pid until quit; mint close_standalone same。
  - 测试：23 related unittest passed。
  - 语法：key py files ast.parse OK (historical \s SyntaxWarning remains in grok_register_ttk embedded JS)。
  - UI：live panel + small jpg visual read: 5 tabs only, no zombie UI。
  - 安全：不再做 cross-process PID whitelist kill；no orphan monitor loop。
  - 数据：pool_autoreg refresh race fixed (write last_trigger before thread start)。
  - 文档：goal-2 synced; goal-1 historical zombie PASS is outdated and not runtime。
  - 回滚：git branch feature/web-panel-goproxy diffs reversible。
- 风险：
  - real full registration e2e not re-run in this loop
  - README still names tests.test_browser_monitor_and_logs (module kept for log cleanup tests only)
  - worktree has many unrelated dirty/untracked files; Task 4 must selective commit
- 下一步：Task 4 selective commit。

## Task 4: 提交本轮修复
- [ ] 未完成

## Task 5: final review + goal close
- [ ] 未完成

