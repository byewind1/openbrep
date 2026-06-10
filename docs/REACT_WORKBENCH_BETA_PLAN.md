# React Workbench Beta 计划

> 日期：2026-06-11
> 前置文档：`REACT_WORKBENCH_MAIN_UI_AUDIT.md`（2026-05-30 功能对照审计）、`PRODUCT.md`
> 状态基线：Workbench Session v1 已合入本分支（项目版本戳 / 统一脚本落盘 / 启动恢复）

## Beta 定义

react-workbench 是 OpenBrep 的**新主工作台 Beta**：建筑师不用回到 Streamlit，
就能完成一个真实 GDL 对象的"打开 → 编辑 → 预览 → 参数调整 → 编译 → AI 修改"闭环。

**Streamlit 进入维护模式**：不再美化、不再加功能，只修阻断性 bug。
新功能一律只做在 react-workbench。

## Beta 成功标准与现状

| # | 标准 | 现状 | 缺口 |
|---|---|---|---|
| 1 | 文件选择器打开 HSF、导入 GDL/GSM、保存/另存 HSF | ✅ 基本完成 | 项目路径/保存状态展示不够醒目（P1） |
| 2 | 主舞台按任务切换（预览 ⇄ 编辑器），参数侧边，变更即时更新 3D/2D | ⚠️ 布局已对，质量不够 | 竞态保护已修；缺 debounce、预览来源标识、保真度提示（P2） |
| 3 | Monaco 编辑、保存、刷新预览、dirty 标记 | ✅ 完成 | 脚本树和编辑器已有 ● 标记 |
| 4 | 编译链路：Mock/LP 状态、错误、输出位置、Reveal | ✅ 基本完成 | 诊断展示偏日志堆叠，需按脚本分组+状态徽标收口（P1） |
| 5 | AI 能解释/生成修改/展示变更，且过期结果不污染新项目 | ⚠️ 一半 | epoch 守卫已落地；变更可审查性弱——用户要从聊天文本里猜发生了什么（P3） |
| 6 | 重启后恢复最近项目、编译配置、LLM 配置 | ✅ 完成 | Session v1 已交付 |
| 7 | 测试 + 一次真实手动 smoke 证明可替代 Streamlit 日常路径 | ⚠️ 待做 | readiness gate 存在；Beta 级 smoke 清单见本文末尾 |

## Beta 覆盖范围（从 Streamlit 迁移）

以下旧功能 Beta **必须覆盖**（依据审计表，绝大部分已完成）：

- HSF 打开 / GDL 导入 / GSM 解包导入 / 保存 / 另存
- 脚本编辑（Monaco）、保存、dirty 状态
- 3D / 2D 预览、参数编辑/应用/重置、参数元数据编辑、手动加参数、参数校验
- Mock / LP 编译、输出目录、Reveal 输出
- Revision 保存 / 列表 / 恢复、项目 Git
- AI 创建 / 修改 / 解释、聊天历史持久化、采用历史代码块、图片参考输入（单图）
- 错题本（lessons 查看/编辑/忽略/删除/汇总）、项目记忆状态/清除
- LLM / 编译器 / 自定义 provider 设置
- Tapir 基础面板（已有的保留，Beta 内**不再扩展**）

## Beta 暂不迁移（明确不做）

- Pro license / Pro knowledge package UX
- 高级 vision 工作流（多图、迭代图生成）
- 聊天历史的批量/多消息操作（单消息采用已够用）
- Floating/可停靠预览窗（保持 flag 冻结）
- Tauri/Electron 实际打包（P4 仅做评估文档）
- Archicad/Tapir 深度集成（live 双向同步等）

## 优先级计划

### P0：Beta 边界（本文档）✅

### P1：核心工作流打磨

| 任务 | 验收标准 |
|---|---|
| 项目状态条 ✅ | 顶部常驻显示：项目名、HSF 路径（过长保留末段+悬停全路径）、clean/dirty（脚本+参数，原有）、最近保存时间（脚本保存/参数应用/项目保存/另存均更新） |
| 保存入口统一 ✅ | 核查后判定已满足：Project 菜单含 New/Open/Recent/Import/Save As，禁用态明确；Save 留在工具栏（高频操作，合理设计） |
| 编译诊断收口 ✅ | 分组/计数/点击跳转原已有；本轮补：状态徽标（Passed/Failed/Compiling）+ 失败时渲染 error 字段（原来失败原因只埋日志） |
| work directory ✅ | 修正为真实 gap：AI 新建项目原硬编码落 `./output`，现改为走设置面板的 Output directory（请求显式指定 > 设置 > 兜底） |

