---
id: prompts.tasks.modify_skills
version: 1.0
last_updated: 2026-05-23
used_by: openbrep/runtime/pipeline.py::_MODIFY_SKILLS_PROMPT
description: MODIFY/DEBUG/REPAIR 任务注入到 TASK STRATEGY 的最小改动规则
---

## 修改任务规则（必须遵守）
你正在修改一个已有的 GDL 对象。严格遵守以下规则：
1. 只修改需要修改的部分，不要重写整个脚本（除非整个脚本都需要变）
2. 保留原有的注释、代码风格和命名规范，不要"顺手优化"无关代码
3. 先用中文简要说明：做了什么修改、改了哪个文件、为什么
4. 如果修改了 3D 脚本中的参数引用，检查 paramlist.xml 是否需要同步修改
5. 如果新增了参数，必须同时输出更新后的 paramlist.xml
6. 不需要修改的文件不要输出
7. 用 [FILE: path] 格式输出每个改动文件的完整修改后内容
