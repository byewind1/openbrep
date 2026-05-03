# OpenBrep

**OpenBrep — 面向 ArchiCAD 高阶用户和 GDL 开发者的 AI 工作台。编译验证、知识驱动、资产可追溯。**

[English](README.md) | 简体中文

> **Code Your Boundaries**

> 正式发布版本 v0.6.11 — macOS 安装包修复：补齐 Streamlit 冻结包前端资源与隐藏导入，并保留包级 smoke / browser smoke 验证能力。

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

详见 **[安装指南（INSTALL_CN.md）](INSTALL_CN.md)** — 推荐普通用户优先下载 GitHub Release 桌面包；命令行用户可选 pipx / uv；开发者再使用源码安装。

### 推荐：下载桌面包（普通用户）

访问 [GitHub Releases](https://github.com/byewind1/openbrep/releases/latest)，下载对应系统的压缩包：

- macOS：`OpenBrep-free-macOS.zip`
- Windows：`OpenBrep-free-Windows.zip`

当前 macOS 包兼容性：仅支持 Apple Silicon（`arm64`，M1/M2/M3/M4），需要 macOS 14 Sonoma 或更高版本；当前 zip 不覆盖 Intel Mac。

macOS 解压后进入 `OpenBrep` 文件夹，双击 `OpenBrep.command`；Windows 解压后运行 `OpenBrep.exe`。这种方式不要求用户先学会 `git clone`、`git pull` 或手动安装 Python 依赖。

### 命令行安装（高级用户）

正式发布到 PyPI 后，推荐用隔离工具安装：

```bash
pipx install "openbrep[ui]"
obr
```

或使用 uv：

```bash
uv tool install "openbrep[ui]"
obr
```

### 源码安装（开发者）

```bash
git clone https://github.com/byewind1/openbrep.git
cd openbrep
bash install.sh
obr
```

> 如果 `obr` 不可用，重开终端或运行 `source ~/.zshrc`
> 
> `obr` 默认仅在本机 `http://localhost:8501` 启动 UI，不会自动打开浏览器；请用你的常用浏览器手动访问或直接使用已收藏地址。

### 源码升级

```bash
cd openbrep
git pull origin main
bash install.sh   # 有新依赖时重跑，无害
obr
```

> 个人配置（config.toml / API Key）升级后保持不变，无需重新配置。

真实编译（.gsm 输出）需要 ArchiCAD 28/29。

---

## 启动

```bash
streamlit run ui/app.py
```

浏览器自动打开。侧边栏配置 LLM 模型和 API Key 后即可使用。

---

## CLI 模式

面向 GDL 开发者的终端工作流。
```bash
pip install -e "."

# 创建对象
openbrep create "做一个宽600mm深400mm的书架，4个层板" --output ./my_shelf

# 修改对象（基于磁盘上的 HSF 项目目录）
openbrep modify ./my_shelf "把层板改成6个，间距均匀分布"

# 查看帮助
openbrep --help
```

每次修改都读取完整项目状态，精确修改而非重写，自动编译验证。

> 开发者说明：如果你当前使用的是 `pip install -e "."` / `pip install -e ".[ui]"` 的 editable install，修改仓库源码后通常会立即生效，不需要重新安装；只有在变更 `pyproject.toml`、依赖、命令入口（如 `obr` / `obrcli`）或打包规则时，才建议重新安装一次。

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
| 智谱 | glm-5 / glm-4-flash | 国内可用，性价比高 |
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

面向维护者和 AI 开发工具的架构规范见：

- [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md)
- [docs/AI_DEVELOPMENT_GUIDE.zh-CN.md](docs/AI_DEVELOPMENT_GUIDE.zh-CN.md)
- 英文版：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/AI_DEVELOPMENT_GUIDE.md](docs/AI_DEVELOPMENT_GUIDE.md)

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
│   └── app.py               # Streamlit Web 界面
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

复制 `config.example.toml` 为 `config.toml`（已 .gitignore），按需填写。

### 1) 官方供应商（`provider_keys`）

