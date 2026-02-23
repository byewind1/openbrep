# OpenBrep v0.5

[简体中文](README.zh-CN.md) | English

**用自然语言驱动 ArchiCAD GDL 库对象的创建、修改与编译。**

> **OpenBrep: Code Your Boundaries**

> 稳定发布版本 — 核心功能完整，适合建筑师日常 GDL 开发工作。

---

## 问题与解法

你用 AI 写了一段 GDL 代码，想在 ArchiCAD 里测试。传统路径：

```
打开库对象编辑器 → 手动填参数 → 切 5 个 Script 窗口 → 粘代码 → 编译
```

**openbrep 把这个流程压缩到：**

```
描述需求（中文/英文皆可）→ AI 生成并填入脚本框 → 一键编译 → .gsm 拖入 ArchiCAD
```

或者导入已有 .gsm 文件，让 AI 帮你 debug、重构、加参数。

---

## 安装

```bash
# GitHub (国际用户)
git clone https://github.com/byewind1/openbrep.git
# 或 Gitee (国内用户)
git clone https://gitee.com/byewind/openbrep.git

cd openbrep

# 安装核心 + Web 界面依赖
pip install -e ".[ui]"
```

需要 Python 3.10+。真实编译（.gsm 输出）需要安装 ArchiCAD 28/29。

---

## 启动

```bash
streamlit run ui/app.py
```

浏览器自动打开。侧边栏配置 LLM 模型和 API Key 后即可使用。

---

## 功能一览

### 编辑器栏（左侧）

| 功能 | 说明 |
|---|---|
| 📂 **导入** | 拖入 `.gdl` / `.txt` / `.gsm` 文件；.gsm 经 LP_XMLConverter 解包为 HSF |
| 🔧 **编译 GSM** | HSF → .gsm，支持 Mock 模式（无需 ArchiCAD）和真实 LP_XMLConverter 编译 |
| 📥 **提取** | 从 AI 对话中扫描代码块，自动识别脚本类型（3D/2D/Param...）并写入编辑器 |
| **脚本标签页** | 6 个独立脚本框（3D / 2D / Master / Param / UI / Properties），每个均支持 streamlit-ace 语法高亮和全屏编辑 |
| **参数表** | 查看、手动添加参数；AI 生成的 paramlist.xml 可一键写入 |
| 🔍 **语法检查** | IF/ENDIF、FOR/NEXT、ADD/DEL 匹配，3D 末尾 END，2D 必须有 PROJECT2 |

### AI 对话栏（右侧）

| 功能 | 说明 |
|---|---|
| **🖼️ 图片即意图** | 上传建筑构件图片 → AI 识别几何、提取参数化维度 → 直接生成 GDL 脚本，无需文字描述 |
| **自然语言创建** | "做一个宽 600mm 深 400mm 的书架，4 个层板" → 自动生成全部脚本和参数 |
| **自然语言修改** | 已有项目时："把层板改成 5 个，材质加一个 shelfMat 参数" → AI 理解上下文按需修改 |
| **Debug 模式** | 包含 "为什么"/"检查"/"修复" 等词时，自动注入全部脚本上下文；AI 可以给出分析文字 + 代码修复 |
| **确认写入** | 已有项目的 AI 修改不会自动覆盖，消息下方出现 [✅ 写入] [❌ 忽略] 按钮 |
| **对话操作栏** | 每条 AI 消息下方：👍 👎 📋 🔄（好评/差评/复制/重新生成） |
| **多模型支持** | Claude / GLM / GPT / DeepSeek / Gemini / Ollama 本地，侧边栏切换 |

---

## 支持的 LLM

| 提供商 | 模型 | 说明 |
|---|---|---|
| Anthropic | claude-haiku / sonnet / opus | 推荐首选 |
| 智谱 | glm-4.7 / glm-4.7-flash | 国内可用，性价比高 |
| OpenAI | gpt-4o / gpt-4o-mini / o3-mini | |
| DeepSeek | deepseek-chat / deepseek-reasoner | |
| Google | gemini-2.5-flash / pro | |
| Ollama | qwen2.5 / qwen3 / deepseek-coder | 本地，无需 API Key |

---

## GSM 导入（AC29 支持）

侧边栏选择 LP_XMLConverter 模式，配置路径后可导入 .gsm 文件进行修改：

```
# ArchiCAD 29 路径（LP_XMLConverter 内嵌于 app bundle）
/Applications/GRAPHISOFT/Archicad 29/Archicad 29.app/Contents/MacOS/
  LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter
```

也可直接在 `config.toml` 中写入，启动后自动读取。

---

## HSF 格式简介

.gsm 文件解压后是这样的目录结构（HSF）：

```
MyBookshelf/
├── libpartdata.xml     ← 对象身份（GUID、版本）
├── paramlist.xml       ← 参数定义（强类型）
├── ancestry.xml        ← 对象分类
└── scripts/
    ├── 1d.gdl          ← Master Script
    ├── 2d.gdl          ← 2D 平面符号
    ├── 3d.gdl          ← 3D 几何模型
    ├── vl.gdl          ← 参数逻辑（VALUES/LOCK）
    └── ui.gdl          ← 自定义界面
```

openbrep 以 HSF 为原生格式，每个脚本独立处理，AI 只读取与当前任务相关的脚本（减少 context 占用）。

---

## 项目结构

```
openbrep/
├── openbrep/
│   ├── hsf_project.py       # HSF 数据模型
│   ├── paramlist_builder.py # paramlist.xml 强类型生成
│   ├── gdl_parser.py        # .gdl → HSFProject
│   ├── compiler.py          # LP_XMLConverter 封装
│   ├── core.py              # Agent 主循环 + generate_only
│   ├── llm.py               # 多模型统一接口
│   ├── knowledge.py         # 知识库加载
│   └── skills_loader.py     # 任务策略加载
├── ui/
│   └── app.py               # Streamlit Web 界面（v0.5）
├── knowledge/               # GDL 参考文档（可自行扩充）
├── skills/                  # 任务策略（可自行扩充）
├── docs/
│   └── manual.md            # 详细用户手册
├── tests/                   # 单元测试
├── config.example.toml
└── pyproject.toml
```

---

## 配置

复制 `config.example.toml` 为 `config.toml`（已 .gitignore），按需填写：

```toml
[llm]
model = "glm-4.7"

[llm.provider_keys]
zhipu     = "your-zhipu-key"
anthropic = "your-claude-key"
openai    = "your-openai-key"
deepseek  = "your-deepseek-key"

[compiler]
path = "/Applications/GRAPHISOFT/Archicad 29/..."
```

---

## 文档

- **[用户手册 →](docs/manual.md)** — UI 每个功能的详细说明、工作流、常见问题

---

## 版本历史

| 版本 | 主要内容 |
|---|---|
| **v0.5** | **OpenBrep 品牌发布** — 项目更名为 OpenBrep；稳定版本发布；Gitee 镜像支持（国内用户快速访问） |
| v0.5 pre | 统一编辑器 UI；**图片即意图**（上传图片 → AI 生成 GDL）；AI 对话修改脚本；确认写入流程；paramlist.xml 自动注入；GSM 导入（AC29）；streamlit-ace 语法高亮；全屏编辑；多模型支持 |
| v0.4.0 | HSF-native 架构重构；Streamlit Web UI；强类型 paramlist；44 项单元测试 |
| v0.3.x | GDL 解析器；Context surgery；Preflight |
| v0.2.0 | Anti-hallucination；Golden snippets |
| v0.1.0 | Core agent loop |

---

## License

MIT — see [LICENSE](LICENSE).
