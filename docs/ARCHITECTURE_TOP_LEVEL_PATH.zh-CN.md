# OpenBrep 顶级架构优化路径

日期：2026-05-07  
状态：执行中  
适用范围：功能基本定型后的架构治理

OpenBrep 的使命不是做一个通用 AI 聊天壳，而是做面向 Archicad 高阶用户和 GDL 开发者的专业 AI GDL 工作台。顶级架构应围绕一条稳定主线组织：

```text
用户目标
→ 专业 GDL 任务理解
→ HSF 源项目
→ AI 生成 / 修改 / 修复 / 解释
→ 预览与编译验证
→ 可追溯资产交付
```

## 最优调整路径

### 1. 固定产品级 Seam

长期只保留这些高价值 Seam：

- **HSF Source Session**：管理当前 HSF 项目、脚本、参数表、保存、另存、快照和源状态。
- **AI Workbench**：管理聊天输入、意图路由、专业 GDL 任务梳理、生成、修复、解释和图像调试。
- **Preview Verification**：管理 2D/3D 预览、预览预检、编译前后验证和可见编辑器缓冲同步。
- **Knowledge Memory**：管理官方知识、项目知识、Pro Skill、错题本、聊天记录和二阶段整理。
- **Archicad Adapter**：管理 Tapir/Archicad 实机联动，作为可选 Adapter，不污染核心链路。
- **Streamlit Shell**：只负责装配、布局和依赖注入，不承载业务规则。

这些 Seam 对应 OpenBrep 的产品合同，不能再按 UI 控件或临时按钮拆分逻辑。

### 2. 把 `ui/app.py` 压成装配入口

`ui/app.py` 的最终状态应是：

- 初始化页面与 session 默认值。
- 创建 Service / Controller / Adapter。
- 把 callback 注入 view。
- 保留历史测试需要的薄 wrapper。

任何超过薄 wrapper 的业务流程都应迁入对应 Module。这样做的收益是 Locality：后续 AI 工具改生成、预览、知识或保存逻辑时，不需要在一个大文件里跨领域搜索。

### 3. 加深 AI Workbench

AI Workbench 是下一阶段最重要的 Module。它不应只是“把用户话发给 LLM”，而要封装：

- 明确目标时直接生成，避免生硬追问。
- 目标不完整但可推断时，先自主生成专业假设。
- 需要工程约束时，把尺寸、参数、GDL 命令、预览可行性、编译风险一次性组织成生成上下文。
- 输出结果必须进入 HSF 源项目和编辑器，而不是只停留在聊天文本。

这里的 Interface 应保持小：输入用户目标、当前项目、知识上下文，输出可应用的生成计划和用户可读说明。复杂的提示词、知识选择和 GDL 规则应藏在 Implementation 内。

### 4. 加深 Knowledge Memory

知识库要从“拼接文本”升级为“分层可验证上下文”：

- Free 层：官方 GDL 语法、常见构件、通用错误规避、基础预览规则。
- Project 层：当前 HSF 项目的参数、脚本、历史聊天、错题本、局部 Skill。
- Pro 层：高商业价值物件类别 Skill，例如橱柜、门窗、企业制图规范、参数化节点、BIM 标注/清单联动。

Knowledge Memory 的 Interface 应回答：“当前任务需要哪些可信知识？”而不是让调用方知道每个目录和文件格式。

### 5. 用合同测试保护 Seam

每个核心 Seam 都要有合同测试：

- HSF Source Session：保存、另存、应用脚本、参数表写入、快照。
- AI Workbench：生成、修改、解释、调试、图像路径、错题本整理。
- Preview Verification：典型 GDL 几何命令、循环、IF、PRISM、BLOCK、CYLIND。
- Knowledge Memory：Free/Project/Pro 顺序、去重、失败降级。
- Archicad Adapter：Tapir 不可用时的降级行为。

## 本轮已实施切片

本轮先加深 **AI Workbench** 内部结构：

- 新增 `ui/chat_paths.py`，承接文本和图像两条聊天执行路径。
- `ui/chat_controller.py` 回到入口调度、错题本整理、Tapir 触发分发和兼容导出。
- 现有 `run_normal_text_path` / `run_vision_path` 调用名继续可用，降低迁移风险。

这个切片不改变用户功能，但把后续“专业 GDL 自主探索一轮再生成”的复杂度放到了更合适的 Module 内，为下一轮改造 AI Workbench 留出清晰位置。
