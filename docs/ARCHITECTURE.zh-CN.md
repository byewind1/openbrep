# OpenBrep 架构说明

日期：2026-10-02  
状态：面向维护者与 AI 编程工具的当前有效架构指南  
英文版：[ARCHITECTURE.md](ARCHITECTURE.md)

OpenBrep 是面向 Archicad 高阶用户和 GDL 开发者的 AI 辅助 GDL 工作台。核心产品承诺：

```text
自然语言或导入库对象
→ 可编辑 HSF 项目
→ AI 辅助生成、修改、调试、解释
→ 编译验证 GSM 输出
→ 项目与资产可追溯
```

这份文档定义当前架构、归属边界和开发规则。人类维护者和 AI 开发工具都应优先阅读。

关键架构决策记录（中文）：

- [ADR 0001: HSF 项目目录是 OpenBrep 的源格式](adr/0001-hsf-as-source.zh-CN.md)
- [ADR 0002: AI 生成写入由 generation service 边界承接](adr/0002-generation-service-boundary.zh-CN.md)
- [ADR 0003: 自定义 Skill 是用户经验的可追溯输入](adr/0003-custom-skill-workflow.zh-CN.md)
- [ADR 0004: React 工作台是唯一 shell，Streamlit UI 退役](adr/0004-react-workbench-only-shell.zh-CN.md)
- [ADR 0005: benchmark 黄金语料密封化与"prompt 变化即重录"判据](adr/0005-benchmark-golden-corpus-replay.zh-CN.md)
- [ADR 0006: Codex 双入口（local / managed）](adr/0006-codex-dual-entry.zh-CN.md)

功能基本定型后的长期治理路径见：[OpenBrep 顶级架构优化路径](ARCHITECTURE_TOP_LEVEL_PATH.zh-CN.md)。

## 当前状态

已退役的 Streamlit `ui/` 包在仓库中已不存在。产品 shell 现在是 React 工作台
（`frontend/`），通过本地 Python API（`openbrep/workbench_api.py`）通信，并打包为
Tauri v2 桌面应用（`src-tauri/`）。`obr` CLI（`cli/main.py`）与 React 工作台共享同一
domain core 和生成 pipeline。

基线：

```text
python tests: 3098 passed, 87 subtests passed
frontend: 780 passed (vitest) + tsc clean
```

`openbrep/workbench_api.py`（`WorkbenchSession`）是组合根。真实行为放在
`openbrep/workbench/` 下可测试的 service 模块里。

## 分层模型

新增或迁移代码时，按以下 seam 模型判断归属：

```text
React workbench UI（页面、面板、store、actions）
  frontend/src/workbench/*
  frontend/src/components/*
  frontend/src/state/*

本地 API 会话（组合根）+ 后端服务
  openbrep/workbench_api.py
  openbrep/workbench/*_service.py

Tapir/Archicad 工作流
  openbrep/tapir_bridge.py
  openbrep/tapir_controller.py
  openbrep/workbench/tapir_service.py
  openbrep/workbench_tapir.py

AI 生成工作流
  openbrep/runtime/pipeline.py

Blender 脚本 → GDL 导入器（BS2G）
  openbrep/importers/blender_script/*
  primitive 模式：parser.py / mapper.py / generator.py
  mesh 模式：mesh_capture.py / loft_detect.py / loft_gdl.py / mesh_gdl.py

CLI（obr）
  cli/main.py

Domain 逻辑
  openbrep/*
```

规则：

- `WorkbenchSession` 保持薄适配层；真实行为放进可测试的
  `openbrep/workbench/*_service.py` 模块。
- 不要让一个 service 混入不相关 seam。当模块开始同时承载聊天路由、源修改、
  preview、knowledge 和 adapter 行为时，拆分它。
- React 工作台是唯一 UI 面。绝不重新引入已退役的 Streamlit `ui/` 包。
- 优先选择小而深的接口，而不是大量 pass-through helper。

## 运行时流程

