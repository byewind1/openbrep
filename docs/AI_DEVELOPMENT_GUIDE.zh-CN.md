# OpenBrep AI 开发指南

日期：2026-10-02  
对象：Codex、Claude Code、Qwen Code、Cursor、Copilot Agent，以及使用 AI 辅助开发的人类维护者  
英文版：[AI_DEVELOPMENT_GUIDE.md](AI_DEVELOPMENT_GUIDE.md)

这是 AI 工具参与 OpenBrep 开发时必须遵守的操作契约。请和 [ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md) 一起阅读。

涉及源格式、生成边界或 Skill 机制时，还应阅读：

- [ADR 0001: HSF 项目目录是 OpenBrep 的源格式](adr/0001-hsf-as-source.zh-CN.md)
- [ADR 0002: AI 生成写入由 generation service 边界承接](adr/0002-generation-service-boundary.zh-CN.md)
- [ADR 0003: 自定义 Skill 是用户经验的可追溯输入](adr/0003-custom-skill-workflow.zh-CN.md)

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

截至 2026-10-02：

```text
新工作开始前 main 应保持干净且已 push
python tests: 3098 passed, 87 subtests passed
frontend: 780 passed (vitest) + tsc clean
```

已经就位的核心 seam：

```text
frontend/src/workbench/*, frontend/src/state/*, frontend/src/components/*
openbrep/workbench_api.py（组合根，薄适配层）
openbrep/workbench/*_service.py
openbrep/runtime/pipeline.py
```

已退役的 Streamlit `ui/` 包在仓库中已不存在，不要把它当作新代码的参照。

## 绝对规则

1. 不要重新引入已退役 Streamlit `ui/` 包的 import。
2. 不要绕过 `HSFProject` 管理源状态。
3. 不要把 `.gsm` 当作可编辑源文件。
4. 不要让 `workbench_api.py` 长出实质逻辑；真实行为放进
   `openbrep/workbench/*_service.py`。
5. 不要从附带 UI 变更静默写入用户配置（设置面板用 draft 状态加显式保存）。
6. 没有测试时不要重写 `run_agent_generate` 行为。
7. 不要随意改变 intent 路由顺序。
8. 不要在一个 service 模块里混入不相关 seam。
9. 不要让 React 视图直接实例化 LLM、compiler 或 pipeline——必须经本地 API 与
   service 层。
10. 不要破坏 flat workspace 布局兼容性。

## 代码放置规则

按下面规则决定代码应该放哪里：

```text
纯 domain 行为
  openbrep/*

React 工作台 UI（页面、面板、store、actions）
  frontend/src/workbench/*
  frontend/src/components/*
  frontend/src/state/*

本地 API 组合根（薄适配层）
  openbrep/workbench_api.py

后端服务
  openbrep/workbench/*_service.py

AI 生成工作流
  openbrep/runtime/pipeline.py

确定性参数修改
  openbrep/runtime/micro_modify.py

Blender 脚本 → GDL 导入器（BS2G）
  openbrep/importers/blender_script/*

Tapir / Archicad 工作流
  openbrep/tapir_bridge.py
  openbrep/tapir_controller.py
  openbrep/workbench/tapir_service.py
  openbrep/workbench_tapir.py

CLI（obr）
  cli/main.py
```

如果位置不明确，保持 `workbench_api.py` 为薄适配层，把真实行为放进可测试的
service 模块。

## 兼容 Wrapper

Streamlit 时代的 `ui/app.py` wrapper（`run_agent_generate`、`chat_respond`
等）已随 `ui/` 包一起移除。当前的稳定入口是：

```text
openbrep.runtime.pipeline.TaskPipeline.execute      （CLI 与工作台生成）
openbrep/workbench_api.py WorkbenchSession 路由      （本地 API 合约）
```

除非在同一个变更里迁移所有测试和调用方，否则不要改变它们的行为或路由 payload。

## Session State 纪律

React 工作台的 UI 状态在 Zustand store（`frontend/src/state/`）里。服务端
session 状态在 `WorkbenchSession`（`openbrep/workbench_api.py`），是公开应用
合约。

脚本或参数发生变更时：

```text
清空 preview data
清空 preview warnings
重置 preview metadata
程序化改变编辑器内容时 bump editor version
不可逆 AI 写入前 capture snapshot
```

View 不应直接修改关键状态。行为通过 store 的 actions 传递，设置类写入必须走
draft 状态加显式保存动作。

## 生成链路契约

当前生成链路：

```text
CLI 或 React 工作台 assistant 路由
  → openbrep/workbench/assistant_service.py
  → openbrep.runtime.pipeline.TaskPipeline.execute
  → 编译门禁 + verify_semantics（有界修复轮）
  → TaskResult（success = 验证报告 passed）
```

Intent 路由顺序（`IntentRouter.classify()`）：

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

生成相关改动至少跑：

```bash
python -m pytest tests/test_pipeline_create_compile.py tests/test_pipeline_modify.py tests/test_micro_modify.py tests/test_pipeline_semantic_repair.py -q
python -m pytest tests/ -q
```

## GDL 知识上下文契约

GDL 生成不是把整个 `knowledge/` 目录粗暴塞进 prompt。CREATE / IMAGE 请求必须先经过目标相关知识选择：

