# gdl-agent v0.4

**AI-powered ArchiCAD GDL library object builder — HSF-native.**

**用 AI 自动创建和修改 ArchiCAD GDL 库对象 — 基于 HSF 原生格式。**

> ⚠️ **Alpha** — 44 unit tests passing, HSF compilation pending real LP_XMLConverter validation.

---

## 30 秒说明白 / In 30 Seconds

你用 AI（Claude/GPT/GLM）写了一段 GDL 代码，想在 ArchiCAD 里测试。传统方式：打开库对象编辑器 → 手动填 10 个参数 → 切 5 个 Script 编辑器 → 粘贴代码 → 编译。

**gdl-agent 自动完成这一切。**

```
你的 .gdl 文件 ──→ gdl-agent ──→ .gsm（直接拖进 ArchiCAD 测试）
```

或者在 Web 界面里对话修改：

```
"给书架加一个材质参数 shelfMat" ──→ gdl-agent ──→ 修改后的 .gsm
```

## 什么是 HSF？为什么用它？ / What is HSF?

HSF（Hierarchical Source Format）是 ArchiCAD 的文本化库对象格式。一个 .gsm 文件"解压"后变成这样的目录：

```
MyBookshelf/
├── libpartdata.xml      ← 对象身份证（GUID、版本号）
├── paramlist.xml        ← 参数定义表（强类型）
├── ancestry.xml         ← 对象分类（家具/门窗/...）
└── scripts/
    ├── 1d.gdl           ← Master Script
    ├── 2d.gdl           ← 2D 平面符号
    ├── 3d.gdl           ← 3D 几何模型
    ├── vl.gdl           ← 参数逻辑（VALUES/LOCK）
    └── ui.gdl           ← 自定义界面
```

**HSF vs 单体 XML：**

| | 单体 XML (v0.3) | HSF 目录 (v0.4) |
|:---|:---|:---|
| Context 效率 | 整个 XML 塞给 LLM | 只喂相关的 .gdl 文件 |
| 错误定位 | 行号经常对不上 | 直接指向具体脚本文件 |
| Git | 一个文件的 diff 不可读 | 每个脚本独立 diff |
| 参数类型 | 容易写错 | paramlist.xml 强类型校验 |

## 安装 / Install

```bash
git clone https://github.com/byewind/gdl-agent.git
cd gdl-agent
pip install -e .

# 如果要用 Web 界面
pip install -e ".[ui]"
```

## 快速开始 / Quick Start

### 方式 1: Web 界面（推荐建筑师使用）

```bash
streamlit run ui/app.py
```

浏览器自动打开。四步完成：

1. **创建/导入** — 拖入 .gdl 文件或创建新对象
2. **编辑** — 查看参数、修改脚本
3. **编译** — 点击编译按钮，Mock 模式无需 ArchiCAD
4. **测试** — 真实编译后得到 .gsm，拖进 ArchiCAD

### 方式 2: Python 代码

```python
from gdl_agent.hsf_project import HSFProject, ScriptType
from gdl_agent.gdl_parser import parse_gdl_file
from gdl_agent.compiler import MockHSFCompiler

# 从 .gdl 文件创建项目
project = parse_gdl_file("examples/Bookshelf.gdl")

# 或从零创建
project = HSFProject.create_new("MyShelf", work_dir="./workspace")

# 查看项目
print(project.summary())

# 保存 HSF 到磁盘
project.save_to_disk()

# 编译（Mock 模式）
compiler = MockHSFCompiler()
result = compiler.hsf2libpart("./workspace/MyShelf", "./output/MyShelf.gsm")
print(f"Success: {result.success}")
```

## 项目结构 / Project Structure