```text
用户输入
  ├─ 自然语言
  ├─ 图片
  ├─ .gdl / .txt 导入
  ├─ .gsm 导入
  ├─ Blender .py 脚本导入
  └─ HSF 目录加载

React 工作台 UI
  ├─ frontend/src/workbench/WorkbenchApp.tsx   (组合根)
  ├─ frontend/src/state/*                       (store + actions)
  └─ frontend/src/components/*                  (面板)

本地 API 边界（HTTP/JSON，ThreadingHTTPServer）
  ├─ openbrep/workbench/http_server.py          (transport)
  └─ openbrep/workbench_api.py
     WorkbenchSession — 组合根，session 级变更锁

Service 层
  ├─ openbrep/workbench/project_service.py
  ├─ openbrep/workbench/compiler_service.py
  ├─ openbrep/workbench/assistant_service.py
  ├─ openbrep/workbench/preview_service.py
  ├─ openbrep/workbench/tapir_service.py
  ├─ openbrep/workbench/memory_service.py
  └─ openbrep/workbench/settings_service.py

Domain 核心
  ├─ openbrep/hsf_project.py
  ├─ openbrep/runtime/pipeline.py   (TaskPipeline)
  ├─ openbrep/runtime/router.py     (IntentRouter)
  ├─ openbrep/compiler.py
  ├─ openbrep/verification.py
  ├─ openbrep/gdl_parser.py
  ├─ openbrep/paramlist_builder.py
  ├─ openbrep/validator.py
  └─ openbrep/knowledge.py

输出
  ├─ 可编辑 HSF 项目目录
  ├─ revision 元数据
  └─ workspace/output/ 下的编译 GSM
```

## 源格式原则

OpenBrep 将 HSF 项目目录视为可编辑源格式。

```text
workspace/
  Bookshelf/
    libpartdata.xml
    paramlist.xml
    ancestry.xml
    calledmacros.xml
    libpartdocs.xml
    scripts/
      1d.gdl
      2d.gdl
      3d.gdl
      vl.gdl
      ui.gdl
      pr.gdl
    .openbrep/
      knowledge/
        project.toml
        01_context.md
      memory/
        decisions.md
        learnings/
      revisions/
```

规则：

- `.gsm` 是编译交付物，不是源格式。
- 单个 `.gdl` 文件不足以表示完整库对象。
- `paramlist.xml` 与 `scripts/*.gdl` 必须作为同一个源单元处理。
- 编译不能创建新的 HSF 源目录。
- 导入 `.gsm` 可以创建新的稳定 HSF 项目目录。
- 修改对象时直接更新当前 HSF 项目目录，通过 revision 元数据保留可追溯性。
- 项目级 `.openbrep/knowledge/` 是比全局知识优先的上下文来源；修改摘要与错误
  教训持久化在 `.openbrep/memory/` 供后续 session 读回。

相关文档：[project_layout.md](project_layout.md)

## 关键模块职责

### `frontend/src/workbench/WorkbenchApp.tsx`

角色：React 工作台组合根。

允许职责：

- 组装布局（`ResizableWorkspaceGrid`、左右 rail）、顶部菜单和底部抽屉。
- 将 Zustand store（`useWorkbenchStore`）接入面板组件。
- 懒加载重型面板（设置、revision 历史）。

不要新增：

- 工作流编排。当某个工作流需要本地状态、校验或多个控件时，在
  `frontend/src/workbench/<feature>/` 下抽一个 feature 模块。
- 在 React 模块里写 Python/GDL 解释逻辑。

### `openbrep/workbench_api.py`

角色：本地 API 组合根（`WorkbenchSession`）与路由分发。

拥有：

- 单个 `WorkbenchSession` 实例，持有当前项目状态（`HSFProject`）、配置、
  compiler mode 和 service 实例。
- `session_id`（每个后端进程一个）和 `project_epoch`（每次切换项目时递增；
  前端据此丢弃过期的异步结果）。
