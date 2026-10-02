# ADR 0004: React 工作台是唯一 shell，Streamlit UI 退役

日期：2026-06-21（commit `40aa6af`；v0.9.0 随 Tauri 桌面工作台正式发布）  
状态：Accepted

## 背景

OpenBrep 早期用 Streamlit `ui/` 包作为 shell（`ui/app.py` 一度达到 1588 行，
经数轮治理拆出 `ui/*_service.py`、`ui/*_controller.py` 边界）。2026-05 起另起
React workbench 分支开发专业工作台 UI，v0.8.0（2026-06-19）合并后 React 成为
默认 UI，Streamlit 降级为 fallback。

双前端并存带来两类问题：

- **漂移**：同一域逻辑要同时接两个 UI 层（Streamlit 的 `ui/*_service.py` 与
  workbench 的 `openbrep/workbench/*_service.py`），每改一次行为就要双份接线、
  双份测试，两条路径必然渐行渐远。
- **维护成本**：Streamlit 的 callback 共享状态路径多、UI 测试脆弱（24 个 UI
  测试），控件模型也无法承载工作台式高密度交互。它作为 fallback 已不再产生
  与成本相称的价值。

## 决策

彻底移除 Streamlit `ui/` 包（79 个文件 + 24 个 UI 测试），React workbench
（`frontend/`）+ 本地 API（`openbrep/workbench_api.py`）成为唯一 shell；桌面化
走 Tauri v2 壳（`src-tauri/`），Python 以 sidecar 方式启动。域逻辑此前已迁入
`openbrep/workbench/*_service.py`，`WorkbenchSession` 为组合根。

本决策随 commit `40aa6af`（2026-06-21）落地，后续清理收尾；v0.9.0
（2026-09-13）随 Tauri 桌面工作台对用户发布。

## 成功标准

- 仓库中不存在 `ui/` 包，CI 全绿且不依赖 Streamlit。
- 所有 UI 行为经由 workbench API 合约（session / project / compile / preview
  等路由）暴露，可脱离前端独立测试。
- 红线固化到 `AGENTS.md` Non-Negotiable Rules 第 1 条与
  `docs/ARCHITECTURE.md`：不得重新引入 Streamlit `ui/` 包的 import。

## 后果

- 正面：域逻辑只服务一个 UI 消费方；前端行为改由 vitest + tsc 守护；新 UI
  能力（工作台布局、参数面板、预览质量档）不再受 Streamlit 控件模型限制。
- 代价：失去"Streamlit 快速拼原型"的路径，所有新 UI 必须走 React 组件 +
  service 模块；个别一次性脚本（如旧打包 smoke）随之过时或重写。
- 红线：任何工具或贡献者不得 resurrect Streamlit `ui/` 包；历史材料见
  `docs/archive/`（仅历史参考）。

## 对 AI 开发工具的要求

- 处理 UI / shell 相关需求时优先阅读 `docs/ARCHITECTURE.zh-CN.md`（seam 模型）
  与 `AGENTS.md` 的 "Where Code Belongs"。
- 不要引用 `ui/*` 路径；遇到历史文档中的 `ui/*` 表述，以本文与
  `ARCHITECTURE.zh-CN.md` 的现状为准。
- 不要试图"恢复 fallback"：退役是一等决策，不是临时状态。
