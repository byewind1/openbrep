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

## 禁止事项
- 禁止提交 `config.toml`（含真实 API Key 和代理地址）
- 禁止提交 `.obsidian/`
- 禁止在 `config.example.toml` 暴露真实 key 和代理地址
- 禁止删除现有 validator 规则（只能降级为 warning，不能删）
- 禁止在没有明确问题时重写全部脚本


## 模型使用建议
- 精细代码编辑（str_replace、多步骤缩进修改）：必须用 claude 系列模型
- GLM-4.7 适合：对话、分析、解释、简单生成
- GLM-4.7 不适合：复杂代码手术、多文件联动修改、缩进敏感的 Python 编辑