- 通过 session 级 RLock 串行化变更请求，避免慢速 AI 生成与快速编译/保存在同一
  项目上交错。
- 到 service 层的路由分发。

保持该模块为薄适配层。真实行为在 `workbench/*_service.py`。

### `openbrep/workbench/http_server.py`

角色：ThreadingHTTPServer transport。

拥有 HTTP 请求/响应管道与静态文件服务（Tauri 单端口模式）。路由分发在
`workbench_api.py`。

### `openbrep/workbench/request_gate.py`

角色：HTTP transport 的请求串行化策略。

- 变更路由经 session 级锁串行化。
- 只读路由与原生对话框路由保持无锁。
- 新路由默认加锁（安全方向）。

### `openbrep/workbench/*_service.py`

角色：本地 API 背后的应用级业务工作流。

示例：

- `project_service.py` — 打开/导入/加载/保存 HSF 项目与快照。
- `compiler_service.py` — 编译当前项目为版本化 `.gsm`。
- `assistant_service.py` — AI 生成/修改/解释/修复分发。
- `preview_service.py` / `three_preview.py` — 2D/3D preview payload。
- `tapir_service.py` — Tapir/Archicad 参数读写。
- `memory_service.py` — workspace 级学习记忆。
- `settings_service.py` — 运行时/compiler/LLM 设置（draft 状态）。
- `revision_service.py`、`project_parameter_service.py`、
  `project_script_service.py`、`project_session_service.py`、
  `blender_import_service.py`、`git_service.py`。

规则：

- 真实行为放在可测试模块里，不放 `workbench_api.py`。
- 不要在一个 service 模块里混入不相关 seam。

### Unified conversation / 统一对话接缝

`workbench/conversation_service.py` orchestrates read-only prepare and token-bound execute; `source_snapshot.py` binds editor drafts and source/context versions. `runtime/turn_policy.py` guards permission before every mutation engine. `runtime/advisor.py` and `runtime/inspection.py` produce read-only advice and bounded evidence; `workbench/working_intent.py` retains scoped user constraints and evidence-driven task state. `workbench/workspace_session_service.py` owns workspace attachment and persistence. Default CLI/benchmark prompts do not receive GUI conversation context. See [conversation contracts and evaluation](ASSISTANT_CONVERSATION.md).

### `openbrep/runtime/pipeline.py`

角色：LLM 任务执行的 domain pipeline（`TaskPipeline`）。

拥有：

- Intent 分发（经 `IntentRouter`）、任务执行、tracing。
- Chat、GDL create、modify、debug、repair handler。
- LLM modify 路径之前的确定性 micro-modify 拦截。
- 验证集成：编译成功后，CREATE 与 MODIFY/DEBUG/REPAIR 运行语义验证和有界
  修复轮。

pipeline 保持独立于 UI 与 HTTP 层。它可以接收 `on_event`、`should_cancel` 等
callback，但不得依赖 session state 或 HTTP transport。

### `openbrep/runtime/router.py`

角色：确定性意图分类（`IntentRouter`）。

Intent 取值：`CREATE`、`MODIFY`、`DEBUG`、`REPAIR`、`IMAGE`、`CHAT`。前五个由
router 分类；`REPAIR` 由修复调用方显式指定。分类基于关键词/签名（debug 前缀、
错误日志签名、知识问题），对无项目的模糊输入可选用 LLM 兜底。不得在未更新测试
的情况下改变路由顺序。

### `openbrep/runtime/micro_modify.py`

角色：确定性参数值修改（"把层板数改成 5"）。

只做高精度检测：名称/描述解析、Length 单位换算、布尔词。任何模糊、复合或疑问
句式的输入返回 `None`，原样落入 LLM modify 路径。命中时修改以零 token 成本完成，
但仍运行编译。

### `openbrep/runtime/semantic_repair.py`

角色：语义验证后的有界接受/回滚修复环。