```toml
[llm]
model = "glm-4-flash"
temperature = 0.2
max_tokens = 4096

[llm.provider_keys]
zhipu     = "your-zhipu-key"
anthropic = "your-claude-key"
openai    = "your-openai-key"
deepseek  = "your-deepseek-key"
google    = "your-gemini-key"
aliyun    = "your-qwen-key"
kimi      = "your-kimi-key"
```

前缀匹配规则：
- `glm-` → `zhipu`
- `deepseek-` → `deepseek`
- `claude-` → `anthropic`
- `gemini-` → `google`
- `qwen-` / `qwq-` → `aliyun`
- `moonshot-` → `kimi`
- `gpt-` / `o1` / `o3` / `o4` → `openai`
- `ollama/` → 本地模式，不需要 API Key

### 2) 自定义 provider（推荐对象写法）

```toml
[llm]
model = "ymg-gpt-5.3-codex"
api_key = "YOUR_YMG_KEY"
api_base = "https://api.ymg.com/v1"

[[llm.custom_providers]]
name = "ymg"
protocol = "openai"  # openai | anthropic
base_url = "https://api.ymg.com/v1"
api_key = "YOUR_YMG_KEY"

[[llm.custom_providers.models]]
alias = "ymg-gpt-5.3-codex"   # UI 里选择的名字
model = "gpt-5.3-codex"       # 实际请求给 provider 的模型名
```

### 3) 路由优先级（重要）

请求时模型与凭据解析顺序：
1. `custom_providers`（优先按 alias/model 命中）
2. `provider_keys`（按模型前缀匹配）
3. `[llm]` 顶层 `api_key` / `api_base`（兜底）

也就是说：命中 custom provider 时，会优先使用该 provider 的 `api_key/base_url/protocol`。

### 4) 常见坑

- 只写 `models = ["ymg-gpt-5.3-codex"]`，不写 `{alias, model}` 对象，容易 alias 和真实模型混淆。
- `base_url` 不是 OpenAI 兼容入口（常见是缺 `/v1`）。
- 切换模型后未确认 `[llm].model/api_key/api_base` 是否与当前 provider 成组一致。

### 5) 编译器

```toml
[compiler]
path = "/Applications/GRAPHISOFT/Archicad 29/.../LP_XMLConverter"
```

---

## 文档

- **[用户手册 →](docs/manual.md)** — UI 每个功能的详细说明、工作流、常见问题
- **[安装指南 →](INSTALL_CN.md)** — Python 设置、GitHub 访问（VPN/代理/镜像）、依赖安装、LLM 配置、troubleshooting

---

## 版本历史

