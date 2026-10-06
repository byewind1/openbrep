# Changelog

All notable changes to OpenBrep are recorded here.
Format: [Semantic Versioning](https://semver.org), entries newest-first.

---

## [0.12.0] — 2026-10-06

> 完整发布说明见 `docs/releases/v0.12.0.md`。

- 任务执行过程以真实事件落盘（工具开始/结束、验证、交付、取消、失败、公开说明），聊天携带任务关联，重开项目可复盘；进程退出前未完成的任务被发现并如实展示。
- 同项目 AI 修改接入与 XML 保存刷新不再递增项目会话代次，连续咨询/修改/续做不再被打断；项目激活入口（打开/关闭/新建/导入/切换/恢复 revision）保持失效语义。
- Agent 超时独立可配置（`[agent] agent_idle_timeout=180 / agent_task_timeout=1800 / agent_tool_timeout=600`），普通与 Codex 引擎一致；有有效活动的任务不再被固定 90 秒窗口误杀，超时原因结构化区分并保留部分修改。
- 工具超时/取消/项目切换后迟到的源码写入在提交点被授权检查拒绝并隔离；源码变更校验与提交进入同一原子边界；关键写入被拒且无后续有效修复时如实标记未完成，不假称成功。
- Codex 公开说明（commentary）实时转发并落盘，与最终答复分离；隐藏推理不采集。
- 聊天的咨询/失败/取消等全部结果立即保存并关联任务；执行记录保存失败时界面明确提示。
- 时间线：分页查看全部步骤、15 秒等待提示（区分等模型与工具运行）、工具三态、旧记录明确标注不编造历史。

---

## [0.11.2] — 2026-10-04

> 完整发布说明见 `docs/releases/v0.11.2.md`。

- 续接式修改会继承最近一个明确的用户目标；复合目标、重构任务与“保持不变”约束不再误触发旧的效果门。
- 后端重启后从恢复的用户历史重建目标上下文，助手建议不会被当作用户授权或修改目标。
- 参考图取消采用的迟到成功/失败响应增加项目身份守卫，切换项目后不再清空或污染新项目状态。
- 修正 macOS 安装兼容性说明，按完整安装包依赖的最高 `minos` 分别声明 Apple Silicon 与 Intel 最低版本。

---

## [0.11.1] — 2026-10-04

> 完整发布说明见 `docs/releases/v0.11.1.md`。

- 聊天进入统一两阶段对话入口：只读准备（路由/约束/计划）→ 显式确认执行；幂等令牌与过期守卫防止重复执行和跨项目写入。
- 全部修改引擎共享按轮执行策略与范围化用户约束；计划批准与源版本绑定；任务完成由真实交付证据支撑。
- 顾问回答带输入输出预算、事实引用与覆盖不足声明；检查覆盖加深（外部文件变化、CALL 依赖、版本感知缓存）。
- 新增修改效果验收门（GUI 显式契约）：按任务意图推导变化种类（几何/材质/参数/新增选项），几何签名与连通分量作证据，"改了文件但无形态效果"不再记为完成。
- 助手消息受限 Markdown 渲染：参考图图卡、应用内大图、加载失败态、可展开参考图库与显式采用。
- 参考图采用链路：取图带协议/类型/大小/内网约束与逐跳重定向校验；资产按具体项目隔离、刷新可恢复；下一轮执行自动带上已采用参考。
- 聊天记录保留每条消息创建时间；历史保存不再覆盖时间戳。

---

## [0.11.0] — 2026-10-03

> 完整发布说明见 `docs/releases/v0.11.0.md`。

- 设置改为居中宽弹窗，八节导航与内容分栏，统一字号、间距和暗色样式。
- 可视化添加、编辑与删除服务商，支持端点模板、手输模型及显式保存的凭据草稿。
- 从兼容端点获取模型列表，勾选合并并保留手工模型；失败时仍可手输。
- 连接测试展示错误分类、修复提示和可展开技术详情；状态徽标区分配置、发现和测试结果。
- 配置导出默认脱敏；导入先预览差异，再确认提交，并检测配置版本冲突。
- 配置写入采用副本验证与原子替换，删除被引用服务商时展示阻止原因。
- 保留跨导航草稿，支持模型可见性直达、焦点还原、键盘循环和嵌套弹窗关闭隔离。
- 发布流水线读取版本发布说明并保留 Release 草稿，桌面安装包验收后再公开。

---

## [0.10.12] — 2026-10-02

> 完整发布说明见 `docs/releases/v0.10.12.md`。

- 修复桌面 PATH 缺少 Node 时 npm 版 Codex app-server 启动退出。
- 当前 cc-switch 官方供应商读取同账号、同登录用户的更新凭据，修复旧凭据导致的 401；更新使运行缓存失效。
- CHAT / MODIFY 等待 `willRetry=true` 的上游重试，不再提前报告失败。

---

## [0.10.11] — 2026-10-02

> 完整发布说明见 `docs/releases/v0.10.11.md`。

### 工作台草稿保护（SF1）

- 修复五类丢稿场景（F01–F05）：Save 不再只看当前标签脏、Save As 包含未保存编辑、
  参数应用不覆盖脚本草稿、打开其他项目前先确认、修订与工作树一致
- 过期响应隔离（R2）：`saveProject` / `runParameterWrite` / `exportHsfProject` /
  `saveRevision` 在异步写后与 catch 分支校验项目身份，晚到的旧响应不再污染新项目
- 新增回归测试：`sourceDraftSafety.test.ts`、`WorkbenchAppDraftSafety.test.tsx`、
  `useProjectLeaveGuard`（含 10 个 R2 回归用例）

### 模型路由与重试（R 线）

- 角色与分层契约：模型选择携带任务角色与 tier，路由结果作为显式选择传递，
  请求身份全程保留，配置不可变性钉死
- 角色级 fallback 重试策略（R5）；pi 模型目录元数据导入（R6）；
  目录校验器接受集合与此前完全一致（R3b）
- 多 key 凭据池与按项目路径限制模型（R7，path-scoped model policy）
- 性能：无技能信号时技能意图检测短路

### 工程质量与 CI

- ruff 门禁进入 CI：`openbrep` + `cli` 清理至零发现后强制执行
  （E501 豁免 + 棘轮计划；F401 保留 facade 再导出）
- 错误收割按确定性顺序遍历，修复 ext4（CI）与 APFS 遍历顺序不同的稳定失败；
  浏览器冒烟对齐现行 mock 编译文案
- `TaskPipeline` 结构化拆分：`_handle_gdl` 701→104 行、`_handle_script_update`
  391→72 行，行为不变、benchmark 回放零退化

### 文档

- 回填 ADR 0004（Streamlit 退役）/ 0005（黄金语料回放）/ 0006（Codex 双入口）
- 14 份过期一次性文档归档至 `docs/archive/`；README / INSTALL / 架构文档
  澄清 Streamlit 退役现状

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