当 `verify_semantics` 报告阻塞问题时，修复环运行有界轮数后接受或回滚。修复结果
只记录用于观测，不会静默吞掉交付门禁失败。

### `openbrep/verification.py`

角色：统一验证报告。

把 static/lint/compile/plan 检查聚合成 `VerificationReport`（`passed`、`counts`、
`compile_status`）。`TaskResult.success` 就是报告的 `passed`——交付门禁。不得把它
硬编码回 `True`。

### `openbrep/semantic_verifier.py`

角色：确定性几何/行为验证（`verify_semantics`）。

在生成的脚本上运行轻量 `gdl_previewer`，抓住"能编译但几何错误"：mesh 非空/非
退化、包围盒与声明的 A/B/ZZYZX 匹配、声明的参数确实驱动几何。

### `openbrep/naming_alignment.py`

角色：可插拔的参数命名约定。

按同义词词典驱动的约定重命名参数；A/B/ZZYZX/AC_* 永远不是重命名来源，字符串
字面量引用只在整串匹配时替换。`detect_reserved_param_misuse()` 在 CREATE 与
MODIFY 验证阶段运行：保留名用错维度角色会成为阻塞的
`reserved_param_semantic_bug` 检查（交付时带警告，不自动修复）。

### `openbrep/importers/blender_script/*`

角色：Blender Python 脚本 → GDL 导入器（BS2G）。

- primitive 模式：`parser.py` / `mapper.py` / `generator.py`。
- mesh 模式：`mesh_capture.py` / `loft_detect.py` / `loft_gdl.py` /
  `mesh_gdl.py`。
- `mathutils_shim.py` 把 bpy/bmesh/mathutils stub 保留在一处。
- 不支持的操作降级为显式警告或清晰错误，绝不静默输出空结果。

### Tapir/Archicad 工作流

角色：读取选中对象参数，把安全修改写回 Archicad。

```text
openbrep/tapir_bridge.py
openbrep/tapir_controller.py
openbrep/workbench/tapir_service.py
openbrep/workbench_tapir.py
```

### `cli/main.py` 与 `scripts/obr7.py`

角色：CLI（`obr`）与本地 UI 启动编排。

- `cli/main.py` 是 Typer 应用：`create`、`modify`、`compile`、`repair`、
  `chat`、`configure`、`doctor`、`history`、`rollback`、`compare`、
  `revision`、`memory`、`import-blender`。
- 不带子命令运行 `obr` 会经 `scripts/obr7.py` 启动 React 工作台：dev 模式启动
  本地 API 加 Vite dev server 并打开浏览器；`--tauri` 模式在单端口上服务构建
  产物且不启动浏览器；`--daemon` 以分离模式运行并写状态文件。

### `src-tauri/`

角色：Tauri v2 桌面壳（Rust）。

`src/main.rs` 拉起 Python sidecar，等待 `OBR7_READY_URL` 信号，打开 Webview
窗口；窗口关闭时发送 `/api/shutdown` 并等待 Python 进程退出（孤儿进程防护）。

## HSF 与编译语义

### 创建

创建新对象时创建一个 HSF 项目目录：

```text
workspace/ObjectName/
```

### 导入 `.gsm`

导入 `.gsm` 的流程：

```text
.gsm
→ LP_XMLConverter libpart2hsf
→ 临时 HSF
→ 稳定 workspace/ObjectName/
→ HSFProject.load_from_disk()
```

如果名称已存在，当前行为是创建带 imported 后缀的副本。

### 修改

修改会更新当前 HSF 项目：

```text
auto_apply=True
  → 立即写入 scripts/params

auto_apply=False
  → 兼容旧调用路径，生成计划仍按直接写入处理
```

### 编译

编译读取当前 HSF 项目目录并写出：

```text
workspace/output/ObjectName_vN.gsm
```

编译不能创建新的 HSF 源目录。

## 生成语义

`TaskPipeline.execute()` 是 CLI、React 工作台 assistant service 和测试共用的
稳定高层入口。调用方未显式指定 intent 时，由 `IntentRouter.classify()` 解析。

