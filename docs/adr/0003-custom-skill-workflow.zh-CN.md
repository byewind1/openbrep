# ADR 0003: 自定义 Skill 是用户经验的可追溯输入

日期：2026-04-27  
状态：Accepted

## 背景

OpenBrep 面向高阶 ArchiCAD 用户和 GDL 开发者。用户会长期积累个人偏好、公司规范、对象类型经验和禁止修改边界。如果这些知识只留在聊天上下文里，后续生成无法稳定复用，也不利于其他 AI 工具交接。

## 决策

OpenBrep 将自定义 Skill 作为用户经验的持久化输入。聊天创建 Skill 的路径负责把用户规则落成可读取文件；后续生成和修改应自动加载项目 Skill，使 AI 在没有重复说明的情况下遵守这些规则。

## 成功标准

- 用户可以通过聊天创建或更新 Skill。
- Skill 文件存放在项目约定目录，能被后续生成自动读取。
- Skill 内容表达成功标准、偏好和边界，而不是只保存一次性操作步骤。
- 生成结果能体现相关 Skill 的约束。
- 文档说明用户后续不需要反复输入 Skill 文件名。

## 后果

Skill 机制让 OpenBrep 从一次性聊天工具变成可长期积累经验的 GDL 工作台。代价是 Skill 内容必须保持清晰、可审查、可迁移，不能把隐私、临时上下文或过窄的一次性任务混入长期规则。

## 对 AI 开发工具的要求

处理 Skill 相关需求时优先阅读：

- `docs/CUSTOM_SKILLS.zh-CN.md`
- `openbrep/skills_loader.py`
- `ui/chat_controller.py`
- `ui/views/sidebar_panel.py`

新增 Skill 行为时，优先让用户提供“目标和成功标准”，再把稳定规则沉淀为 Skill。不要把一次性命令包装成长久规则。
