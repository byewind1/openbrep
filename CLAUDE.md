# gdl-agent / CLAUDE.md

## 项目定位

- Python 项目：OpenBrep 的核心 Agent 运行时 + React 工作台后端。
- 负责 GDL 生成、编译、调试循环，以及 React 工作台 API 服务能力。
- **Streamlit 已于 v0.9.0 完全退役**，UI 层只有 React 工作台。
- 对外以 `localhost:8502` 提供服务，供 `openbrep-addon` 调用。

## 架构与模块职责

- 入口与主流程
  - `openbrep/core.py`：主编排流程（生成/修复/迭代）。
  - `openbrep/cli.py`：命令行入口。
- LLM 与提示词
  - `openbrep/llm.py`：多模型接口适配层。
  - `openbrep/prompts/`：系统提示、错误分析、自检提示。
- GDL 工具链
  - `openbrep/compiler.py`：编译调用。
  - `openbrep/validator.py` / `openbrep/preflight.py`：输入与前置校验。
  - `openbrep/gdl_parser.py`：GDL 解析。
  - `openbrep/gdl_previewer.py`：预览相关能力。
- React 工作台后端
  - `openbrep/workbench_api.py`：ThreadingHTTPServer，提供 `/api/*` 路由 + 静态文件服务（Tauri 模式）。
  - `openbrep/workbench/`：工作台各 service（assistant / preview / compiler / settings 等）。
  - `openbrep/workbench/view_models.py`：classify_code_blocks / classify_vision_error（域逻辑，无 UI 依赖）。
  - `openbrep/workbench/three_preview.py`：preview_3d_to_three_payload / render_three_preview_html。
  - `openbrep/local_file_dialog.py`：macOS/Tk 原生文件选择对话框。
- 数据与工程格式
  - `openbrep/hsf_project.py`：HSF 工程格式处理。
  - `openbrep/xml_utils.py`：XML 辅助处理。
  - `openbrep/paramlist_builder.py`：参数列表构建。
- 扩展机制
  - `openbrep/knowledge.py`：knowledge 加载。
  - `openbrep/skills_loader.py`：skills 加载。
  - `skills/`、`knowledge/`：可扩展内容目录。
- 运行环境与依赖
  - `openbrep/config.py`：配置加载。
  - `openbrep/dependencies.py`：依赖检测。
  - `openbrep/sandbox.py`：沙箱/隔离执行相关。
- Tauri 桌面壳（WIP）
  - `src-tauri/`：Rust Tauri v2 项目骨架。
  - `src-tauri/src/main.rs`：spawn Python 后端 → 读 OBR7_READY_URL → 打开 WebviewWindow → 关窗时调 /api/shutdown。
  - 编译需要 Rust ≥ 1.85（rustup stable，非 Homebrew）。

## 对外接口（与 openbrep-addon）

- 默认服务地址：`http://localhost:8502`
- `openbrep-addon/copilot/server.py` 会读取本项目 `config.toml`，并通过 `openbrep.llm` 等模块调用能力。
- 端口、请求格式变更属于跨项目接口变更，必须同步更新 `openbrep-addon`。

## 开发注意事项

- `config.toml` 不提交 Git。
  - 使用 `config.example.toml` 作为模板。
- 修改模型配置、消息协议或返回结构时，必须回归验证 `openbrep-addon` Copilot 面板。
- 优先保持模块边界清晰：编排层（core）不直接耦合 UI 层实现细节。
- 新增 knowledge/skills 时，确保加载路径、命名和回退逻辑稳定。

### 本地同步约定（必做）
- 默认在隔离分支/工作区开发；提交并推送到 `origin/main`（及需要时 `gitee/main`）后，必须自动执行：
  - `git -C "/Users/ren/MAC工作/工作/code/开源项目/gdl-agent" pull --ff-only origin main`
- 目标：保证用户本地主目录运行 `obr` 时立即看到最新代码，避免“已推送但本地仍旧版本”。