```text
纯聊天 / GDL 教学问题                       → CHAT
debug 前缀 / 错误日志 / 强 debug            → DEBUG
明确的修改/检查关键词                        → MODIFY
明确的创建关键词                             → CREATE
泛 GDL 关键词                               → 有项目则 MODIFY，否则 CREATE
有图片且文本含糊                             → IMAGE
有项目且含糊                                 → MODIFY
无项目且含糊                                 → LLM 兜底，否则 CHAT
```

不要在没有更新测试的情况下改变顺序。

`execute()` 内部分发：

- MODIFY/DEBUG/REPAIR 默认走有预算的 agent loop（`request.agent_loop`）；确定性
  micro-modify 先行尝试，命中即以零 token 成本胜出。
- 编译成功后，CREATE 与 MODIFY/DEBUG/REPAIR 运行 `verify_semantics`；阻塞问题触发
  `runtime/semantic_repair.py` 的有界接受/回滚修复轮。
- `TaskResult.success` 是验证报告的 `passed`（交付门禁）。不得硬编码回 `True`。

## 验证环

生成或修改的脚本在交付前经过确定性验证环：

```text
编译门禁
  openbrep/compiler.py
  MockHSFCompiler 或 HSFCompiler (LP_XMLConverter)

统一验证报告
  openbrep/verification.py
  static / lint / compile / plan 检查 → VerificationReport.passed

语义验证
  openbrep/semantic_verifier.py::verify_semantics
  mesh 非空、包围盒 vs A/B/ZZYZX、参数响应性

修复环
  openbrep/runtime/semantic_repair.py
  发现阻塞问题时运行有界接受/回滚轮

命名对齐
  openbrep/naming_alignment.py
  保留名误用 → 阻塞的 reserved_param_semantic_bug 检查
  （交付时带警告，不自动修复）

确定性拦截
  openbrep/runtime/micro_modify.py
  纯参数值修改完全跳过 LLM 路径
```

只有验证报告通过，交付物才算"成功"。`TaskResult.success` 不得被无条件强制为
`True`。

## 本地 API Session 合约

React 工作台通过 HTTP 与有状态的 `WorkbenchSession` 单例通信。把 session 合约
视为公开应用状态。

重要字段：

```text
project            当前 HSFProject（或 None）
source             "empty" | "hsf" | "gsm" | "gdl" | "blender"
source_path        加载项目的来源
session_id         每个后端进程一个
project_epoch      切换项目时递增；前端据此丢弃过期异步结果
compiler_mode      "mock" | "lp"
recent_project_paths
```

规则：

- 变更请求由 session 级锁串行化（`request_gate.py`）；只读路由保持无锁。
- 脚本或参数变更后清空 preview 状态。
- 不可逆 AI 写入前捕获项目快照。
- 设置面板使用 draft 状态加显式保存动作。不得从附带 UI 变更写入用户配置。

## 测试策略

当前基线：

```text
python -m pytest tests/ -q
3098 passed, 87 subtests passed

frontend
cd frontend && npx vitest run
npx tsc --noEmit -p tsconfig.app.json
```

按变更类型选择测试范围：

```text
Workbench API / service 变更
  → tests/test_workbench_api.py
  → tests/test_workbench_services.py
  → tests/test_workbench_concurrency.py

Generation 变更
  → tests/test_pipeline_create_compile.py
  → tests/test_pipeline_modify.py
  → tests/test_micro_modify.py
  → tests/test_pipeline_semantic_repair.py
  → merge 前跑全量测试

验证 / 命名变更
  → tests/test_semantic_verifier.py
  → tests/test_naming_alignment.py
  → tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py

Blender 导入器变更
  → tests/test_blender_script_importer.py
  → tests/test_bs2g_mesh_loft.py
  → tests/test_bs2g_shim.py

Preview 变更
  → tests/test_gdl_previewer.py tests/test_three_preview.py
  → 渲染行为变化时做 preview smoke/manual check

Tapir/Archicad 变更
  → mock 单元测试
  → release 前人工 Archicad 检查

Frontend 变更
  → npx vitest run
  → npx tsc --noEmit -p tsconfig.app.json
```

