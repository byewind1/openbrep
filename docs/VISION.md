# openbrep 项目愿景与路线图

# Project Vision & Roadmap

---

## 一句话定位 / Elevator Pitch

**openbrep** 是建筑行业第一个 AI 驱动的 ArchiCAD GDL 库对象开发工具——让不会编程的建筑师也能通过自然语言创建参数化 BIM 构件。

**openbrep** is the first AI-powered ArchiCAD GDL library object development tool for the architecture industry — enabling architects to create parametric BIM components through natural language, without coding skills.

---

## 我们要解决什么问题 / The Problem

### 现状：GDL 开发是 BIM 最大的瓶颈

ArchiCAD 的库对象系统（GDL）是业内最强大的参数化设计语言之一，但它也是最不友好的：

- **语法古老** — 基于 1980 年代 BASIC 方言，没有现代 IDE 支持
- **学习曲线陡峭** — 建筑师需要同时掌握 3D 几何数学、参数逻辑、UI 设计、XML 格式
- **调试体验极差** — 错误提示含糊，没有断点调试，只能在 ArchiCAD 里反复试
- **开发效率低** — 一个中等复杂度的库对象（如衣柜）需要 2-5 天手工开发
- **人才稀缺** — 全球精通 GDL 的开发者可能不超过几千人

结果是：大多数建筑事务所要么买昂贵的第三方库，要么用通用对象凑合，要么完全放弃参数化。

### 我们的判断

AI 大模型已经能理解 GDL 语法并生成可编译的代码。缺的是一个**专业的工程化框架**——把 AI 的代码生成能力变成建筑师真正能用的工具。

---

## 核心理念 / Core Principles

### 1. 建筑师优先，不是程序员优先

用户不需要懂 Python、不需要用 Terminal、不需要理解 XML Schema。打开网页，用自然语言描述需求，拿到 .gsm 文件，拖进 ArchiCAD。

### 2. HSF 原生，不是 XML 拼接

v0.4 的核心决策：抛弃 XML 单体文件，全面拥抱 HSF（Hierarchical Source Format）。这不仅是格式选择，而是架构哲学——文件系统即数据结构，每个脚本独立存在，天然支持 AI 的 Context Surgery。

### 3. 知识驱动，不是通用对话

GDL 有太多隐式规则（Material 必须是整数索引、UTF-8 BOM 编码、IF/ENDIF 必须配对...）。通用 AI 不知道这些。openbrep 通过 `knowledge/` 和 `skills/` 文档将领域知识注入 AI 上下文，让每次代码生成都站在专家经验之上。

### 4. 编译即验证

AI 写的代码不可信——必须通过 LP_XMLConverter 真实编译验证。失败了自动带错误信息重试，最多 N 次，直到编译通过或明确告知用户问题所在。

---

