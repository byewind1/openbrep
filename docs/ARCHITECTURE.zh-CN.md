# OpenBrep 架构说明

日期：2026-04-27  
状态：面向维护者与 AI 编程工具的当前有效架构指南  
英文版：[ARCHITECTURE.md](ARCHITECTURE.md)

OpenBrep 是面向 Archicad 高阶用户和 GDL 开发者的 AI GDL 工作台。它不是通用聊天壳，而是围绕 HSF/GDL/GSM 生命周期构建的专业工具。

核心产品链路：

```text
自然语言或导入库对象
→ 可编辑 HSF 项目
→ AI 生成、修改、调试、解释
→ 编译验证 GSM 输出
→ 项目与资产可追溯
```

这份文档定义当前架构、模块边界、开发规则和交接规范。人类维护者和 AI 开发工具都应优先阅读。

## 当前状态

早期 Streamlit UI 主要集中在 `ui/app.py`。当前 `main` 已经完成第一轮架构治理，核心逻辑被拆入明确边界：

```text
ui/app.py: 1812 行
测试基线：447 passed, 6 subtests passed
```

`ui/app.py` 仍然偏大，但它不再是新功能堆放处。它现在应该被视为应用装配入口和兼容 wrapper 容器。

## 分层模型

新增或迁移代码时，按以下分层判断归属：

```text
View 层
  ui/views/*
  只负责 Streamlit 控件和面板渲染。应尽量无状态。

Controller 层
  ui/*_controller.py
  负责一个用户工作流的编排。可以通过注入依赖读写 session_state，
  但不应拥有核心业务规则。

Service 层
  ui/*_service.py
  负责应用级业务流程，例如项目导入、编译、生成、结果应用。
  Service 应尽量可在 Streamlit 外测试。

Shell / Bootstrap 层
  ui/app_shell.py
  负责页面配置、全局 CSS、可选依赖探测、本地运行能力探测。

Domain 层
  openbrep/*
  负责 HSF/GDL 解析、编译、验证、模型路由、知识库、运行时 pipeline。
  这一层不应 import Streamlit。

Tests
  tests/*
  保护 UI controller、service、domain 逻辑和历史兼容 wrapper 的行为。
```

## 运行时流程

```text
用户输入
  ├─ 自然语言
  ├─ 图片
  ├─ .gdl / .txt 导入
  ├─ .gsm 导入
  └─ HSF 目录加载

Streamlit View
  └─ ui/views/*

Controller / Service 边界
  ├─ ui/chat_controller.py
  ├─ ui/project_service.py
  ├─ ui/generation_service.py
  ├─ ui/vision_controller.py
  ├─ ui/revision_controller.py
  └─ ui/preview_controller.py

Domain Core
  ├─ openbrep/hsf_project.py
  ├─ openbrep/runtime/pipeline.py
  ├─ openbrep/compiler.py
  ├─ openbrep/gdl_parser.py
  ├─ openbrep/paramlist_builder.py
  ├─ openbrep/validator.py
  └─ openbrep/knowledge.py

输出
  ├─ 可编辑 HSF 项目目录
  ├─ 等待人工确认的 pending diffs
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
```

规则：

- `.gsm` 是编译产物，不是源格式。
- 单个 `.gdl` 文件不足以表示完整库对象。
- `paramlist.xml` 与 `scripts/*.gdl` 必须作为同一个源单元处理。
- 编译不能创建新的 HSF 源目录。
- 导入 `.gsm` 可以创建新的稳定 HSF 项目目录。
- 修改对象时，要么更新当前 HSF 项目，要么生成 pending diffs 等待确认。

相关文档：[project_layout.md](project_layout.md)

## 关键模块职责

### `ui/app.py`

角色：应用装配入口。

允许职责：

- 导入依赖和兼容符号。
- 初始化 page shell 和 `session_state`。
- 加载 sidebar 需要的配置全局变量。
- 定义测试或 Streamlit callback 仍依赖的薄 wrapper。
- 组装三栏 UI。
- 向 views、controllers、services 注入依赖。

不要新增：

- 大块 UI 渲染。
- 新业务流程。
- 分散的 session 默认值初始化。
- 新 LLM 或 compiler 编排。
- 重复 HTML/CSS。

如果 `app.py` 中新增代码超过约 30-50 行，通常应该移到 view、controller 或 service。

### `ui/app_shell.py`

角色：Streamlit shell 与本地能力探测。

负责：

- `configure_page(st)`
- 全局 CSS
- `streamlit_ace` 可用性
- Plotly 可用性
- Tapir bridge 可用性
- Archicad 进程探测

这里不能放产品业务流程。该模块应保持浅、稳定、导入安全。

### `ui/session_defaults.py`

角色：集中管理 `st.session_state` 默认值。

所有新的持久 session key 都应加到这里，除非它是明确的一次性临时值并且在使用前立即创建。

规则：

- 保持现有 key 名稳定，除非做明确迁移。
- 按领域分组。
- 不要复用共享可变默认对象。
- controller 依赖的新 key 必须补测试。