### 文件加载策略（上下文控制）
- `ui/app.py` 超过 100KB：除非明确涉及 UI 修改，否则默认只读“头部摘要区”（导入、常量、session_state 初始化，约前 200 行）+ 目标函数相关区块。
- 日常查询优先按函数/符号定位后分段读取，不默认全量加载 `ui/app.py`。
- 如报错栈或日志明确指向 `ui/app.py`，可跳过“只读头部”限制，按定位结果读取必要上下文。
- 需要修改 `ui/app.py` 时，必须先读完整文件再改，避免遗漏状态依赖。
- `pipeline.py` / `core.py` 允许全量加载。
- 读取顺序建议：先定位（函数名/关键字）→ 再读相关区块；不要仅依赖“前 200 行”。

### validator 架构原则
- 分层：error / warning / info 三级，只有 error 阻断流程
- 硬错误白名单（仅以下情况为 error）：
  - paramlist.xml 无法解析或为空
  - 参数名重复/类型非法
  - 3d.gdl 末尾缺少 END
- 其余全部降级为 warning，只展示不触发重写
- 跨脚本一致性检查在 `cross_script_checker.py`，不在 `validator.py`

### 自动重写策略
- `auto_rewrite = False`（当前关闭，validator 规则成熟后再开启）
- 即使开启，也只响应 error，不响应 warning
- warning 追加到 plain_text 展示给用户

### debug 模式原则
- 定位问题 → 解释根因 → 最小改动
- 必须输出完整可用脚本（用户直接注入编辑器）
- 禁止无故重写全部脚本
- 没有问题的文件不输出

## 本地运行（常用）

- 启动对外服务（示例）：`python -m uvicorn copilot.server:app --port 8502`
- 若由 `openbrep-addon` 驱动，确保本项目环境与配置可被其进程访问。

## UI 已知陷阱

> ⚠️ Streamlit 已于 v0.9.0 完全退役，以下 Streamlit 相关陷阱已归档，不再适用。

### 预览失效排查顺序（React 工作台）
1. 先用 `python3` 直接测试 `gdl_previewer`，确认 previewer 本身是否正常
2. 再检查 `openbrep/workbench/preview_service.py` 的调用参数
3. 最后检查 `openbrep/workbench/three_preview.py` 中的 payload 转换逻辑

### workbench_api.py 静态文件服务（Tauri 模式）
- `--static-dir <path>` 启动时，GET 非 `/api/` 路径会服务 `frontend/dist/`
- 没有匹配文件或路径无扩展名时，回退到 `index.html`（SPA routing）
- 注意路径遍历防护：candidate 必须在 _STATIC_DIR 下

### /api/shutdown 防死锁
- ThreadingHTTPServer 不能在请求处理线程调 `server.shutdown()`（会死锁）
- 现有实现：先 `_send({“ok”: True})` 返回响应，再启 daemon thread 调 shutdown

## 版本策略
- 0.8.x：React 工作台稳定迭代，Tauri 桌面化探索
- 0.9.0：Streamlit 完全退役，架构债务清算完成
- 1.0.0：Tauri 桌面壳正式发布，PyInstaller 打包
- 每个版本在 `docs/releases/vX.X.X.md` 记录发布说明
- README 版本历史统一用表格，不用标题+列表混排

## GDL生成质量标准

- 编译通过 ≠ 任务完成，语义正确是唯一标准
- C01–C10 benchmark是回归基准，任何修改不得降低已通过数量
- ADD/DEL stack操作必须逐行追踪，禁止假设嵌套深度
- 生成失败时输出根因分析，禁止盲目retry
- 单次生成目标：结构完整、参数有意义、几何可渲染，而非仅能编译

## 禁止事项
- 禁止提交 `config.toml`（含真实 API Key 和代理地址）
- 禁止提交 `.obsidian/`
- 禁止在 `config.example.toml` 暴露真实 key 和代理地址
- 禁止删除现有 validator 规则（只能降级为 warning，不能删）
- 禁止在没有明确问题时重写全部脚本
- 禁止把 `react-workbench` 分支合并到 main，除非：① 通过 `scripts/workbench_readiness_gate.py` 与手动冒烟验证；② 用户明确说"合并到 main"。历史教训：上次合并后被 revert（`9db2b52`）。

