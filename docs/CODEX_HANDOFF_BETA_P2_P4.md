# Codex 交接：React Workbench Beta 剩余任务（P2/P3/P4）

> 写给执行 agent 的完整上下文。按任务顺序逐个实现，每个任务独立 commit。
> 总纲领见 `docs/REACT_WORKBENCH_BETA_PLAN.md`，本文是它的可执行版。

## 0. 工作环境

- 工作目录：git worktree `.worktrees/react-workbench`，分支 `react-workbench`
- **禁止合并到 main**（CLAUDE.md 禁止事项有明文，违反过一次被 revert）
- 提交信息用中文；推送到 `origin` 和 `gitee` 两个远程的 `react-workbench` 分支
- 前端在 `frontend/`（React + Zustand vanilla store + vitest），后端是 `openbrep/workbench_api.py` + `openbrep/workbench/*.py` services（无框架的本地 HTTP API）

### 验证命令（每个任务完成后必须全过）

```bash
# 前端
cd frontend && npx vitest run && npx tsc -p tsconfig.app.json --noEmit

# 后端（本机 python3 没有 pytest，用 uv）
uv run --python 3.11 --with pytest --no-project python3 -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
```

已知无关失败：`tests/test_cli_configure.py::test_doctor_reports_missing_key_as_failure`（本地 config.toml 含真实 key 导致，改动前就挂，忽略）。

## 1. 项目背景（30 秒版）

OpenBrep 是 AI 辅助的 GDL（Archicad 对象语言）工作台。React Workbench 正从 POC 转正为
替代旧 Streamlit UI 的主工作台 Beta。用户是建筑师和 GDL 开发者，闭环是：
打开 HSF 源 → 编辑脚本/调参数 → 3D/2D 预览 → 编译 → AI 修改 → 保存 revision。

设计基调（`PRODUCT.md`）：专业工作台（VS Code/Blender 感），不是聊天壳，不是表单堆。
主舞台按任务切换：预览舞台 ⇄ 编辑器舞台（`PreviewWorkspaceStage.tsx` 已实现切换）。

## 2. 已完成基线（不要重做）

| 能力 | 位置 | commit |
|---|---|---|
| 项目版本戳：`session_id` + `project_epoch`，换项目自动 +1，snapshot 携带 | `openbrep/workbench_api.py` WorkbenchSession | 3c792d2 |
| 前端 epoch 守卫：AI 生成/问答/创建中途切项目 → 丢弃过期结果 | `frontend/src/state/actions/assistantActions.ts` | 3c792d2 |
| 统一落盘入口 `flushDirtyScripts()`：编译和 AI 生成前强制先保存编辑器手改 | `frontend/src/state/actions/scriptActions.ts` | 3c792d2 |
| backend 启动恢复上次项目 `restore_last_project()` | `openbrep/workbench_api.py` | 3c792d2 |
| 参数预览竞态保护：请求带序号，乱序旧响应丢弃 | `frontend/src/state/actions/parameterActions.ts` `setDraftParameter` | 6299308 |
| 项目状态条：顶栏 HSF 路径 + `lastSavedAt`（Saved HH:MM 丸） | `TopMenu.tsx` / store `lastSavedAt` | 16690eb |
| 编译状态徽标（Passed/Failed/Compiling…）+ 失败 error 渲染 | `frontend/src/components/BottomDrawer.tsx` | 9925226 |
| AI 新建项目落点走设置面板 Output directory | `openbrep/workbench/project_session_service.py` | 9925226 |

## 3. 关键架构模式（写代码前必读）

- **store 结构**：`frontend/src/state/workbenchStore.ts` 组装 `actions/*.ts` 各领域 slice；
  类型在 `workbenchStoreTypes.ts`；纯函数在 `workbenchStoreUtils.ts`。
  新 state 字段要同时改：types + store 初始值 + （如需项目切换时重置）`hydrateSnapshot`。
- **测试模式**：`workbenchStore.test.ts` 用 `makeApi(overrides)` 构造 fake api；
  组件测试用 @testing-library/react（参考 `TopMenu.test.tsx`、`BottomDrawer.test.tsx`）。
- **预览数据流**：`previewActions.ts` 的 `loadPreview3D/2D` 把 dirty 编辑器缓冲区作为
  overrides 传给后端（`dirtyScriptBuffers()`），所以预览可以反映未保存内容——
  这正是"来源标识"任务的依据。
- **架构红线**（CLAUDE.md）：`WorkbenchApp.tsx` 只做组装；业务进 store action 或后端 service；
  不在 React 重新实现 GDL 解析/预览/编译；每个工作流补针对性测试。

## 4. 任务清单（按顺序做）

### P2-1 参数刷新 debounce

