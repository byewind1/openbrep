# Changelog

All notable changes to OpenBrep are recorded here.
Format: [Semantic Versioning](https://semver.org), entries newest-first.

---

## [0.10.10] — 2026-09-21

> 完整发布说明见 `docs/releases/v0.10.10.md`。

### 预览材质

- 新增离线材质文档与预设（`openbrep/materials.py`，项目内 `.openbrep/materials.json`）：
  木材 / 金属 / 玻璃 / 塑料四套家族预设，带颜色、粗糙度、金属度、透明度、IOR
- 语义材质预设：按参数名 / 描述 / 用户指令推断材质家族（橡木桌面 → wood，金属桌腿 → metal）
- 命名 GDL 材质真实生效：`DEFINE MATERIAL` / `MATERIAL "name"` 作用于预览网格，
  材质 ID 大小写不敏感匹配
- 没有材质文件时按参数推断兜底，不再退回灰色占位（`chair_ok` 实测 8 个网格全部解析）
- 材质前/后变化写入 MODIFY 验收报告；未解析材质明确 warn，不伪装通过
- 预览棚光与色调映射调整（AgX + RoomEnvironment）
- 新增 `openbrep/runtime/visual_self_check.py`：真实 Three.js 渲染截图自检，
  引擎不可用时返回 `unverified`（`OPENBREP_VISUAL_CHECK=1/0` 可强制开关）

### Codex 接入

- 双入口（`openbrep/codex/entry.py`）：新增 `local` 本机配置入口，跟随 `CODEX_HOME`
  或标准 `~/.codex`，只读解析模型 / provider，不管理认证；默认仍为托管登录，
  既有配置行为不变
- app-server 互斥锁迁出 Codex home（`~/.openbrep/run/`，`OPENBREP_CODEX_LOCK_DIR` 可覆盖）
- cc-switch 多供应商路由（`openbrep/codex/cc_switch.py`）：只读注册表适配、
  模型按供应商分组、目录开关与自动刷新、结构化 `provider/model` 引用
- `codex` 可执行文件解析回退到用户登录 shell 的 PATH

### 交付可信度（ST02 / ST03 / ST07 / ST08）

- 新增 `openbrep/source_fingerprint.py`：受管源文件集合的确定性 `sha256:` 指纹
- 新增 `openbrep/runtime/delivery_finalizer.py`：成功修改绑定真实 after-revision，
  失败不得伪造
- 新增 `openbrep/workbench/delivery_presentation.py` 与前端 `DeliveryCard`：
  已验证 / 部分修改 / 快照失败 / 旧记录 / 无 diff 五种状态如实展示
- 新增 `openbrep/workbench/host_verification_service.py`：宿主验证绑定精确产物指纹

### 参数与技能

- 新增 `openbrep/parameter_mutations.py`：`paramlist.xml` 无损原子结构化修改
- 新增 `openbrep/parameter_observation.py`：只读参数角色与生效值观测，保守证明
- 新增 `openbrep/skill_proposals.py` + `workbench/skill_proposal_service.py`：
  技能提案可审阅、可恢复审批，未审批候选不进入技能检索
- 新增 `openbrep/contracts/stair.py`：显式绑定的螺旋楼梯工程合同求值

### 其他

- 创建会话隔离与历史批量删除
- Python 测试 3003 passed；前端 vitest 721 passed + `tsc` 干净

## [0.10.0 – 0.10.9] — 2026-09

各版本发布说明见 `docs/releases/v0.10.*.md`（含独立 Codex 会话目录、
ChatGPT/Codex 连接修复、Windows sidecar 启动修复、模型可见性与发布元数据校准）。

---

## [0.9.1] — 2026-09-13

### 安装包真正独立可用（下载安装即可用）

**打包**
- 新增 `openbrep-backend.spec`：PyInstaller onefile 冻结 Python 后端为 sidecar 二进制 `obr7-backend`，内嵌 openbrep 包、知识库（free 层）、skills、前端构建产物与 litellm 数据文件
- Tauri 通过 `bundle.externalBin` 打包 sidecar；`src-tauri/src/main.rs` 启动时优先使用内嵌 sidecar，开发态回退系统 `python3 scripts/obr7.py`；Windows 下设 `CREATE_NO_WINDOW` 避免控制台窗口闪现
- `scripts/obr7.py` 支持冻结态：资源根切换到 `sys._MEIPASS`，运行时 cwd 落到可写的 `~/.openbrep/workspace`（避免写入只读的 .app 资源目录），API 进程内运行（冻结二进制无 `python -m` 子进程可用），daemon 子进程直接拉起二进制本身
- 发布流水线：构建前端 → 冻结后端 → 冻结后端冒烟（起服务、验 `/api/snapshot` 与前端首页）→ Tauri 打包；两平台均冒烟

**构建修复（v0.9.0 tag 首轮 CI 暴露）**
- `tauri.conf.json` 的 beforeBuild/beforeDev 钩子按 src-tauri 工作目录修正相对路径（`cd ../frontend`）
- 补齐 Windows 构建必需的 `icons/icon.ico`

**测试**
- `tests/test_obr7_launcher.py` 新增冻结态用例（runtime root、daemon argv），24 个全绿

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
