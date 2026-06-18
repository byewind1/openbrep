# React Workbench 最小收口记录

日期：2026-05-31  
分支：`react-workbench-poc`  
当前提交：`ae5869d`  
状态：最小验证通过，暂停继续扩功能。

## 本轮收口范围

本轮没有继续迁移新的 Streamlit 功能，也没有修改 Python backend、React 前端或测试逻辑。目标是把当前长任务停在可恢复、可验收的状态：

- 确认分支状态。
- 跑最小相关验证。
- 记录当前成果和下一步建议。
- 用 checkpoint commit 固化收口记录。

## 当前已形成的 React Workbench 能力

- React Workbench 已经是主 UI 方向，Streamlit 保留为 fallback。
- 前端已拆出 `frontend/src/workbench/WorkbenchApp.tsx` 作为工作台组合入口。
- 已有脚本树、Monaco 编辑器、参数面板、预览面板、底部 Diagnostics、设置抽屉等基础工作台结构。
- `frontend/src/state/actions/*` 已承接多类 store action，避免继续把逻辑堆到单个入口文件。
- 设置抽屉已包含项目记忆/错题本相关 UI 文件：`frontend/src/workbench/settings/MemoryLessonsPanel.tsx`。
- 最近提交已经包含 React 合并就绪评估：`docs(workbench): assess react merge readiness`。

## 最小验证结果

在 `.worktrees/react-workbench-poc` 执行：

```bash
python -m pytest tests/test_workbench_api.py -q
```

结果：

```text
55 passed in 0.22s
```

在 `frontend/` 执行：

```bash
npm run test -- --run
```

结果：

```text
Test Files  5 passed (5)
Tests       63 passed (63)
```

在 `frontend/` 执行：

```bash
npm run build
```

结果：

```text
vite v7.3.3 building client environment for production...
634 modules transformed.
dist/index.html                     0.41 kB
dist/assets/index-B1r2qF3P.css     22.36 kB
dist/assets/index-DqwlouZN.js   1,169.78 kB
built in 2.46s
```

备注：build 有 Vite chunk 大小提示，属于当前 Monaco/Three/React 组合下的性能优化提醒，不是失败。

## 当前风险

- 本轮只跑了 workbench 相关最小验证，没有跑全量 `python -m pytest tests/ -q`。
- 没有启动浏览器做人工 UI smoke；视觉和交互仍需要人工打开 `obr7` 或 dev server 复核。
- 前端 bundle 体积已经超过 Vite 默认提示阈值，后续应考虑 Monaco/Three 相关 code splitting。
- React Workbench 仍处于 POC 到主 UI 的过渡期，下一轮应继续做功能迁移，但每轮都要保持组件和 action 分层。

## 下一步建议

优先顺序：

1. 继续迁移 Streamlit 中最高频的项目打开/最近项目/工作区选择能力，确保 React 可以稳定打开真实 HSF 项目。
2. 完善设置抽屉中的项目记忆/错题本闭环，包括刷新、总结、编辑、忽略/恢复的最小可用链路。
3. 做一次浏览器人工 smoke，确认脚本编辑、保存、mock compile、预览窗口、设置抽屉和底部 Diagnostics 在 UI 上都可用。
4. 等 React Workbench 达到主 UI 最小可用后，再考虑和 `main` 的合并策略。

## 恢复工作入口

```bash
cd /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/.worktrees/react-workbench-poc
git status --short --branch
python -m pytest tests/test_workbench_api.py -q
cd frontend
npm run test -- --run
npm run build
```