### `ui/views/*`

角色：Streamlit 渲染层。

典型文件：

- `sidebar_panel.py`
- `chat_panel.py`
- `editor_panel.py`
- `parameter_panel.py`
- `project_tools_panel.py`
- `workspace_tools_panel.py`
- `preview_views.py`

规则：

- View 通过 callback 接收行为。
- View 不实例化 LLM、compiler、pipeline 或 service。
- View 可以读取简单状态用于展示，但不应拥有工作流决策。
- 保持现有 UI 语言和工作流结构。
- 不要加入卡片套卡片或破坏工作台信息密度的装饰性布局。

### `ui/chat_render.py`

角色：统一聊天消息渲染。

历史消息和 live 输出都必须使用该模块。不要重新引入 `st.chat_message`，也不要在其他文件复制气泡 HTML。

### `ui/chat_controller.py`

角色：单轮聊天编排。

负责：

- 从聊天/sidebar 触发 Tapir 操作。
- bridge follow-up 输入解析。
- debug 前缀解析。
- 文本路径与图片路径分发。
- 聊天锚点焦点处理。

不负责：

- GDL 生成内部逻辑。
- Vision 生成内部逻辑。
- 项目导入/编译语义。
- 原始 view 渲染。

### `ui/project_service.py`

角色：项目生命周期 service。

负责：

- 将当前 HSF 项目编译为版本化 GSM。
- 通过 LP_XMLConverter 导入 GSM。
- 加载已有 HSF 目录。
- 统一处理 `.gdl` / `.txt` / `.gsm` 导入。
- 将加载后的项目写入 session state。

它将底层 IO 委托给 `ui/project_io.py`，将状态修改委托给已有 action helper。

### `ui/generation_service.py`

角色：AI 生成工作流 service。

负责：

- 生成生命周期状态。
- 生成路径中的 intent 判定。
- 构造 `TaskPipeline` 请求。
- 裁剪近期聊天历史。
- 取消生成处理。
- 通过注入 callback 应用生成计划。

兼容规则：

`ui/app.py.run_agent_generate` 仍然是稳定入口。测试和其他模块可能 patch `ui.app` 中的符号；在测试迁移前不要删除这个 wrapper。

### `ui/vision_controller.py`

角色：图片驱动生成/调试工作流。

负责：

- Vision 路由准备。
- Vision 生成生命周期接入。
- 图片错误分类。
- 应用生成脚本或 pending diffs。

在图片路径有更完整真实 UI 测试前，不要急着把它合并进 `generation_service.py`。

### `ui/gdl_checks.py`

角色：编辑器快速本地 GDL 检查。

这不是 LP_XMLConverter 编译验证的替代品，只是快速反馈。

### `openbrep/runtime/pipeline.py`

角色：LLM 任务执行的 domain pipeline。

运行时 pipeline 应独立于 Streamlit。它可以接收 `on_event`、`should_cancel` 等 callback，但不应依赖 `st.session_state`。

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

修改会更新当前 HSF 项目或生成 pending diffs：

```text
auto_apply=True
  → 立即写入 scripts/params

auto_apply=False
  → session_state.pending_diffs
  → 用户确认写入
```

### 编译

编译读取当前 HSF 项目目录并写出：

```text
workspace/output/ObjectName_vN.gsm
```

编译不能创建新的 HSF 源目录。

## 生成语义

`run_agent_generate` 是聊天、elicitation、vision debug 和测试仍使用的高层稳定入口。内部委托给 `GenerationService`。

生成路径 intent 判定顺序：

```text
debug intent                  → REPAIR
modify bridge prompt          → MODIFY
post clarification explain    → CHAT
post clarification check      → MODIFY
explainer intent              → CHAT
existing script content       → MODIFY
otherwise                     → CREATE
```

不要在没有更新测试的情况下改变顺序。

生成结果处理：

- `TaskPipeline.execute()` 返回 task result。
- `build_generation_result_plan()` 将结果转成应用计划。
- `ui/actions.py` 将计划应用到当前项目或 pending review。
- `ui/view_models.py` 格式化聊天回复。

## Session State 合约

UI 是有状态应用。`session_state` 应视为公开应用合约。

重要 key：

```text
project
work_dir
chat_history
pending_diffs
pending_ai_label
pending_gsm_name
script_revision
editor_version
preview_2d_data
preview_3d_data
preview_warnings
preview_meta
active_generation_id
generation_status
generation_cancel_requested
last_project_snapshot
tapir_selected_guids
tapir_param_edits
model_api_keys
assistant_settings
```

规则：

- 新默认值加到 `ui/session_defaults.py`。
- key 名保持稳定，除非实现迁移。
- 脚本或参数变更后清空 preview 状态。
- 程序写入编辑器内容后 bump editor version。
- 不可逆 AI 写入前捕获项目快照。
- View 模块不要直接修改核心 project 状态，应通过注入 callback。

