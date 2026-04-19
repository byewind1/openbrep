# gdl-agent / CLAUDE.md

## 项目定位

- Python 项目：OpenBrep 的核心 Agent 运行时。
- 负责 GDL 生成、编译、调试循环，以及 Streamlit/UI 与 API 服务能力。
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

### Streamlit widget 废弃参数
当前环境 streamlit==1.54.0，以下写法已废弃会导致静默失效：
- `st.plotly_chart(fig, width='stretch')` → 改为 `use_container_width=True`
- `st.image(..., use_column_width=...)` → 改为 `use_container_width=True`
- `st.button(..., use_container_width=...)` 仍有效

每次修改 `ui/app.py` 涉及 widget 时，检查是否有废弃参数。

### 预览失效排查顺序
1. 先用 `python3` 直接测试 `gdl_previewer`，确认 previewer 本身是否正常
2. 再检查 UI 调用层参数是否正确传入
3. 最后检查 Streamlit widget 废弃参数

### Streamlit 编辑器缓冲区同步
- 编辑器内容修改后不会自动同步回 `st.session_state.project`
- 任何需要读取“当前脚本”的操作（预览、编译、导出）前，
  必须先调用 `_sync_visible_editor_buffers()` 同步缓冲区
- 否则会拿到旧脚本或空脚本，表现为“功能失效”但无报错

### 生成中禁用 widget
- `agent_running = True` 时禁用：侧边栏模型选择、API Key、工作目录、编译/导入/提取按钮
- 用 `try/finally` 保证异常时也能解锁

## 版本策略
- 0.5.x：当前功能集稳定迭代，内部架构优化
- 0.6.0：生成质量有可测量突破（编译通过率提升）
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

## 快速上手

### 启动
obr                          # 启动 Streamlit UI
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
- **最小回归集**：每次修改核心流程（`openbrep/core.py`、`openbrep/llm.py`、`openbrep/config.py`、`ui/app.py`）至少跑：
  - `python3 -m py_compile openbrep/core.py openbrep/llm.py openbrep/config.py ui/app.py`
  - `python3 run_tests.py`
- **新增测试规则**：
  - 只要改动了输入/输出结构、模型路由、参数解析，必须新增/更新对应测试。
  - 复现 bug 后新增回归测试，再修 bug。
- **示例**：
  - `tests/test_config.py`：覆盖 `custom_providers` 解析与 `get_provider_for_model`。
  - `tests/test_llm.py`：覆盖 `api_base` 和 `protocol` 分流逻辑。

## 发布流程
- **版本号规则**：遵循当前策略（`0.5.x` 稳定迭代，`0.6.0` 质量突破）。
- **发布说明**：每次发布在 `docs/releases/vX.X.X.md` 记录变更要点。
- **README 版本历史**：更新 `README.md` 和 `README.zh-CN.md` 的版本表格。
- **发布前检查**：
  - `python3 -m py_compile openbrep/config.py openbrep/llm.py ui/app.py`
  - `python3 run_tests.py`
- **示例命令**：
  - `git tag v0.5.6`
  - `git push origin v0.5.6`

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