| 版本 | 主要内容 |
|---|---|
| v0.6.11 | macOS 安装包修复：补齐 Streamlit 冻结包的前端静态资源与 `streamlit.runtime.scriptrunner` 隐藏导入；新增浏览器级包验证脚本，确保不仅 health 通过，首页和脚本执行也通过（见 docs/releases/v0.6.11.md） |
| v0.6.10 | macOS 安装包修复：打包启动器显式关闭 Streamlit `global.developmentMode`，避免 `server.port` 冲突；保留包级 smoke 验证入口，便于直接验证 Release zip（见 docs/releases/v0.6.10.md） |
| v0.6.9 | 安装包验证补丁：打包启动器支持固定端口与禁用自动开浏览器，新增 `scripts/package_smoke.py`，用于下载 Release zip 后直接运行包内启动器并验证 Streamlit health，不依赖本地 `obr`（见 docs/releases/v0.6.9.md） |
| v0.6.8 | macOS 安装包修复：打包启动器改为进程内启动 Streamlit，避免冻结包递归拉起自身；macOS zip 增加 `OpenBrep.command` 和启动说明；`openbrep[ui]` 明确包含 UI 导入所需依赖（见 docs/releases/v0.6.8.md） |
| v0.6.7 | UI 收尾小版本：隐藏版本管理与 Archicad 实机联动入口，收敛 HSF 保存 / 另存为为直达动作，清理多余提示与低频操作，进一步降低工作台认知负担（见 docs/releases/v0.6.7.md） |
| v0.6.6 | UI 清爽化小版本：拆分打开文件/HSF 项目入口，简化编译 GSM 和输出目录选择，移除低价值调试按钮与冗余提示，优化 macOS 原生文件选择器激活体验（见 docs/releases/v0.6.6.md） |
| v0.6.5 | 安装体验补丁：修正 GitHub Release 自动发布命令，改用 `gh release create --generate-notes` 兼容 `--repo`，用于接续 v0.6.4 的安装包发布自动化（见 docs/releases/v0.6.5.md） |
| v0.6.4 | 安装体验小版本：普通用户首选 GitHub Release 桌面包；修正 macOS/Windows installer workflow 产物路径并在 tag 构建后自动创建/更新 GitHub Release；补充 pipx / uv / git clone 分层安装说明（见 docs/releases/v0.6.4.md） |
| v0.6.3 | 新增个人工作空间记忆：持久化聊天记录、GDL 错题本、用户触发整理后的自我提升 skill；LLM 注入分层为用户工作空间记忆与源码开发者基线；优化 HSF 项目目录持久化、编译版本识别、自定义 provider 配置同步、参数单位文案与 UI 架构治理（见 docs/releases/v0.6.3.md） |
| v0.6.2 | 新增 wiki 知识检索与问答链路；新增用户自定义 flat 知识库接入；新增 skill creator 对话式创建与列表路由；补齐 pipeline/knowledge/skill 相关测试并修复 chat 关键路由细节（见 docs/releases/v0.6.2.md） |
| v0.6.1 | CLI 可用性与安装体验提升；新增/完善 GDL 静态检查与自动 repair；补强 chat / explainer / 图片链路与参考图生成；修复 obr 在非项目目录无法启动 UI（见 docs/releases/v0.6.1.md） |
| v0.6.0 | Runtime Phase 1 主骨架正式发布收尾：统一 create / modify / repair / chat 主链路；repair 独立 intent 闭合；CLI/UI/runtime 版本与发布口径统一（见 docs/releases/v0.6.0.md） |
| v0.5.7 | CLI 模式正式可用（create/modify 命令）；生成过程支持用户取消；pipeline 改进解决越改越差问题；参数路由修复（见 docs/releases/v0.5.7.md） |
| v0.5.6 | 图片上传不再卡死；cross-script / static checker 误报减少；AI 助手设置支持长期保存并注入系统提示；自定义代理与官方供应商分层选择，并优先显示代理名（见 docs/releases/v0.5.6.md） |
| v0.5.5 | UI重构为四栏布局：左栏操作+预览、中栏脚本+参数表、右栏AI对话（见 docs/releases/v0.5.5.md） |
| v0.5.4 | validator 分层重构：error/warning 分开，硬错误白名单收紧；跨脚本检查器；debug 最小改动；生成过程实时显示；生成中禁用 widget（见 docs/releases/v0.5.4.md） |
| **v0.5.3** | 知识库升级与文档品牌增强：整合拆书增量（含 2D 高级与可编译示例）、新增命令索引与命令级精准路由、README 顶部加入 logo（见 `docs/releases/v0.5.3.md`）。 |
| **v0.5.2** | 版本标注与发布归档规范化：UI 版本号统一读取代码版本；README 标题去版本；新增发布说明文档（见 `docs/releases/v0.5.2.md`） |
| **v0.5** | **OpenBrep 品牌发布** — 项目更名为 OpenBrep；稳定版本发布；Gitee 镜像支持（国内用户快速访问） |
| v0.5 pre | 统一编辑器 UI；**图片即意图**（上传图片 → AI 生成 GDL）；AI 对话修改脚本；确认写入流程；paramlist.xml 自动注入；GSM 导入（AC29）；streamlit-ace 语法高亮；全屏编辑；多模型支持 |
| v0.4.0 | HSF-native 架构重构；Streamlit Web UI；强类型 paramlist；44 项单元测试 |
| v0.3.x | GDL 解析器；Context surgery；Preflight |
| v0.2.0 | Anti-hallucination；Golden snippets |
| v0.1.0 | Core agent loop |

---

## License

MIT — see [LICENSE](LICENSE).