```text
openbrep.runtime.pipeline.TaskPipeline
  → openbrep.knowledge_selector.select_gdl_knowledge
  → planner_context 给 object_planner
  → generation_context 给 GDLAgent.generate_only
```

知识层级：

```text
knowledge/archetypes/*       构件级建模知识，如 bookshelf、cabinet、door
knowledge/wiki/*             命令级知识，如 BLOCK、PRISM_、PROJECT2
knowledge/*.md               基础语法、参数、控制流、常见错误
.openbrep/knowledge/*        当前 HSF 项目级知识
.openbrep/skills/*           当前 HSF 项目级 skill
workspace memory/learnings   错题本与 learned skill
```

新增或修改 GDL 知识时，必须保持：

- archetype 文档带 frontmatter，至少包含 `id`、`type`、`task_types`、`object_types`、`commands`、`script_types`、`priority`、`tags`。
- archetype 中引用的核心 GDL 命令应有对应 `knowledge/wiki/<COMMAND>.md`，或者在 lint 脚本中有明确豁免理由。
- planner 结果必须保留实际 `knowledge_sources`，不能只让 LLM 自己编写来源。
- object plan report 和 trace 应保留 `knowledge_sources`，用于后续排查生成质量。

知识相关改动至少跑：

```bash
python knowledge/scripts/lint-knowledge.py knowledge
python scripts/knowledge_context_smoke.py --json
python -m pytest tests/test_knowledge_selector.py tests/test_knowledge_lint.py tests/test_knowledge_context_smoke.py tests/test_object_planner.py -q
python -m pytest tests/ -q
```

不要把 `CLAUDE.md`、`AGENTS.md`、`index.md`、`log.md` 这类维护说明注入 LLM 生成上下文。

### 专业知识交叉验证

当新增或修改命令级 GDL 知识时，应把本地知识、Graphisoft 官方文档和官方社区入口分层验证。

分批执行计划见：[GDL_KNOWLEDGE_VERIFICATION_PLAN.zh-CN.md](GDL_KNOWLEDGE_VERIFICATION_PLAN.zh-CN.md)。

权威顺序：

```text
Graphisoft GDL Reference Guide
  > Graphisoft GDL Center 其他官方文档
  > Graphisoft Community GDL 讨论
  > OpenBrep 本地知识库
  > LLM 推断
```

官方文档用于验证语法事实，社区只用于发现实践案例、边界问题和常见坑，不得用社区讨论覆盖官方语法。

默认交叉验证命令：

```bash
python scripts/verify_gdl_knowledge_sources.py \
  --commands BLOCK PRISM_ REVOLVE PROJECT2 HOTSPOT2 MATERIAL \
  --format markdown \
  --output /tmp/openbrep-gdl-knowledge-verify.md
```

如果本机 Python 无法直接连接 Graphisoft GDL Center，可先下载官方 index HTML，再离线验证：

```bash
mkdir -p /tmp/openbrep-gdl-verify
curl -L --compressed https://gdl.graphisoft.com/reference-guide/index/ \
  -o /tmp/openbrep-gdl-verify/graphisoft-gdl-index.html

python scripts/verify_gdl_knowledge_sources.py \
  --official-index-file /tmp/openbrep-gdl-verify/graphisoft-gdl-index.html \
  --commands BLOCK PRISM_ REVOLVE PROJECT2 HOTSPOT2 MATERIAL \
  --format markdown \
  --output /tmp/openbrep-gdl-knowledge-verify.md
```

如果当前环境不允许联网，但希望生成明确错误报告而不中断流程：

```bash
python scripts/verify_gdl_knowledge_sources.py --offline-ok
```

## 项目生命周期契约

当前项目路径：

```text
workbench 路由
  → openbrep/workbench/project_service.py / project_session_service.py
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
python -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
python -m pytest tests/ -q
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
Workbench API / services
  tests/test_workbench_api.py
  tests/test_workbench_services.py
  tests/test_workbench_concurrency.py

Generation
  tests/test_pipeline_create_compile.py
  tests/test_pipeline_modify.py
  tests/test_micro_modify.py
  tests/test_pipeline_semantic_repair.py

验证 / 命名
  tests/test_semantic_verifier.py
  tests/test_naming_alignment.py
  tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py

Blender 导入器
  tests/test_blender_script_importer.py
  tests/test_bs2g_mesh_loft.py
  tests/test_bs2g_shim.py

Preview
  tests/test_gdl_previewer.py tests/test_three_preview.py

Vision
  tests/test_vision.py

Frontend
  cd frontend && npx vitest run
  npx tsc --noEmit -p tsconfig.app.json

全量
  python -m pytest tests/ -q
```

## 手工检查

影响 UI、生成、编译、Tapir 或 Archicad 行为时，需要手工 smoke test：

```text
1. obr  （启动工作台）
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
即使单元测试通过，是否仍可能破坏工作台手工路径？
最终回复是否说明未覆盖的手工风险？
```

## 已知技术债

Streamlit 时代（React 工作台迁移之前）完成的治理里程碑，仅作历史保留；当前
状态以 ARCHITECTURE.zh-CN.md 为准：

```text
1. config/model source 处理已下沉到 ui/config_service.py。
2. tests/test_llm.py 已拆出 LLM adapter 与 config service 聚焦测试。
3. HSF-as-source、generation-service、custom Skill 已补 ADR。
4. ui/app.py 已降到 1400-1600 行目标区间。
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
