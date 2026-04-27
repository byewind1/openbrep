# OpenBrep AI 开发指南

日期：2026-04-27  
对象：Codex、Claude Code、Qwen Code、Cursor、Copilot Agent，以及使用 AI 辅助开发的人类维护者  
英文版：[AI_DEVELOPMENT_GUIDE.md](AI_DEVELOPMENT_GUIDE.md)

这是 AI 工具参与 OpenBrep 开发时必须遵守的操作契约。请和 [ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md) 一起阅读。

## 项目使命

OpenBrep 不是通用聊天机器人。它是面向 Archicad 用户和 GDL 开发者的专业 GDL 代码工作台。

每次改动都应强化以下至少一个产品支柱：

```text
HSF-native 源码管理
GDL 代码生成、修复、解释、重构
编译验证 GSM 输出
资产与 revision 可追溯
适合长期高频使用的专家工作台 UI
```

## 面向目标的 Agent 契约

OpenBrep 希望 AI 开发工具围绕成功标准自主闭环，而不是只机械执行逐步指令。本指南里的操作规则是质量和架构护栏，不是目标本身。

开始修改前，先为当前请求定义简短的完成条件。好的完成条件应说明：

- 哪个用户可见行为、文档或工程结果必须出现。
- 哪个架构边界必须保持不变。
- 哪些测试或人工检查可以证明结果达标。
- 本轮是否需要提交、push，并确认 `origin/main` 同步。

随后按自主循环执行：

```text
理解上下文
定义成功标准
做最小且完整的修改
运行针对性检查
修复失败
运行最终必要检查
需要时提交、push、确认同步
报告结果、验证方式和残余风险
```

如果当前会话内可以实现，不要停在计划阶段。只有在缺失信息会阻塞成功，或合理假设会带来产品、数据风险时，才向人类追问。

## AI Agent 开工前动作

编辑前先执行：

```bash
git status --short --branch
rg -n "relevant_symbol" .
python -m pytest tests/ -q
```

如果全量测试对当前步骤太重，可以先跑目标测试；但合并前必须跑全量测试。

不要一上来重写大文件。先理解当前边界。

## 当前安全基线

截至 2026-04-27：

```text
新工作开始前 main 应保持干净且已 push
ui/app.py: 1773 行
测试基线：452 passed, 6 subtests passed
```

已经合入的核心重构边界：

```text
ui/project_service.py
ui/generation_service.py
ui/app_shell.py
ui/chat_controller.py
ui/chat_render.py
ui/session_defaults.py
ui/views/*
```

## 绝对规则

1. 不要把实质新逻辑继续堆进 `ui/app.py`。
2. 不要绕过 `HSFProject` 管理源状态。
3. 不要把 `.gsm` 当作可编辑源文件。
4. 不要复制聊天气泡渲染代码。
5. 不要分散添加 `st.session_state` 默认值。
6. 没有测试时不要重写 `run_agent_generate` 行为。
7. 不要随意改变 intent 路由顺序。
8. 不要因为 wrapper 看起来重复就删除兼容 wrapper。
9. 不要让 Streamlit view 实例化 LLM、compiler 或 pipeline。
10. 不要破坏 flat workspace 布局兼容性。

## 代码放置规则

按下面规则决定代码应该放哪里：

```text
纯 domain 行为
  openbrep/*

Streamlit page shell / CSS / 可选依赖探测
  ui/app_shell.py

Session 默认值
  ui/session_defaults.py

项目导入 / 加载 / 编译工作流
  ui/project_service.py
  ui/project_io.py

AI 生成工作流
  ui/generation_service.py
  openbrep/runtime/pipeline.py

Vision / 图片工作流
  ui/vision_controller.py

聊天单轮编排
  ui/chat_controller.py

聊天渲染
  ui/chat_render.py

Streamlit 面板
  ui/views/*

UI 纯格式化 / 解析 helper
  ui/view_models.py

Tapir / Archicad 工作流
  ui/tapir_controller.py
  ui/tapir_views.py
  openbrep/tapir_bridge.py
```

如果位置不明确，优先在 `ui/app.py` 保留很薄的 adapter，把真实行为放进可测试模块。

## 兼容 Wrapper

`ui/app.py` 中保留了一些公开兼容 wrapper，因为测试和 UI callback 仍会直接 import 或 patch 它们。

典型例子：

```text
run_agent_generate
chat_respond
classify_and_extract
_handle_unified_import
_handle_hsf_directory_load
import_gsm
do_compile
_apply_generation_result
_apply_generation_plan
```

除非在同一个变更里迁移所有测试和调用方，否则不要删除或改名。

## Session State 纪律

新增持久 key 时，放到 `ui/session_defaults.py`。

脚本或参数发生变更时：

```text
清空 preview data
清空 preview warnings
重置 preview metadata
程序化改变编辑器内容时 bump editor version
不可逆 AI 写入前 capture snapshot
```

View 不应直接修改关键状态。通过 callback 注入行为。

## 生成链路契约

当前生成链路：