## ⚠️ vibe coding 行为约束

> 本项目由非程序员主导，Claude Code是执行者。以下规则防止屎山代码和技术债务累积。

### 接到任务前必须做的事

1. 收到模糊需求先问清楚：用户是谁？成功标准是什么？有没有现有代码可以复用？
2. 涉及超过50行代码或多个文件时，必须先输出计划等确认：目标/影响文件/步骤/不做的事/验证方法
3. 修改前必须先读相关文件，不能凭假设修改

### 写代码时的硬性规则

- 每个组件/模块只做一件事，不要把所有逻辑堆在一个文件里
- 公共逻辑提取到utils/或hooks/，不要复制粘贴
- 每个函数只做一件事，超过30行考虑拆分
- 关键逻辑必须加注释
- 错误必须显示给用户，禁止console.log了事或静默失败
- 禁止硬编码URL、端口、密钥——用环境变量或常量文件
- 禁止一次性修改超过3个无关文件

### OpenBrep 架构防退化规则

- 按产品 Seam 放代码，不按按钮放代码：HSF Source Session、AI Workbench、Preview Verification、Knowledge Memory、Archicad Adapter、Streamlit Shell。
- `ui/app.py` 只做装配入口；超过薄 wrapper 的逻辑必须放到 controller/service/domain。
- 不要让一个 controller 混合多个 Seam；聊天执行路径放 `ui/chat_paths.py`，聊天运行时规则放 `ui/chat_runtime.py`，聊天里的 Tapir 事件放 `ui/chat_tapir_events.py`。
- 每次拆新模块都要补最小合同测试，保护公开 Interface 和关键优先级规则。
- 优先做小而深的 Module，避免一堆只转发参数的浅 helper。

### 完成任务后必须做的事

1. 给出验证步骤（打开哪个页面，做什么操作，预期结果是什么）
2. 提示commit：`git add . && git commit -m "功能描述" && git push origin main`
3. 涉及新踩坑、架构变化、新依赖时，提示更新CLAUDE.md

### 遇到问题时的原则

- 先读错误信息定位原因，不要盲目试错
- 修了一个bug引入另一个bug，立刻告知，不要继续叠加修复
- 对技术方案不确定时，给两个选项让用户决策
- 发现现有代码潜在问题，即使不影响当前任务也要主动指出


## 配置系统

### 文件说明
- config.toml：用户本地配置，不进 git
- config.example.toml：模板，进 git，key 用占位符

### config.toml 格式（新格式，2026年3月起）
[llm]
model = "模型名"
temperature = 0.2
max_tokens = 4096

[llm.provider_keys]
zhipu    = "key"   # glm-* 系列
deepseek = "key"   # deepseek-* 系列
aliyun   = "key"   # qwen-* 系列
kimi     = "key"   # moonshot-* 系列

[[llm.custom_providers]]
name     = "my-proxy"
base_url = "https://your-proxy.com/v1"
api_key  = "your-key"
models   = ["gpt-5.4", "gpt-5.2-codex"]
protocol = "openai"   # openai | anthropic

[compiler]
path    = "/path/to/LP_XMLConverter"
timeout = 60

### 关键规则
- custom_providers 是 list[dict]，不是 dict，遍历用 for p in custom_providers
- compiler 路径字段名是 path，不是 lp_converter_path（旧格式已废弃）
- 选中 custom_providers 里的模型时，UI 隐藏 API Key 输入框
- get_provider_for_model() 匹配顺序：custom_providers → provider_keys 前缀匹配

### 前缀匹配规则
- glm- → zhipu
- deepseek- → deepseek
- qwen- / qwq- → aliyun
- moonshot- → kimi
- ollama/ → 本地直连，无需 key

