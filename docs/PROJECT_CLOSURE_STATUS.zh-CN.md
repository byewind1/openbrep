# OpenBrep 必须收口项状态

日期：2026-05-23

本文把当前计划噪声收敛为维护者可执行的三类状态。它不替代路线图，只回答“现在必须先收口什么”。

## 已收口

- Free 层 GDL 知识 P0-P6 已完成，见 `docs/GDL_KNOWLEDGE_VERIFICATION_PLAN.zh-CN.md`。
- Runtime Phase 1 主链路已在 v0.6.0 收口。
- Release zip 构建、macOS/Windows artifact 上传、包级 smoke 和浏览器 smoke 已进入 workflow。
- Pro V1 授权码、`.obrk` 导入验证和水印打包脚本已有基础实现。
- 工作区记忆已覆盖聊天、错题和整理后的 Skill 持久化。

## 必须收口

### 1. macOS unsigned 分发说明

状态：收口为 unsigned zip + Gatekeeper workaround，不再把证书签名/公证作为当前阶段必须项。

已完成：

- `README.md`、`README.zh-CN.md`、`INSTALL_CN.md` 已写明 `xattr -dr com.apple.quarantine /path/to/OpenBrep` 临时处理方式。
- `scripts/build_macos.sh` 保持 unsigned zip 构建，不要求维护者配置 Apple Developer 证书。
- macOS package smoke 和 browser smoke 用真实 zip 验证启动与首页渲染。

后续可选：

- 如果未来用户规模和分发需求上升，再单独做 Developer ID 签名、公证、`.app` / `.dmg` 分发。
- 当前不为签名公证单独发布 patch release。

### 2. v0.7 Revision MVP

状态：CLI 和数据层 T1-T3 已收口，UI 收口策略仍需决定。

已完成：

- project-level `.openbrep/revisions` 快照。
- `history` / `rollback` CLI。
- `compare` CLI 和 revision 子命令。
- 每个带解释 metadata 的 revision 落盘 `explanation.md`。
- CLI history/list 展示触发来源、编译状态、变更摘要。
- revision compare 展示 source diff、compile metadata、explanation diff 和 compare compile 摘要。
- create/list/compare/rollback 单元测试。

剩余选择：

- 主 UI 继续隐藏版本管理，还是恢复为紧凑只读历史面板。
- 用真实 LP_XMLConverter 编译结果校验 revision compile metadata。

建议：先保持 UI 隐藏，只把 CLI 和数据模型作为 v0.7 MVP 收口；等用户明确需要可视历史时再恢复 UI。

### 3. 计划文档降噪

状态：本文件作为新的维护入口。

已完成：

- 将“必须收口”从大路线图里抽成三项。
- `docs/v0.7_revision_management_design.md` 已标记为部分实现并列出剩余收口项。
- `docs/install_distribution_strategy_2026-05-01.md` 已补签名公证状态。

后续规则：

- `docs/superpowers/plans/*` 下的旧执行计划视为历史记录，不作为当前 backlog。
- 当前 backlog 以本文、`docs/VISION.md`、`docs/GDL_COMMERCIAL_SKILL_PLAN.zh-CN.md` 为准。

## 可以推进但不阻塞当前发布

- PyPI Trusted Publishing。
- Pro Door/Window System Skill 第一套样板。
- Pro Skill 元数据和召回结构。

## 暂不承诺

- Homebrew cask。
- npm bootstrapper。
- 社区平台。
- 团队协作和 Git review UI。
- IFC 属性映射。
- Archicad JSON API 无头截图。
- 多语言 UI。