合并到 `main` 前：

```bash
python -m pytest tests/ -q
cd frontend && npx vitest run && npx tsc --noEmit -p tsconfig.app.json
```

## Benchmark 黄金语料回归

Hosted LLM 即使 temperature=0 也非确定，因此 benchmark 效果验证使用黄金语料
录制/回放流程，而不是重跑真实模型：

```text
用真实 LLM 录制一次
  python -m benchmark.runner --suite benchmark/tasks/create/ --llm-record benchmark/fixtures/llm_corpus/create.jsonl
  python -m benchmark.runner --suite benchmark/tasks/modify/ --llm-record benchmark/fixtures/llm_corpus/modify.jsonl

确定性回放
  python -m benchmark.runner --suite benchmark/tasks/create/ --llm-replay benchmark/fixtures/llm_corpus/create.jsonl
```

CI 的 `benchmark-replay` job 回放入库语料并与 `benchmark/baseline.json` 比较
（PASS→FAIL、criteria_failures 增加、pass 数下降都会红灯）。

语料规则：

- 只有送给 LLM 的 prompt 变化时才重录语料（`knowledge/`、`user_knowledge/`、
  `skills/`、prompt 拼装、model/provider、学习记忆注入）。
- 生成后的处理（linter、static checker、naming alignment、semantic verifier、
  修复接受判定、编译链路、断言逻辑）不改变 prompt，用现有语料回放验证即可。
- 回放未命中语料是特性：它拦住"悄悄改变 prompt 却不重录"的情况。
- 基线更新不得退化：`benchmark/update_baseline.py` 拒绝退化方向的更新。

## 分支与合并规则

`main` 是已验证、可运行分支。

建议分支名：

```text
refactor-*
feature-*
fix-*
```

流程：

```text
1. 从干净 main 开始。
2. 创建聚焦分支。
3. commit 保持小而清晰。
4. 编辑时跑目标测试。
5. merge 前跑全量测试。
6. 测试通过后 merge 回 main。
7. merge 后 push main。
```

workbench/service 重构不要长期挂着不合并。session 有很多共享状态路径，过期
分支会迅速变贵。

## AI 编程工具规则

AI 工具改这个仓库时必须遵守：

先从用户要的结果出发，定义成功标准，再选择执行步骤。架构规则是这个循环的护栏：
理解当前边界，做最小且完整的修改，测试，修复，再测试；只有请求结果被验证后才算
完成。当前会话内能实现时，不要停在计划阶段。

1. 改架构前先读本文。
2. 编辑前执行 `git status --short --branch`。
3. 不要重新引入已退役 Streamlit `ui/` 包的 import。
4. 不要把 `.gsm` 当作可编辑源。HSF 项目目录才是源。
5. 不要绕过 `HSFProject` 处理源状态。
6. 不要在没有测试的情况下重写 `run_agent_generate` 行为。
7. 不要在没有更新测试的情况下改变生成意图路由顺序。
8. 不要在 Blender 导入器里静默丢几何——不支持的操作降级为显式警告或清晰错误。
9. bpy/bmesh/mathutils stub 只放在一处
   （`importers/blender_script/mesh_capture.py`、`mathutils_shim.py`）。
10. 不要添加未在 `pyproject.toml` 声明的运行时依赖。
11. 不要从附带 UI 变更静默写入用户配置。
12. 不要破坏当前 flat workspace 布局。

## 新功能放哪里

按此决策表：