### P2：预览可信化（原"预览中心化"，2026-06-11 修正）

> 修正理由：固定预览为主画布与 `PRODUCT.md` 的 "Context-driven workspace" 原则冲突——
> 建筑师调参数时预览是工作台，GDL 开发者改脚本时编辑器是工作台。
> 且 `gdl_previewer` 是简化渲染（GDL 子集），语义正确的最终裁判是编译后的 Archicad，
> 不能把近似预览捧成"唯一真相"。现有 `PreviewWorkspaceStage` 已实现可切换主舞台
> （`previewWorkspaceOpen` 在预览舞台和编辑器舞台间切换），方向正确，保留并打磨。

| 任务 | 验收标准 |
|---|---|
| 参数刷新竞态保护 ✅ | 请求带序号，旧响应到达时丢弃（与 epoch 守卫同思路）；已落地（`6299308`）并有回归测试 |
| 参数刷新 debounce ✅ | 连续变更合并为 ~250ms 一次请求；已用 fake timers 覆盖快速连续输入只触发一次预览请求 |
| 预览来源标识 ✅ | 核查后判定已存在（`bf42bdd`）：footer 按 `verification.source` 显示 Saved / Editor Buffer / Stale |
| 保真度诚实提示 ✅ | 3D/2D footer 常驻 "Approximate preview · verify in Archicad"（悬停有完整说明）；warnings 计数原已可见 |
| 主舞台切换打磨 ✅ | 打开有路径的项目默认进预览舞台；点脚本/点诊断自动切编辑器舞台；双舞台常驻 DOM 用 display 切换，相机视角与编辑器滚动不丢 |
| 脚本改动刷新 ✅ | 右栏原有 Update 按钮；主舞台预览补同款 Update（走 dirty buffer 预览路径） |

### P3：AI 修改进入可审查流程

| 任务 | 验收标准 |
|---|---|
| 变更摘要卡 | AI 生成完成后展示结构化卡片：changed files 列表、一句话摘要，而非纯聊天文本 |
| 一键保存 revision | 摘要卡上直接保存本次 AI 变更为 revision |
| 错误分类展示 | LLM 错误 / 编译错误 / 验证警告分开显示，不混在一段文本里 |

### P4：桌面壳准备（只评估，不实施）

- 产出 `docs/DESKTOP_SHELL_EVALUATION.md`：Tauri vs Electron 最小壳对比
- 关键问题：本地 Python API 启动/端口探测/进程关闭、原生文件对话框迁移路径（Python chooser 作为 fallback）

## 验收：Beta 级手动 Smoke 清单

全链路一次真机操作（验收标准 7）：

1. `./obr7` 启动 → backend 自动恢复上次项目
2. 文件选择器打开一个真实 HSF → 参数/脚本/预览正确加载
3. 拖动参数 → 3D 预览即时更新，无旧帧闪烁
4. Monaco 改 3d.gdl（不保存）→ 预览反映 editor buffer → 保存 → dirty ● 消失
5. Mock 编译 → 诊断按脚本分组；切 LP 模式真实编译 → Reveal 输出 .gsm
6. AI 修改（"加一块层板"）→ 变更摘要 → 保存 revision → 恢复 revision 验证可回滚
7. AI 生成中途切换项目 → 日志出现 discarded，新项目不被污染
8. 杀掉 backend 重启 → 项目/编译配置/LLM 配置恢复

通过后，react-workbench 标记为 Beta，README 增加入口说明，Streamlit 文档标注维护模式。

## 守护规则（沿用审计 + CLAUDE.md）

- `WorkbenchApp.tsx` 只做组装，业务进 store action slices / 后端 services
- 不在 React 重新实现 GDL 解析、预览、编译、HSF 变更
- 每迁移一个工作流补一个针对性测试
- 合并 main 前必须过 `scripts/workbench_readiness_gate.py --full` + 本 smoke 清单（CLAUDE.md 禁止事项已有约束）