## 产品架构 / Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Streamlit Web UI                     │
│  ┌──────────┬──────────┬──────────┬──────┬────────┐ │
│  │ 💬 AI    │ 🏗️ 创建  │ 📝 编辑  │ 🔧   │ 📋     │ │
│  │   对话    │  /导入   │         │ 编译  │ 日志   │ │
│  └────┬─────┴──────────┴────┬────┴──┬───┴────────┘ │
│       │                     │       │               │
│  ┌────▼─────────────────────▼───┐   │               │
│  │       GDL Agent Core         │   │               │
│  │  ┌─────────┐  ┌───────────┐  │   │               │
│  │  │ LLM     │  │ Knowledge │  │   │               │
│  │  │ Adapter │  │ + Skills  │  │   │               │
│  │  └────┬────┘  └─────┬─────┘  │   │               │
│  │       └──────┬──────┘        │   │               │
│  │         ┌────▼────┐          │   │               │
│  │         │ HSF     │          │   │               │
│  │         │ Project │◄─────────┼───┘               │
│  │         └────┬────┘          │                   │
│  └──────────────┼───────────────┘                   │
│            ┌────▼────┐                              │
│            │Compiler │ ← LP_XMLConverter            │
│            └────┬────┘                              │
│                 ▼                                   │
│            📦 .gsm → ArchiCAD                       │
└─────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|:---|:---|:---|
| **HSF 项目模型** | `hsf_project.py` | HSF 目录的内存表示，参数类型自动纠错，UTF-8 BOM 编码 |
| **参数生成器** | `paramlist_builder.py` | 强类型 paramlist.xml 生成/解析/校验 |
| **GDL 解析器** | `gdl_parser.py` | .gdl 源码 → HSFProject，支持中英文 Section 头 |
| **编译器** | `compiler.py` | LP_XMLConverter 封装（hsf2libpart / libpart2hsf） |
| **Agent 核心** | `core.py` | 自然语言 → LLM 生成 → 编译 → 验证 → 重试循环 |
| **LLM 适配** | `llm.py` | 统一接口，支持 Claude / GPT / GLM / DeepSeek / Ollama |
| **知识库** | `knowledge.py` | GDL 参考文档加载器 |
| **技能策略** | `skills_loader.py` | 任务类型检测 + 策略文档加载 |
| **Web 界面** | `ui/app.py` | Streamlit 五 Tab 界面，AI 对话 + 手动编辑 + 编译 |

---

## 开发路线图 / Roadmap

### Phase 1: Foundation ✅ 已完成（v0.1 → v0.4.1）

- [x] Agent 核心循环（生成 → 编译 → 重试）
- [x] Anti-hallucination 机制（参数类型自动纠错、Golden Snippets）
- [x] HSF 原生架构（抛弃 XML 单体文件）
- [x] LP_XMLConverter 真实编译集成
- [x] GDL 源码解析器（.gdl → HSFProject）
- [x] paramlist.xml 强类型生成器
- [x] Streamlit Web UI（5 Tab：AI 对话 / 创建导入 / 编辑 / 编译 / 日志）
- [x] 多模型支持（Claude / GPT / GLM-4.7 / DeepSeek / Ollama）
- [x] 47 项自动化测试
- [x] 真实 .gsm 编译验证通过

### Phase 2: 可用性提升（v0.5 → v0.6）

- [ ] `config.json` 配置系统（API Key + 编译器路径持久化，不再每次启动填写）
- [ ] Knowledge 文档自动加载（读取 `docs/GDL_01-07.md` 注入 AI 上下文）
- [ ] Skills 策略智能匹配（根据用户指令自动选择最相关的策略文档）
- [ ] AI 对话结果保存到本地（"保存为 .gdl" 按钮）
- [ ] GDL 语法高亮编辑器（关键字着色、括号匹配）
- [ ] GDL 静态检查（IF/ENDIF 配对、未定义变量警告、行号级错误提示）
- [ ] 批量参数重命名（改名自动同步到所有脚本）
- [ ] .gsm 导入（LP_XMLConverter libpart2hsf → HSFProject → 编辑 → 重新编译）

### Phase 3: 智能增强（v0.7 → v0.8）

- [ ] 3D 简易预览（BLOCK / PRISM_ / CYLINDER 基本体 → three.js 或 trimesh 渲染）
- [ ] 对话历史记忆（跨 session 记住项目上下文）
- [ ] 多对象项目管理（一个 workspace 管理多个 .gsm）
- [ ] CALL 宏依赖分析（自动检测和管理宏调用关系）
- [ ] ArchiCAD Libpacks 支持（AC28+ 的新库管理格式）
- [ ] GDL 代码自动补全（基于 knowledge 文档的上下文补全）

### Phase 4: 生态与协作（v1.0+）

- [ ] ArchiCAD JSON API 集成（无头渲染：自动导入 .gsm → 放置 → 截图预览）
- [ ] 模板库（预置常用建筑构件模板：门窗 / 家具 / 栏杆 / 幕墙节点）
- [ ] 社区共享平台（用户上传/下载 GDL 对象和 Skills 策略）
- [ ] 团队协作（Git 集成 + 版本对比 + 代码审查）
- [ ] IFC 属性映射（自动生成 Properties Script 用于算量和 BIM 信息交换）
- [ ] 多语言 UI（英文 / 中文 / 日文 / 德文）