## Windows 故障排除

### GSM 解包失败
- **路径验证**：确保 `config.toml` 中 `compiler.path` 指向正确的 `.exe` 文件（例如 `C:\Program Files\GRAPHISOFT\ArchiCAD 29\LP_XMLConverter.exe`）。
- **引号与空格**：路径中的空格无需引号，配置中不要额外添加引号。若路径包含空格，直接写入即可（例如 `C:\Program Files\...`）。
- **扩展名检查**：Windows 要求路径以 `.exe` 结尾，且必须是文件而非目录。
- **错误诊断**：解包失败时，错误信息会显示二进制路径、退出码及输出。若输出为空，请检查：
  - ArchiCAD 版本是否匹配（GSM 文件可能由更高版本创建）。
  - 临时目录权限（通常位于 `%TEMP%`）。
  - 防病毒软件可能拦截子进程。

### 配置示例（Windows）
```toml
[compiler]
path = "C:/Program Files/GRAPHISOFT/ArchiCAD 29/LP_XMLConverter.exe"
timeout = 60
```
建议使用正斜杠 `/` 避免转义问题，Windows 也接受。

### 手动测试 LP_XMLConverter
打开命令提示符，执行：
```cmd
"C:\Program Files\GRAPHISOFT\ArchiCAD 29\LP_XMLConverter.exe" libpart2hsf "input.gsm" "output_dir"
```
确认能否成功解包。若命令失败，请检查 ArchiCAD 安装或依赖项。

## 快速上手

### 启动
obr                          # 启动 React 工作台（obr7）
python3 scripts/obr7.py --tauri  # Tauri 单端口模式（服务 frontend/dist/）
python3 -m py_compile openbrep/config.py  # 语法检查

### 验证预览
python3 -c "
from openbrep.gdl_previewer import preview_3d_script
r = preview_3d_script('BLOCK 1,1,1\nEND')
print('meshes:', len(r.meshes))
"

### 验证配置
python3 -c "
from openbrep.config import load_config
c = load_config()
print('model:', c.llm.model)
print('custom_providers:', len(c.llm.custom_providers))
"


## 测试策略
- **测试目录与命名**：新测试统一放在 `tests/` 下，文件命名 `test_*.py`，测试用例命名 `test_*`。
- **权威测试命令（唯一门禁）**：`uv run pytest tests`。当前基线：706 passed（2026-07-06）。
  - 每次修改核心流程（`openbrep/core.py`、`openbrep/llm.py`、`openbrep/config.py`、`openbrep/runtime/pipeline.py`）后必须跑一遍，测试数量只能增不能减。
  - `python3 -m py_compile openbrep/core.py openbrep/llm.py openbrep/config.py` 作为语法快速检查，不能替代 pytest。
- **`run_tests.py` 已降级为遗留/非权威脚本**：v0.5 时代的手搓 runner，仍可跑（`uv run python run_tests.py`）但不是发布门禁。已知问题：`cli main suite` 里 2 个测试单独用 pytest 跑是绿的，只有在这个 runner 的 in-process 串行执行下才失败（怀疑是前面几十个内联测试之间的全局状态污染），暂不深挖，不要以此为由阻塞发布。
- **新增测试规则**：
  - 只要改动了输入/输出结构、模型路由、参数解析，必须新增/更新对应测试。
  - 复现 bug 后新增回归测试，再修 bug。
- **示例**：
  - `tests/test_config.py`：覆盖 `custom_providers` 解析与 `get_provider_for_model`。
  - `tests/test_llm.py`：覆盖 `api_base` 和 `protocol` 分流逻辑。