## 测试策略

当前基线：

```text
python -m pytest tests/ -q
447 passed, 6 subtests passed
```

按变更类型选择测试：

```text
View 渲染变更
  → 对应 view tests
  → 相关 controller tests

Session key 变更
  → tests/test_session_defaults.py
  → 受影响 controller/service tests

Chat flow 变更
  → tests/test_chat_flow.py
  → tests/test_chat_controller_single_panel.py
  → tests/test_chat_panel_render.py

Generation 变更
  → tests/test_generation_service.py
  → tests/test_llm.py
  → merge 前跑全量测试

Project import/compile 变更
  → tests/test_project_service.py
  → tests/test_project_io.py
  → tests/test_project_io_compile.py
  → tests/test_llm.py 中相关导入流程

Preview 变更
  → tests/test_preview_controller.py
  → 渲染行为变化时做 preview smoke/manual check

Tapir/Archicad 变更
  → mock 单元测试
  → release 前人工 Archicad 检查
```

合并到 `main` 前：

```bash
python -m py_compile ui/app.py
python -m pytest tests/ -q
```

实质 UI 变更还应运行：

```bash
streamlit run ui/app.py
```

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

UI/service 重构不要长期挂着不合并。Streamlit 共享状态路径很多，过期分支会迅速变贵。

## AI 编程工具规则

AI 工具改这个仓库时必须遵守：

1. 改架构前先读本文。
2. 编辑前执行 `git status --short --branch`。
3. 不要把新功能实质逻辑直接写进 `ui/app.py`，除非只是薄 wrapper 或依赖装配。
4. 优先使用既有模块：
   - UI 渲染 → `ui/views/*`
   - 工作流编排 → `ui/*_controller.py`
   - 业务流程 → `ui/*_service.py`
   - 纯格式化/解析 helper → `ui/view_models.py`
   - domain 逻辑 → `openbrep/*`
5. 除非迁移测试和调用方，否则保留公开 wrapper 名。
6. 每个行为变更都要加或更新测试。
7. 不要随意改变 GDL/HSF 源格式语义。
8. 不要把 `.gsm` 当作源格式。
9. 不要破坏现有 flat workspace 布局。
10. Streamlit UI 保持工作台式高密度设计。

## 新功能放哪里

| 功能类型 | 优先位置 |
|---|---|
| 新 sidebar 控件 | `ui/views/sidebar_panel.py` 加 callback 注入 |
| 新项目导入选项 | `ui/project_service.py` 与 `ui/project_io.py` |
| 新编译/版本行为 | `ui/project_service.py`、revision controller、测试 |
| 新聊天动作 | `ui/views/chat_panel.py`、`ui/chat_controller.py`、`ui/chat_render.py` |
| 新 AI 生成行为 | `ui/generation_service.py` 或 `openbrep/runtime/pipeline.py` |
| 新图片生成/调试行为 | `ui/vision_controller.py` |
| 新 preview 能力 | `ui/preview_controller.py`、`ui/views/preview_views.py`、domain previewer |
| 新 Tapir 动作 | `ui/tapir_controller.py`、`ui/tapir_views.py` |
| 新 GDL 校验规则 | 快速 UI 检查放 `ui/gdl_checks.py`，domain 校验放 `openbrep/validator.py` |
| 新模型/provider 逻辑 | `openbrep/config.py`、`openbrep/llm.py`、sidebar callback |
| 新知识库行为 | `ui/knowledge_access.py`、`openbrep/knowledge.py` |

## 已完成重构里程碑

```text
Phase 1: project service boundary
Phase 2: generation service boundary
Phase 3: app shell boundary
```

剩余高价值工作：

```text
1. 将 license / Pro knowledge import 移出 app.py。
2. 将 config/model source management 移出 app.py。
3. 拆分 tests/test_llm.py 为聚焦测试模块。
4. 增加 docs/MANUAL_TEST_CHECKLIST.md。
5. 为关键架构决策增加 ADR。
6. 在不破坏 wrapper 的前提下继续把 app.py 降到 1400-1600 行。
```

## 手工发布检查

触及 UI、生成、编译或 Tapir 的 release 前应检查：

```text
1. 启动 Streamlit UI。
2. 用自然语言生成简单对象。
3. 修改已有对象。
4. 只要求解释，确认不会修改脚本。
5. 导入 .gdl 文件。
6. 用 LP_XMLConverter 导入 .gsm 文件。
7. 加载已有 HSF 目录。
8. 运行本地脚本检查。
9. 运行 2D/3D preview。
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
- 面向 Archicad 高阶用户的高效工作流。
- 让 AI 辅助开发也安全的明确架构边界。

长期架构目标：

```text
ui/app.py
  薄装配入口

ui/views
  所有 Streamlit 渲染

ui/controllers
  工作流编排

ui/services
  project、generation、revision、license、knowledge services

openbrep
  无 Streamlit 依赖的 domain engine

tests
  保护 AI/工具驱动重构的行为契约
```
