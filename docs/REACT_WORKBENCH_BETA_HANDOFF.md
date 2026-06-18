# React Workbench Beta 交接（P0–P4 已完成）

> 日期：2026-06-11
> 取代 `CODEX_HANDOFF_BETA_P2_P4.md`（其任务已全部完成，仅作历史参考）
> 总纲领：`docs/REACT_WORKBENCH_BETA_PLAN.md`（所有任务行已标 ✅）

## 当前状态一句话

Beta 计划 P0–P4 代码层面全部完成并推送（origin + gitee 的 `react-workbench` 分支），
**唯一剩余的 Beta 门槛是一次真机 smoke**（清单见 Beta 计划末尾，8 步，需人工操作）。

## 工作环境（不变）

- worktree：`.worktrees/react-workbench`，分支 `react-workbench`
- **禁止合并到 main**（CLAUDE.md 禁止事项明文：需过 `scripts/workbench_readiness_gate.py --full` + smoke + 用户明确说"合并到 main"）
- 中文 commit；推送 origin + gitee 双远程
- 验证命令：
  ```bash
  cd frontend && npx vitest run && npx tsc -p tsconfig.app.json --noEmit
  uv run --python 3.11 --with pytest --no-project python3 -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
  ```
- 已知无关失败：`test_cli_configure` doctor 测试（本地 config.toml 含真实 key，预存）

## 本分支关键提交（自 Session v1 起）

| commit | 内容 |
|---|---|
| 3c792d2 | Session v1：session_id/project_epoch、epoch 守卫、flushDirtyScripts、启动恢复 |
| 94b8c07 / 27b3f23 | Beta 计划 + P2 改"预览可信化"的产品修正 |
| 16690eb / 9925226 | P1：状态条（路径+保存时间）、编译徽标+error 渲染、AI 落点走 Output directory |
| 6299308 / 3ce459c | P2：参数预览竞态保护 + debounce 250ms |
| 02c4c41 | P2：保真度提示、双舞台常驻 display 切换（保相机）、主舞台 Update 按钮 |
| 846e2ad | P3：changedFiles 摘要卡、一键 Save revision、错误分类徽标 |
| 15042fc | P4：桌面壳评估，推荐 Tauri 2.x |

## 必须知道的架构事实

1. **epoch 守卫模式**：长异步操作前捕获 `get().projectEpoch`，返回后不一致即丢弃。
   新增任何长操作（>1s 的后端调用且会写项目级 state）都必须套这个模式。
   （`assistantActions.ts` 有三处现成示例）
2. **读当前脚本前先 flush**：预览/编译/AI 路径都必须经 `flushDirtyScripts()` 或
   dirty-buffer-overrides（`previewActions.ts` 的 `dirtyScriptBuffers()`），二选一，不能裸调。
3. **AssistantMessage 的 `changedFiles`/`errorCategory` 不持久化**：
   后端 `assistant_service.py` 的 list/save 只保留 role/content。想让摘要卡跨会话存活，
   要扩展 `ErrorLearningStore` 的 transcript 字段（属于 core 模块，动之前先评估）。
4. **双舞台常驻**：`PreviewWorkspaceStage` 同时渲染预览舞台和编辑器舞台，
   用 `.stage-hidden`（display:none）切换——不要改回条件卸载，会丢相机视角。
5. **store 加字段三件套**：`workbenchStoreTypes.ts` + `workbenchStore.ts` 初始值 +
   （项目切换需重置的）`workbenchStoreUtils.hydrateSnapshot`。

## 下一轮任务（按优先级）

### A. 真机 smoke（人工，唯一 Beta 门槛）
按 `REACT_WORKBENCH_BETA_PLAN.md` 末尾 8 步清单操作 `./obr7`。
agent 可辅助：起服务、看日志、修 smoke 中暴露的问题。

### B. smoke 通过后的 Beta 标记（小任务包）
- README.md / README.zh-CN.md 加 React Workbench Beta 入口说明
- Streamlit 相关文档标注"维护模式"
- `docs/releases/` 按版本策略写发布说明（版本号遵循 0.5.x 规则）

### C. Beta 后方向（用户拍板顺序，未承诺）
1. **Tauri 最小壳**：先做无风险前置——后端 `--port 0` 动态端口 + stdout 报告 +
   `POST /api/shutdown`（~30 行，见 `DESKTOP_SHELL_EVALUATION.md` 实施切口）
2. **摘要卡持久化**：扩展聊天 transcript 存储 changedFiles
3. **后端请求级 epoch 校验**：请求带 epoch，后端不匹配拒绝（关闭并发竞争的最后缺口）
4. P5（旧审计遗留）：paramlist 专用预览面板、聊天历史批量操作
