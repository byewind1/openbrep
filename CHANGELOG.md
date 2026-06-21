# Changelog

All notable changes to OpenBrep are recorded here.
Format: [Semantic Versioning](https://semver.org), entries newest-first.

---

## [1.0.0] — 2026-06-21

### Milestone: Tauri 桌面工作台正式落地

**架构**
- Streamlit 完全退役（v0.9.0 规划 → v1.0.0 落地）：删除 `ui/` 目录 79 个文件及 24 个 UI 测试
- 域逻辑迁移：`classify_code_blocks` / `preview_3d_to_three_payload` / `local_file_dialog` 从 UI 层迁移至 `openbrep/workbench/`
- Tauri v2 桌面壳（`src-tauri/`）：Rust 主进程 spawn Python sidecar，通过 stdout 握手协议（`OBR7_READY_URL=`）获取服务地址后打开 Webview 窗口
- `workbench_api.py` 新增 `--static-dir` 单端口模式：同时服务 API 与 `frontend/dist/` 静态资源，带路径遍历防护和 SPA index.html fallback

**防御性加固**
- Rust：stderr 改为 piped + relay thread，Python 崩溃堆栈在终端 / macOS Console.app 可见
- Rust：启动超时硬失败（Err）而非静默打开死窗口，附带可操作错误提示
- Rust：`shutdown_backend` 增加 kill 后等待循环（最长 3s），防止 Python 孤儿进程
- Vite：注入 `VITE_IS_TAURI`（由 `TAURI_ENV_TARGET_TRIPLE` 自动检测），前端可据此分支渲染

**CI**
- 新增 `.github/workflows/release-tauri.yml`：`v*` tag 触发 macOS / Windows 矩阵 Tauri 构建并上传 artifacts

**质量**
- 674 个测试全绿（`python3 run_tests.py`）
- `cargo check` 通过（Rust 1.96 stable，0 error，0 warning）

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