- **现状**：`setDraftParameter`（`parameterActions.ts`）每次变更立刻发预览请求，已有序号守卫
  （`draftPreviewSeq`），但拖动滑块时仍然每个中间值都打一次后端。
- **要求**：连续变更合并为 ~250ms 一次请求；序号守卫保留。
- **测试**：vitest `vi.useFakeTimers()`；断言快速连续 3 次 `setDraftParameter` 只发 1 次
  `fetchPreview`，且 `draftParameters` 立即更新（输入不能有延迟感，只有请求延迟）。
- **注意**：现有测试 `setDraftParameter ignores out-of-order preview responses` 和其他调用
  `setDraftParameter` 的测试需要适配（可能要 `vi.advanceTimersByTime`），不许删测试。

### P2-2 预览来源标识

- **要求**：预览视口角落显示来源徽标——dirty 缓冲区参与渲染时显示
  `editor buffer (unsaved)`，否则 `saved source`。
- **现状**：`PreviewViewport.tsx` 已接收 `hasDirtyScripts` prop（`PreviewWorkspaceStage` 在传），
  大概率只差 UI 渲染和样式。先读组件确认。
- **测试**：组件测试两种状态各一条。

### P2-3 保真度诚实提示

- **要求**：预览角落常驻轻提示文案 `近似预览 · 以编译后 Archicad 为准`（muted 小字，不挡视图）；
  预览 warnings（不支持的 GDL 语句）保证可见——确认现有 warnings 展示路径没有被折叠掉。
- **位置**：`PreviewViewport.tsx`；样式参考 `styles.css` 里 `.viewport-footer` / muted 文案模式。

### P2-4 主舞台切换打磨

- **要求**：① 打开/加载项目后默认进入预览舞台；② 在脚本树点开脚本自动切到编辑器舞台；
  ③ 舞台来回切换不丢 3D 相机视角。
- **现状**：`WorkbenchApp.tsx` 本地 state `previewWorkspaceOpen` 控制；相机逻辑在
  `frontend/src/components/previewCamera.ts`（有测试 `previewCamera.test.ts`）。
  先确认相机是否已在组件卸载时丢失——若 viewport 不卸载则 ③ 可能已满足，验证后再动。
- **测试**：相机持久化若需改动，扩展 `previewCamera.test.ts`。

### P2-5 脚本改动一键刷新预览

- **要求**：编辑器舞台下有显式"刷新预览"入口（按钮即可），点击调 `loadPreview3D()`
  （dirty buffer 已自动参与，见第 3 节）；按钮在无项目时禁用。
- **位置**：`PreviewWorkspaceStage.tsx` 编辑器分支或 `WorkbenchRightRail.tsx`，选更顺手的一处，
  不要两处都加。

### P3-1 AI 变更摘要卡

- **要求**：`generateAssistantChanges` 成功后，聊天里的回复附结构化摘要区：
  changed files 列表（可点击 → `openScript(name)` 跳到对应脚本）+ 一句话摘要。
  不再让用户从纯文本里猜。
- **现状**：`assistantActions.ts` 已把 `changed_files` 拼进回复文本（`suffix` 变量），
  改为结构化数据存进 message（扩展 `AssistantMessage` 类型加可选 `changedFiles?: string[]`），
  渲染在 `AssistantPanel.tsx`。
- **注意**：`AssistantMessage` 会持久化到后端（`saveAssistantHistory`），新字段要向后兼容
  （旧历史没有该字段不能崩）。

### P3-2 摘要卡一键保存 revision

- **要求**：摘要卡上加 `Save revision` 按钮，调用已有 `saveRevision(message)`
  （`revisionActions.ts`），message 自动带用户的修改指令。

### P3-3 错误分类展示

- **要求**：AI 流程失败时分类显示：LLM 配置/认证错误、编译错误、验证警告，三类视觉可区分。
- **现状**：`workbenchStoreUtils.ts` 已有 `isLlmConfigurationError()` 和
  `formatAssistantRequestError()`，在此基础上扩展，不要新造一套。

### P4 桌面壳评估（只写文档，不写代码）

- 产出 `docs/DESKTOP_SHELL_EVALUATION.md`：Tauri vs Electron 最小壳对比。
- 必答：本地 Python API 的启动/端口探测/进程关闭方案；原生文件对话框迁移路径
  （现有 Python chooser `ui/local_file_dialog.py` 作为 fallback）；打包体积与签名成本。
- 给出明确推荐，不要各打五十大板。

## 5. 完成定义（每个任务）

1. 验证命令全过（第 0 节）
2. 新行为有针对性测试
3. 独立 commit（中文信息），推送 origin + gitee
4. `docs/REACT_WORKBENCH_BETA_PLAN.md` 对应任务行标记 ✅ 并附一句实现说明
