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

## 本地运行（常用）

- 启动对外服务（示例）：`python -m uvicorn copilot.server:app --port 8502`
- 若由 `openbrep-addon` 驱动，确保本项目环境与配置可被其进程访问。