---

## 技术栈 / Tech Stack

| 层级 | 技术 | 选型理由 |
|:---|:---|:---|
| 前端 | Streamlit | Python 生态，建筑师零前端门槛 |
| 后端 | Python 3.10+ | 建筑行业最普及的脚本语言 |
| AI 接口 | litellm | 一个库接入所有 LLM 厂商 |
| 编译器 | LP_XMLConverter | ArchiCAD 官方工具，唯一能编译 GDL 的引擎 |
| 格式 | HSF | ArchiCAD 原生文本格式，Git 友好，AI 友好 |
| 测试 | pytest / 自定义 runner | 47 项自动化测试，Mock 编译器支持无 ArchiCAD 测试 |

---

## 竞品对比 / Competitive Landscape

| | openbrep | 手写 GDL | Param-O | ArchiCAD 内置编辑器 |
|:---|:---|:---|:---|:---|
| AI 辅助 | ✅ 核心能力 | ❌ | ❌ | ❌ |
| 自然语言创建 | ✅ | ❌ | ❌ | ❌ |
| 自动编译验证 | ✅ | 手动 | 手动 | 手动 |
| 参数类型校验 | ✅ 自动纠错 | ❌ | 部分 | 部分 |
| 多模型支持 | ✅ 15+ 模型 | N/A | N/A | N/A |
| 开源 | ✅ MIT | N/A | ❌ | ❌ |
| 价格 | 免费 + LLM API 费用 | 免费 | 付费 | ArchiCAD 内置 |

---

## 目标用户 / Target Users

1. **建筑师** — 不会编程，想用自然语言创建自定义 BIM 构件
2. **BIM 经理** — 需要快速定制事务所标准库对象
3. **GDL 开发者** — 已经会写 GDL，想用 AI 加速开发效率
4. **建筑院校** — GDL 教学辅助工具

---

## 商业模式构想 / Business Model Ideas

openbrep 本身开源免费（MIT License）。可能的商业化方向：

- **托管服务** — 预配置的云端版本，免去安装和配置（SaaS 月费）
- **企业定制** — 为大型事务所定制 knowledge/skills + 私有模板库
- **模板市场** — 高质量 GDL 模板的付费下载平台
- **培训服务** — AI + BIM 工作流培训课程

---

## 当前状态 / Current Status（v0.4.1）

- 🟢 HSF 原生架构：稳定
- 🟢 LP_XMLConverter 编译：通过（ArchiCAD 29 验证）
- 🟢 47 项自动化测试：全部通过
- 🟢 Streamlit Web UI：可用
- 🟡 AI 对话：功能可用，knowledge 上下文待接入
- 🟡 配置系统：每次启动需手动填写
- 🔴 3D 预览：未实现
- 🔴 ArchiCAD API 集成：未实现

---

## 参与贡献 / Contributing

欢迎以下形式的贡献：

- **GDL Knowledge 文档** — 完善 `docs/` 下的 GDL 参考文档
- **Skills 策略** — 编写任务策略模板（如何创建门窗、如何处理幕墙节点）
- **Bug 报告** — 在 Issues 中报告编译错误或解析问题
- **测试用例** — 提供真实的 .gdl / .gsm 文件用于测试
- **代码贡献** — Fork → Branch → PR

---

## 联系方式 / Contact

- GitHub: [github.com/byewind/openbrep](https://github.com/byewind/openbrep)
- 作者: 朗朗晴空
- 邮箱: [待填]

---

*"让每个建筑师都能创建自己的参数化构件——这不是技术问题，是工具问题。"*

*"Every architect should be able to create their own parametric components — it's not a skill gap, it's a tooling gap."*