## 发布流程
- **版本号规则**：遵循当前策略（详见"版本策略"一节，当前 1.0.0 系列）。
- **版本号必须同步更新的文件**：`openbrep/__init__.py`（`__version__`）、`pyproject.toml`、`src-tauri/tauri.conf.json`、`frontend/package.json`、`CHANGELOG.md`。历史教训：v1.0.0 发布时漏改了 `openbrep/__init__.py`，导致 `__version__` 停留在上一版本超过两周才被测试捕获。
- **发布说明**：每次发布在 `docs/releases/vX.X.X.md` 记录变更要点。
- **README 版本历史**：更新 `README.md` 和 `README.zh-CN.md` 的版本表格；两个语言版本都要改，历史教训：`README.zh-CN.md` 曾整份停留在上一版本内容超过一个发布周期。
- **发布前检查**：
  - `python3 -m py_compile openbrep/config.py openbrep/llm.py`
  - `uv run pytest tests`
- **示例命令**：
  - `git tag v1.0.1`
  - `git push origin v1.0.1`

## 日志与监控
- **日志位置**：核心流程日志集中在 `openbrep/core.py` 与 `openbrep/llm.py`，UI 日志在 `ui/app.py`。
- **日志规则**：
  - 关键流程（生成/编译/预览/导入）必须有 `st.toast` 或 `st.warning` 给用户反馈。
  - 失败必须给出错误原因，禁止静默失败。
- **示例**：
  - 预览失败时显示：`st.error("预览失败：{e}")`
  - 编译成功时显示：`st.toast("✅ 编译成功")`

## 安全合规
- **敏感信息**：
  - 禁止把真实 API Key/代理地址写入 `config.example.toml`、`docs/`、`README`。
  - `config.toml` 只允许本地使用，不进 git。
- **排查清单**：
  - 提交前运行：`git diff --stat` 检查是否意外包含 `config.toml`。
  - 关键字符串搜索：`rg -n "api_key|API Key|base_url|proxy"`
- **示例**：
  - 正确：`config.example.toml` 使用占位符 `YOUR_API_KEY`。
  - 错误：在示例里出现真实 key 或私有代理域名。

## 落盘 SOP（知识沉淀规范）

**Obsidian 文档目录**：`/Users/ren/Library/Mobile Documents/iCloud~md~obsidian/Documents/库/01-Projects/dev开发/OpenBrep 开发/`

### 何时落盘
用户说"落盘"、"记录一下"、"存下来"、"整理文档"等，**立即执行，不需要用户指定目录和内容范围**。

### 落盘内容（无损整理，不能只摘重点）
- 本轮讨论的核心决策和结论
- 头脑风暴、方案对比、取舍原因
- 架构疑问与解答
- 用户的需求描述和想法
- 重要技术发现、踩坑记录

### 文件命名
- 主题文档：语义清晰的中文名，例如 `validator架构重构方案.md`
- 会话 handoff：`handoff-YYYY-MM-DD.md`

### 落盘格式要求
- 开头注明日期和背景（这次讨论的起因）
- 保留对话中有价值的问答，不要只写结论
- 末尾加「下一步」或「遗留问题」章节

---

## Handoff SOP（会话交接规范）

**每轮开发会话结束前必须写 handoff**，保存到 Obsidian 目录。目的：让下一个 AI（或未来的自己）不依赖对话上下文就能继续工作。

### Handoff 必须包含
1. **本次做了什么**：功能/修复/探索，说清楚结果
2. **未完成的事项**：具体列出，带优先级
3. **重要决策和背景**：为什么这样做，有什么约束
4. **当前状态**：代码在哪个分支、哪个 commit，有无未提交内容
5. **下一步建议**：具体可执行的行动，不要泛泛而谈
6. **相关文档**：Obsidian 里哪些文件有详细记录

### Handoff 文件位置
`/Users/ren/Library/Mobile Documents/iCloud~md~obsidian/Documents/库/01-Projects/dev开发/OpenBrep 开发/handoff-YYYY-MM-DD.md`

### AI 接手新任务时
开始工作前，先查阅：
1. 最新的 `handoff-*.md`（了解上次停在哪）
2. Obsidian OpenBrep 目录下相关主题文档（了解历史决策）
3. 再看代码，不要只靠 commit message 推断背景