```
gdl-agent/
├── gdl_agent/                    # 核心包
│   ├── hsf_project.py            #   HSF 项目数据模型（核心）
│   ├── paramlist_builder.py      #   paramlist.xml 强类型生成器
│   ├── gdl_parser.py             #   .gdl → HSFProject 解析器
│   ├── compiler.py               #   LP_XMLConverter 封装 (hsf2libpart)
│   ├── core.py                   #   Agent 主循环
│   ├── llm.py                    #   LLM 统一接口
│   ├── knowledge.py              #   知识库加载
│   ├── skills_loader.py          #   Skills 加载器
│   ├── snippets.py               #   GDL 代码模板
│   ├── dependencies.py           #   CALL 宏解析
│   ├── preflight.py              #   预检分析
│   ├── config.py                 #   配置管理
│   └── prompts/                  #   LLM Prompt 模板
├── ui/
│   └── app.py                    #   Streamlit Web 界面
├── knowledge/                    #   知识库（接口，用户填充）
│   ├── README.md
│   └── GDL_quick_reference.md
├── skills/                       #   Skills（接口，用户填充）
│   ├── README.md
│   └── _example_create_object.md
├── examples/
│   └── Bookshelf.gdl
├── docs/
├── tests/
├── run_tests.py                  #   44 项测试
├── pyproject.toml
└── README.md
```

## 核心模块说明 / Core Modules

### `hsf_project.py` — HSF 数据模型

整个项目的核心。将 HSF 目录结构映射为 Python 对象。

关键特性：
- **参数类型自动纠正**：LLM 写 `Float` 自动改成 `RealNum`，写 `Bool` 改成 `Boolean`
- **UTF-8 BOM 强制**：所有文件写入时自动加 BOM（LP_XMLConverter 硬性要求）
- **意图路由**：根据用户指令自动判断影响哪些脚本（"改材质" → 只加载 3d.gdl）

### `paramlist_builder.py` — 参数强类型生成器

生成 LP_XMLConverter 能接受的 paramlist.xml。

- CDATA 包裹 Description（必须）
- 10 种合法类型标签（Length/Integer/Boolean/RealNum/...）
- 校验器：重复参数名、Boolean 值非 0/1、保留参数类型错误

### `compiler.py` — 编译器封装

- `hsf2libpart`: HSF 目录 → .gsm
- `libpart2hsf`: .gsm → HSF 目录（反向解压，用于修改旧对象）
- `MockHSFCompiler`: 无需 ArchiCAD 的测试编译器（验证目录结构、GDL 语法）

### `core.py` — Agent 主循环

```
ANALYZE → GENERATE → COMPILE → (成功 → 结束) / (失败 → 重试)
```

- Context surgery 内置于 HSF 结构（每个脚本是独立文件）
- Anti-loop 检测（LLM 输出相同内容时停止）
- 事件回调（可接入 UI 显示进度）

## knowledge/ 与 skills/ — 你的核心资产

`knowledge/` 存放 GDL 参考文档（语法、XML 格式、常见错误），`skills/` 存放任务策略（如何创建对象、如何修复错误）。

**这两个目录是让 Agent 真正好用的关键。** 项目只提供骨架和格式说明，你需要根据自己的 ArchiCAD 版本和编码经验填充。

详见 [knowledge/README.md](knowledge/README.md) 和 [skills/README.md](skills/README.md)。

## 测试 / Testing

```bash
# 44 项测试，无需任何外部依赖
python run_tests.py

# 覆盖范围：
# - HSF 项目创建/保存/加载/参数操作 (15 tests)
# - paramlist.xml 生成/解析/校验 (8 tests)
# - Mock 编译器 (6 tests)
# - GDL 解析器 (5 tests)
# - Agent 主循环 (6 tests)
# - Skills 加载器 (4 tests)
```

## 配置 LP_XMLConverter / Setup

### macOS

```bash
# ArchiCAD 28 默认路径
# /Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter

# 验证
/Applications/GRAPHISOFT/ArchiCAD\ 28/LP_XMLConverter --version
```

### Windows

```
# 默认路径
C:\Program Files\GRAPHISOFT\ArchiCAD 28\LP_XMLConverter.exe
```

在 Web 界面的侧边栏或 config.toml 中配置路径。

## 版本历史 / Versions

- **v0.4.0** — HSF-native 架构重构，Streamlit Web UI，强类型 paramlist，44 项测试
- **v0.3.1** — GDL 解析器，drag & drop .gdl → XML
- **v0.3.0** — Sandbox，Context surgery，Preflight
- **v0.2.0** — Anti-hallucination，Golden snippets
- **v0.1.0** — Core agent loop

## License

MIT — see [LICENSE](LICENSE).