| 功能类型 | 优先位置 |
|---|---|
| 新工作台面板 | `frontend/src/workbench/<feature>/`、`frontend/src/components/*` |
| 新 store/action 逻辑 | `frontend/src/state/actions/*` |
| 新 API 路由 | `openbrep/workbench_api.py`（薄）+ `openbrep/workbench/*_service.py` |
| 新项目导入选项 | `openbrep/workbench/project_service.py`、`project_session_service.py` |
| 新编译/版本行为 | `openbrep/workbench/compiler_service.py`、`revision_service.py` |
| 新 AI 生成行为 | `openbrep/runtime/pipeline.py` 或新的 `runtime/*` 模块 |
| 新确定性参数修改 | `openbrep/runtime/micro_modify.py` |
| 新验证规则 | `openbrep/verification.py`、`openbrep/semantic_verifier.py` |
| 新命名规则 | `openbrep/naming_alignment.py` |
| 新 preview 能力 | `openbrep/workbench/preview_service.py` / `three_preview.py` + 前端 viewport |
| 新 Tapir 动作 | `openbrep/workbench/tapir_service.py`、`openbrep/tapir_bridge.py` |
| 新 GDL 校验规则 | `openbrep/validator.py`、`openbrep/gdl_linter.py`、`openbrep/static_checker.py` |
| 新模型/provider 逻辑 | `openbrep/config.py`（`PROVIDER_PROFILES`）、`openbrep/llm.py` |
| 新知识库行为 | `openbrep/knowledge_selector.py`、`openbrep/knowledge.py` |
| 新 CLI 命令 | `cli/main.py` |
| Blender 导入器变更 | `openbrep/importers/blender_script/*` |

## 当前重构里程碑

已完成：

```text
Phase 4: React 工作台成为默认 UI (v0.8.0)
Phase 5: Tauri 桌面壳落地，React 工作台成为唯一 UI (v0.9.0)
```

最近一轮清理完成：

```text
1. Domain 逻辑迁入 openbrep/workbench/*_service.py，workbench_api.py 作为组合根。
2. 验证统一到 openbrep/verification.py
   （static/lint/compile/plan 检查 → VerificationReport）。
3. 编译成功后加入语义修复环（runtime/semantic_repair.py）；
   TaskResult.success 是验证报告的 passed。
4. 确定性 micro-modify（runtime/micro_modify.py）在 LLM modify 路径之前拦截
   纯参数值修改。
5. 新增 Blender 导入器（BS2G），支持 primitive 与 bmesh mesh 脚本。
```

## 手工发布检查

触及 UI、生成、编译或 Tapir 的 release 前应检查：

```text
1. 启动工作台（obr）或 Tauri 应用。
2. 用自然语言生成简单对象。
3. 修改已有对象。
4. 只要求解释，确认不会修改脚本。
5. 导入 .gdl 文件。
6. 用 LP_XMLConverter 导入 .gsm 文件。
7. 导入 Blender .py 脚本（primitive + bmesh loft）。
8. 加载已有 HSF 目录。
9. 运行 2D/3D preview（所有显示模式）。
10. 编译版本化 .gsm。
11. 如果有 Archicad，reload library 并读取选中对象参数。
12. 如果有 Tapir，写回一个安全参数修改。
```

## 产品方向

OpenBrep 要成为顶级 GDL 代码工作台，而不是通用 AI 聊天包装。优先级：

- HSF-native 项目管理。
- 编译验证输出。
- 可追溯 revision 与 GSM 资产。
- 专业 GDL 解释、修复、重构。
- 面向 Archicad 高阶用户的高密度高效工作流。
- 让 AI 辅助开发安全的明确架构边界。

长期架构目标：

```text
frontend/src/workbench/*
  React 工作台 shell 与面板

frontend/src/state/*
  store 与 actions

openbrep/workbench_api.py
  薄的本地 API 组合根

openbrep/workbench/*_service.py
  project、generation、compile、preview、memory、settings、tapir services

openbrep/runtime/*
  UI 无关的 domain pipeline、意图路由、修复

openbrep/*
  HSF/GDL domain engine

cli/main.py
  obr CLI

tests
  保护 AI/工具驱动重构的行为契约
```