```text
ui/app.py.run_agent_generate
  → ui/generation_service.GenerationService.run_agent_generate
  → openbrep.runtime.pipeline.TaskPipeline.execute
  → build_generation_result_plan
  → ui/actions.apply_generation_plan
  → ui/view_models.build_generation_reply
```

Intent 路由顺序：

```text
debug intent                  → REPAIR
modify bridge prompt          → MODIFY
post clarification explain    → CHAT
post clarification check      → MODIFY
explainer intent              → CHAT
existing script content       → MODIFY
otherwise                     → CREATE
```

生成相关改动至少跑：

```bash
python -m pytest tests/test_generation_service.py tests/test_llm.py -q
python -m pytest tests/ -q
```

## 项目生命周期契约

当前项目路径：

```text
ui/app.py wrapper
  → ui/project_service.ProjectService
  → ui/project_io
  → openbrep.hsf_project.HSFProject
  → openbrep.compiler
```

规则：

```text
导入 .gsm 会创建或加载一个 HSF 项目目录。
导入 .gdl/.txt 会包装成 HSF 项目。
加载 HSF 会打开已有源目录。
编译写出 output/ObjectName_vN.gsm。
编译不能创建新的源目录。
```

项目相关改动至少跑：

```bash
python -m pytest tests/test_project_service.py tests/test_project_io.py tests/test_project_io_compile.py -q
python -m pytest tests/test_llm.py -q
```

## UI 设计规则

OpenBrep 是工作台，不是营销页。

优先：

```text
高密度但清晰的控件
明确的工作流分区
稳定的面板尺寸
面向动作的按钮文案
表格、tabs、segmented controls、toggles、紧凑按钮
```

避免：

```text
大型装饰 hero
卡片套卡片
过重渐变
散落在 view 中的一次性 CSS
重复聊天渲染
本该写入文档的 UI 说明文字
```

## 测试矩阵

编辑时跑最小有效测试，合并前跑全量测试。

```text
Shell / bootstrap
  tests/test_app_shell.py

Session defaults
  tests/test_session_defaults.py

Chat renderer / panel / controller
  tests/test_chat_render.py
  tests/test_chat_panel_render.py
  tests/test_chat_controller_single_panel.py
  tests/test_chat_flow.py

Generation
  tests/test_generation_service.py
  tests/test_llm.py

Project lifecycle
  tests/test_project_service.py
  tests/test_project_io.py
  tests/test_project_io_compile.py

Preview
  tests/test_preview_controller.py

Vision
  tests/test_vision.py

全量
  python -m pytest tests/ -q
```

## 手工检查

影响 UI、生成、编译、Tapir 或 Archicad 行为时，需要手工 smoke test：

```text
1. streamlit run ui/app.py
2. 生成一个简单对象。
3. 修改生成对象。
4. 只要求解释，确认不会修改代码。
5. 导入 .gdl。
6. 有 LP_XMLConverter 时导入 .gsm。
7. 加载已有 HSF 目录。
8. 运行本地脚本检查。
9. 运行 2D/3D preview。
10. 编译版本化 .gsm。
11. 有 Archicad/Tapir 时读取选中对象参数。
12. 有 Archicad/Tapir 时写回一个安全参数编辑。
```

## 分支流程

非平凡改动使用分支隔离：

```bash
git switch main
git pull
git switch -c refactor-something
```

完成改动后：

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push -u origin branch-name
```

测试通过后合并：

```bash
git switch main
git merge --no-ff branch-name -m "merge branch-name"
python -m pytest tests/ -q
git push
```

默认收尾动作：

除非用户明确要求不要提交或不要 push，否则完成的代码或文档工作默认以
commit、push、同步 main 收尾。直接在 `main` 工作时：

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
```

如果在分支工作，先 push 分支，再 merge 回 `main`，跑全量测试，push `main`，
最后确认 `main` 与 `origin/main` 指向同一提交。

## AI 变更审查清单

提交前回答这些问题：

```text
代码是否放在正确层？
是否保留 HSF 作为源格式？
是否保留现有 wrapper 兼容性？
新增 state 是否加入 session_defaults？
是否新增或更新测试？
是否跑了正确的目标测试？
merge 前是否跑了全量测试？
即使单元测试通过，是否仍可能破坏 Streamlit 手工路径？
最终回复是否说明未覆盖的手工风险？
```

## 已知技术债

高价值后续工作：

```text
1. 将 config/model source 处理移出 ui/app.py。
2. 拆分 tests/test_llm.py 为多个聚焦测试模块。
3. 为 HSF-as-source 和 generation-service 边界补 ADR。
4. 在不提前删除 wrapper 的前提下，把 ui/app.py 继续降到 1400-1600 行。
```

## 产品方向提醒

不确定时，为专业 GDL 开发者优化：

```text
快速导入
清晰可编辑源
可信 AI 修改
编译验证
可追溯输出
可重复工作流
低摩擦 Archicad 交接
```

不要只为演示效果优化。OpenBrep 应该像严肃的 GDL 工程工作台。
