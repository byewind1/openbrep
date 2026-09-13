# Changelog

All notable changes to OpenBrep are recorded here.
Format: [Semantic Versioning](https://semver.org), entries newest-first.

---

## [0.9.0] — 2026-09-13

> 版本口径：本里程碑曾短暂以 v1.0.0 口径准备（2026-06-21，仅文档与版本号，未打 tag、未发布 Release），现撤回 1.0 编号，以 v0.9.0 正式发布。发布说明见 `docs/releases/v0.9.0.md`。

### Milestone: Tauri 桌面工作台正式落地

**架构**
- Streamlit 完全退役：删除 `ui/` 目录 79 个文件及 24 个 UI 测试
- 域逻辑迁移：`classify_code_blocks` / `preview_3d_to_three_payload` / `local_file_dialog` 从 UI 层迁移至 `openbrep/workbench/`
- Tauri v2 桌面壳（`src-tauri/`）：Rust 主进程 spawn Python sidecar，通过 stdout 握手协议（`OBR7_READY_URL=`）获取服务地址后打开 Webview 窗口
- `workbench_api.py` 新增 `--static-dir` 单端口模式：同时服务 API 与 `frontend/dist/` 静态资源，带路径遍历防护和 SPA index.html fallback

**防御性加固**
- Rust：stderr 改为 piped + relay thread，Python 崩溃堆栈在终端 / macOS Console.app 可见
- Rust：启动超时硬失败（Err）而非静默打开死窗口，附带可操作错误提示
- Rust：`shutdown_backend` 增加 kill 后等待循环（最长 3s），防止 Python 孤儿进程
- Vite：注入 `VITE_IS_TAURI`（由 `TAURI_ENV_TARGET_TRIPLE` 自动检测），前端可据此分支渲染

**功能累积（v0.8.0 → v0.9.0）**
- 语义修复环（`openbrep/runtime/semantic_repair.py`）：编译通过后再跑语义验证，阻塞性问题触发有界修复轮
- 命名对齐与保留字角色检查（`openbrep/naming_alignment.py`）
- 确定性微修改（`openbrep/runtime/micro_modify.py`）：纯参数值修改不走 LLM
- Vision Harness（`openbrep/vision/harness.py`）：图片输入四段管线，CREATE 带图需用户确认抽取结果
- GSM CALL 宏依赖解析与图库上下文（`openbrep/library_context.py`），2D 预览覆盖 POLY2 全族与富文本链
- Copilot 集成（`openbrep/workbench/copilot_service.py` + `obr serve`）
- 统一供应商注册表 `[[llm.providers]]`、会话级模型覆盖、模型可见性
- 质量台账（`openbrep/quality/`）与 Reflector/Curator 经验蒸馏
- Benchmark 黄金语料录制/回放，CI 离线回归门禁

**CI**
- 新增 `.github/workflows/release-tauri.yml`：`v*` tag 触发 macOS / Windows 矩阵 Tauri 构建并上传 Release
- 旧 PyInstaller 流水线 `build-installers.yml` 不再随 tag 触发（保留 workflow_dispatch 手动入口）

**质量**
- 2641 个 Python 测试全绿（`python -m pytest tests/ -q`）
- `cargo check` 通过（Rust stable，0 error，0 warning）

---

## [0.8.0] — 2026-05

React 工作台成为默认 UI：合并 react-workbench 分支，`obr` 默认启动 React + Monaco + Three.js 工作台，Streamlit 降级为 fallback；新增 Verification 一等 seam，统一验证报告含置信度 / 检查结果 / 残余风险；CREATE 路径 compile 状态显式可见，MODIFY 路径含 compile + auto-repair 证据。

## [0.7.0] — 2026-04

GDL 资产生命周期里程碑：新增 modify / repair 前后 revision 快照、`obr history` / `obr rollback`、工程级变更摘要、GDLContractChecker 合规检查输出、`--compare mock|real` 对比编译。

## [0.6.x] — 2026-01 ~ 2026-04

Runtime Phase 完整落地、知识库校准、macOS 桌面包发布、安装体验完善，详见 `docs/releases/v0.6.*.md`。

## [0.5.x] — 2025

OpenBrep 品牌发布（v0.5），图片即意图，CLI 模式，自然语言 create / modify。

## [0.4.0]

HSF-native 架构重构，Streamlit Web UI，强类型 paramlist，44 项单元测试。
