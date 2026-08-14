# OpenBrep 工作副本、认可提交与自然语言回滚开发计划

日期：2026-08-14

状态：设计提案，等待维护者认可后分阶段实施

范围：React Workbench、Local API、项目级 Revision/Git、助手自然语言回滚

## 1. 目标

把 OpenBrep 当前容易混淆的“保存”和“版本”语义拆成四层：

```text
编辑器草稿
  → 保存到 HSF 工作副本
  → 自动/手动安全检查点（Revision）
  → 编译验证通过后由用户认可并正式提交（Git Commit）
```

最终产品契约：

- `Save` 只表示把草稿写入当前 HSF 工作副本，不表示认可。
- `Revision` 是可被自动创建、可被清理的短期安全检查点，不表示认可。
- `Git Commit` 是用户明确认可的长期版本基线。
- AI 可以修改工作副本和创建自动检查点，但不能自行认可或提交。
- 用户可以用自然语言表达回滚意图；LLM 只解析目标，不能通过重写源码执行回滚。
- 所有恢复操作必须展示目标和影响、取得明确确认，并由确定性后端执行。

这项工作属于 `HSF Source Session` 与 `AI Workbench` 两个 seam 的交界：
版本状态与恢复行为属于 HSF Source Session；自然语言目标解析和确认卡属于
AI Workbench。`openbrep/workbench_api.py` 继续只做薄路由。

## 2. 非目标

本计划不做：

- GitHub/GitLab 远端同步、push、pull 或账号管理；
- 多用户协作、分支合并和冲突编辑器；
- `.gsm` 二进制 diff；
- 用 Git 替代 HSFProject；
- 把整个 OpenBrep workspace 初始化为一个单体仓库；
- 让 LLM 获得任意 Git 命令或任意文件恢复权限；
- 修改 CREATE/MODIFY 的正常提示词或 benchmark 语料。

## 3. 当前机制审计

### 3.1 已具备

- 编辑器有内存 dirty buffer，`Save` 后写入当前 HSF 源文件。
- 编译和 AI 请求前会保存 dirty script buffer。
- Revision 支持创建、列表、比较和恢复；恢复会生成新的 revision，不删除历史。
- MODIFY agent loop 在第一次实际写入前惰性创建 `auto: before modify` 快照。
- 成功编译的常规 MODIFY 会创建 `auto: after modify (compile ok)` 快照。
- CLI 有 `obr rollback`，回滚前会创建 safety revision。
- MCP 层已有确定性 `rollback` 工具。
- Workbench 已有项目级 Git init、enable、status 和 commit 服务。
- Git 初始化会忽略 revisions、memory、latest、artifacts 和 `.gsm`。

### 3.2 当前缺口

- 当前漏窗项目和 workspace 都没有 `.git`，因此尚无正式认可版本。
- `Save Revision` 点击后立即创建快照，没有确认、diff 或验证门禁。
- 手动 revision 与自动 revision 在同一序列中，名称和 UI 容易让用户误认为“提交”。
- Revision 默认受保留数量策略约束，不适合作为永久认可记录。
- Git Commit 位于设置页，远离日常项目工作流。
- Git Commit 没有提交前确认卡、完整 diff 或验证状态门禁。
- Git Commit 当前使用 `git add -A`，提交范围依赖 `.gitignore`，缺少 HSF
  正式源文件 allowlist。
- Git 状态只区分 dirty/clean，不能表达“已保存但未验证”“验证通过但未认可”。
- Workbench 助手内部 MODIFY 工具没有 rollback；用户在聊天中说“回滚”可能被
  当成普通修改意图，无法保证精确恢复。
- Workbench 的 Restore 确认只显示 revision id，没有目标摘要、diff、验证状态和
  当前未认可改动的处理说明。

## 4. 架构决策

### D1：采用双版本系统，不把 Revision 冒充 Git

```text
Revision = safety checkpoint
Git Commit = accepted baseline
```

两者继续并存：

- Revision 高频、自动、低决策成本，用于事故恢复。
- Git Commit 低频、明确、由用户决策，用于长期可信资产历史。
- `.openbrep/revisions/` 不进入 Git，避免两套历史互相嵌套。

### D2：每个 HSF 项目使用独立 Git 仓库

Git 根目录保持为单个 HSF 项目目录，例如：

```text
/Users/ren/openbrep-workspace/hsf/某构件/.git/
```

不在 workspace 根目录建立统一仓库。理由：

- 每个 GDL 构件拥有独立生命周期和认可历史；
- 一个项目的提交不会夹带其他项目的工作副本；
- 项目移入 `.openbrep/trash/` 时历史随项目一起移动；
- 保持当前 flat workspace 布局；
- 与现有 `WorkbenchGitService` 的项目级语义一致。

### D3：`Save`、`Checkpoint`、`Accept & Commit` 使用不同动词

统一中文文案：

| 动作 | 建议文案 | 语义 |
|---|---|---|
| 保存编辑器 | 保存到工作副本 | 写入 HSF，不认可 |
| 手动 revision | 创建安全检查点 | 短期恢复点，不认可 |
| Git commit | 认可并提交 | 建立正式可信基线 |
| revision restore | 恢复检查点 | 恢复短期快照 |
| Git revert/restore | 回退认可版本 | 回到正式基线或反做正式提交 |

不得在同一按钮或同一 API 中混合这些语义。

### D4：提交只允许用户显式确认

AI、编译、参数应用、脚本保存和 Revision 创建都不能触发 Git Commit。

“认可并提交”必须经过确认卡，确认卡至少显示：

- commit message；
- 当前项目路径；
- 将提交的文件列表与增删行统计；
- 真实编译器结果、GSM 名称和时间；
- 静态检查、语义验证、预览完整性状态；
- 当前认可基线的 commit id；
- 明确说明“提交后该版本将成为新的认可基线”；
- `确认认可并提交` 与 `继续修改` 两个动作。

确认期间若工作副本发生变化，提交必须失败并要求重新生成确认卡，不能提交用户
没有看过的内容。

### D5：正式提交采用源文件 allowlist

不要继续以裸 `git add -A` 作为正式提交边界。集中定义并测试正式资产范围。

必须纳入：

- `libpartdata.xml`
- `paramlist.xml`
- `ancestry.xml`
- `calledmacros.xml`
- `libpartdocs.xml`
- `scripts/*.gdl`
- `.gitignore`
- 项目级、明确属于可复现输入的配置或溯源元数据
- 当前认可证据文件（见 D6）

必须排除：

- `.openbrep/revisions/`
- `.openbrep/latest`
- `.openbrep/memory/`
- `.openbrep/feedback.jsonl`
- `.openbrep/git.json`
- 临时 trace、聊天记录和运行日志
- `artifacts/`、`output/`、`*.gsm`
- OS/editor 临时文件

Vision extraction 是否纳入正式提交必须由执行前 ADR 明确：建议纳入经过用户确认、
用于重建建模意图的 extraction JSON；原图仍留在 workspace materials，不复制进
每个项目仓库。

### D6：提交保存结构化认可证据

在项目内维护一个进入 Git 的当前认可证据文件，例如：

```text
.openbrep/acceptance.json
```

建议字段：

- schema version；
- accepted_at；
- 用户提交说明；
- HSF 源文件内容摘要；
- compile mode、success、compiler、GSM 名称、exit code；
- static/semantic/delivery checks 摘要；
- 基于哪个 revision/AI trace；
- warnings 与用户 override（若未来允许）。

文件不写入自身 commit hash，避免循环依赖；Git commit id 由仓库历史提供。

### D7：默认只有真实编译通过才能认可提交

标准提交门禁：

```text
无未保存编辑器草稿
+ 工作副本内容与确认卡 hash 一致
+ LP_XMLConverter 真实编译成功
+ 静态检查无 blocking error
+ delivery/semantic verification 通过
= 可以确认提交
```

Mock compile、未配置编译器或验证失败时，不提供普通“认可并提交”。用户仍可创建
Revision 检查点保存 WIP。是否需要高级“带失败状态提交”不在首期范围，避免再次
模糊“认可”含义。

### D8：接受提交不包含 GSM 二进制

Git 保存可编辑、可重建的 HSF 源和认可证据；GSM 继续进入 artifacts。确认卡展示
GSM 产物及其摘要，但不把 `.gsm` 放入 Git。

### D9：所有回滚都是向前记录的新事件

- 恢复 Revision 后创建新的 `trigger=rollback` revision。
- 对已认可 Git 历史的撤销使用 `git revert` 或“从目标树建立新提交”，不使用
  `git reset --hard`，不改写历史。
- 回滚前先创建 safety revision。
- 恢复失败必须保持原工作副本字节不变。

### D10：自然语言回滚是“LLM 解析 + 确定性执行”

用户可以说：

```text
撤销刚才 AI 加的回纹
回到今天上午编译成功的版本
恢复到增加海棠之前
回退到上一个我认可的版本
```

处理流程：

```text
自然语言
  → 只读解析候选目标
  → 返回一个或多个 revision/commit 候选及置信度
  → 展示恢复前后摘要和 diff
  → 用户明确确认
  → 后端执行固定 restore/revert 操作
```

LLM 不获得任意 Git 命令，不调用 `update_script`/`patch_script` 模拟回滚。目标不唯一
时必须让用户选择，不能猜。

### D11：回滚分成三种用户意图

1. **撤销最近一次 AI 修改**
   - 定位最近一次 `auto: before modify` revision；
   - 即使 AI 最终编译失败也可恢复；
   - 执行前展示本次 AI 改动的文件范围。

2. **恢复某个安全检查点**
   - 目标是 revision id；
   - 适合未认可的试验过程。

3. **回退到认可版本**
   - 目标是 Git commit；
   - 当前存在未认可改动时先创建 safety revision；
   - 若只是丢弃未认可工作副本，恢复目标 commit 的 allowlist 内容；
   - 若要撤销已认可的后续提交，创建新的 revert commit 并再次要求认可确认。

### D12：正常 CREATE/MODIFY prompt 必须保持不变

自然语言版本控制意图在进入 `TaskPipeline` 之前由 Workbench 路由处理，不向
`ModifyAgentTools` 增加 rollback schema。这样：

- 普通 CREATE/MODIFY 的 prompt 和 tool schema 保持逐字节一致；
- benchmark create/modify corpus 无需因本功能重录；
- 回滚不会消耗 MODIFY agent loop 工具预算；
- 版本控制写操作不混入 GDL 生成 seam。

若实施者改变任何正常 LLM prompt/tool schema，必须按 AGENTS.md 纪律重录相应语料，
不能把 replay miss 当作普通测试失败绕过。

## 5. 目标状态模型

前端统一展示一个项目版本状态，而不是只显示 dirty/clean：

```text
Draft       编辑器有未保存 buffer
Modified    HSF 工作副本不同于最新认可 commit
Verified    当前工作副本已通过针对同一内容 hash 的完整验证
Accepted    当前工作副本与最新认可 commit 一致
Untracked   项目尚未启用正式版本控制
```

状态转换：

```text
Accepted
  └─ 编辑 → Draft
       └─ 保存 → Modified
            └─ 编译/验证成功 → Verified
                 └─ 用户确认提交 → Accepted

Modified/Verified
  └─ AI 首次写入前 → 自动 Revision 检查点

任意非 Draft 状态
  └─ 用户确认恢复 → Modified 或 Accepted（取决于目标）
```

任何源码变化都必须使旧 verification evidence 失效。不能用“上一次编译成功”解锁
已经变化的工作副本。

## 6. 服务与 API 边界

### 6.1 后端职责

保留现有模块的深接口：

- `openbrep/revisions.py`：Revision 存储、列举、比较和原子恢复；不调用 LLM。
- `openbrep/workbench/revision_service.py`：Workbench Revision adapter。
- `openbrep/workbench/git_service.py`：Git primitive，包括 init、status、diff、
  commit、读取 commit tree、revert；不承担 UI 文案或 LLM 解析。

新增一个窄的编排服务，建议：

```text
openbrep/workbench/version_control_service.py
```

负责：

- 汇总 Draft/Modified/Verified/Accepted 状态；
- 构造提交预览和确认 token；
- 校验内容 hash，防确认后内容变化；
- 调用验证服务和 Git primitive 完成认可提交；
- 构造回滚预览；
- safety revision + 原子 restore/revert 编排；
- 向前端返回稳定、无 UI 依赖的数据契约。

不得把上述逻辑堆入 `workbench_api.py`，也不得绕过 `HSFProject` 更新 source state。

### 6.2 建议 API

以下名称可在实现 review 时微调，但语义必须保持：

```text
GET  /api/project/version-control/status
POST /api/project/version-control/initialize
POST /api/project/version-control/checkpoint
POST /api/project/version-control/accept/preview
POST /api/project/version-control/accept/apply
POST /api/project/version-control/rollback/preview
POST /api/project/version-control/rollback/apply
```

`accept/preview` 返回：

- proposed message；
- changed files/diff stats；
- verification evidence；
- current HEAD；
- working source hash；
- 短期 confirmation token。

`accept/apply` 必须携带 preview 返回的 token，并再次检查 HEAD、working source hash
和 verification hash。任何不一致返回 `stale_confirmation`，不产生 commit。

`rollback/preview` 接受结构化目标，或者自然语言解析后的候选 id；返回影响摘要、
当前未认可改动以及恢复策略。`rollback/apply` 同样使用防过期 token。

所有 mutating routes 默认经过 session-level request gate。

### 6.3 自然语言路由

增加独立的版本控制意图，不直接改动现有生成路由顺序：

```text
明确的版本控制命令
  → Version Control intent guard
  → 只读本地规则优先解析
  → 必要时 LLM 仅返回严格结构化候选
  → 前端确认卡
```

本地规则应覆盖高置信表达：

- 上一个/刚才 AI 修改；
- revision id；
- commit 短 hash；
- 上一个认可版本。

含糊的自然语言才调用 LLM。LLM 输出只允许：

```text
target_kind / target_id / rationale / confidence
```

解析阶段零写盘、零 Git mutation。

## 7. 前端产品设计

### 7.1 项目头部状态

紧凑展示：

```text
工作副本：Modified
检查点：r0012
认可版本：a1b2c3d
验证：LP ✅ / Static ✅ / Semantic ✅
```

不要把版本控制只藏在 Settings。

### 7.2 Version Control 面板

建议替换/扩展当前 RevisionPanel，分两组：

**正式版本**

- 当前状态；
- 最近认可提交；
- `认可并提交`；
- 正式提交历史；
- `回退到此版本`。

**安全检查点**

- `创建安全检查点`；
- 自动/手动标签；
- 最近检查点；
- `恢复此检查点`。

两个列表在视觉和用词上必须明显区分。

### 7.3 提交确认卡

卡片不是普通 yes/no 警告，应允许用户完成一次认可判断：

- 人话摘要优先，diff 统计其次，可展开源码 diff；
- 明确显示验证证据是否对应当前内容；
- 提交说明必填，提供基于用户指令的草稿但不自动提交；
- 禁止在 dirty buffer、stale verification、compile fail 时确认；
- 首次启用 Git 时解释“正式历史保存在本项目目录内”。

### 7.4 回滚确认卡

至少展示：

- 当前状态和目标状态；
- 目标是 Revision 还是 Git Commit；
- 将被替换的文件；
- 未认可改动是否会被移出工作副本；
- 将自动创建的 safety revision；
- 恢复后是否需要重新编译；
- `确认恢复` / `取消`。

自然语言请求只能打开该卡，不能绕过确认。

## 8. 分阶段实施计划

每阶段必须能独立 review、独立测试、独立提交，不采用“大重写后一次验收”。

### P0：ADR 与术语冻结

交付：

- 新 ADR：Revision 检查点与 Git 认可提交双版本模型；
- 确认项目级 repo、allowlist、真实编译门禁、回滚不改写历史；
- 更新当前 Revision/Git 文案表。

验收：

- 架构文档不再把 Save/Revision/Commit 混为同义词；
- 明确 Vision extraction 是否属于正式可复现输入；
- 无运行时代码变化。

### P1：统一状态模型与提交范围（后端只读）

交付：

- `VersionControlStatus` 数据契约；
- 正式源 allowlist；
- working source hash；
- Draft/Modified/Verified/Accepted/Untracked 状态计算；
- Git status 不再只返回 dirty 布尔值。

测试：

- 未初始化、初始提交、已修改、已验证、dirty buffer 映射；
- runtime 文件不进入 allowlist；
- CJK 路径、空格路径、Windows 路径口径；
- 只读状态查询逐字节不改项目。

### P2：认可提交预览与确认闭环

交付：

- accept preview/apply API；
- 内容 hash + confirmation token 防过期；
- `.openbrep/acceptance.json`；
- Version Control 面板和提交确认卡；
- 首次 Git init/enable 作为显式用户动作；
- Git Commit 从 Settings 搬到项目工作流，Settings 只保留高级状态入口。

测试：

- 无 dirty buffer 且验证通过时可提交；
- compile fail/mock/no compiler 均不能普通认可；
- preview 后任一源码变化使 apply 返回 stale；
- 确认前不 init、不 stage、不 commit；
- 取消确认零写盘；
- commit 只包含 allowlist；
- 提交成功后状态为 Accepted；
- AI 完成任务不会自动 commit。

### P3：Revision 重新定位为安全检查点

交付：

- `Save Revision` 改为 `创建安全检查点`；
- 手动 checkpoint 显示“不是正式提交”；
- 自动/手动/rollback revision 视觉分型；
- Restore 确认卡显示目标摘要和 safety 行为；
- 明确 Revision prune 不影响 Git 历史。

测试：

- checkpoint 不改变 Git HEAD；
- checkpoint 不要求 compile pass；
- 恢复创建新的 rollback revision；
- 恢复失败保持工作副本不变；
- dirty editor buffer 时恢复被阻止或要求先明确处理。

### P4：确定性正式回滚

交付：

- rollback preview/apply API；
- “撤销最近一次 AI 修改”；
- “恢复检查点”；
- “回到认可版本”；
- 回滚前 safety revision；
- 原子恢复和会话 snapshot 刷新；
- 已认可历史使用新 revert commit，不 reset/rebase。

测试：

- 失败 MODIFY 只有 before revision 时仍能撤销；
- 恢复旧 commit 不删除后续 Git 历史；
- 回滚失败不产生半恢复状态；
- 回滚前未认可状态可从 safety revision 再恢复；
- 参数、脚本、根 XML 同步恢复，不出现跨文件混合版本；
- 恢复后 preview/parameter/script snapshot 全部刷新。

### P5：自然语言回滚确认卡

交付：

- 高置信本地回滚意图 guard；
- 结构化候选 resolver；
- 歧义候选选择 UI；
- 助手消息中的 rollback preview card；
- 用户确认后调用 P4 确定性 API。

测试：

- “撤销刚才 AI 修改”命中 before revision；
- “上一个认可版本”命中 Git parent；
- 指定 r0007/commit hash 精确命中；
- 多个“今天上午成功版本”返回候选而不是猜测；
- 闲聊中的“Git 回滚是什么”不触发写操作；
- 未确认时项目和 Git 零变化；
- 正常 CREATE/MODIFY messages 与 tool schema 逐字节不变；
- benchmark create/modify replay 零 miss。

### P6：迁移、文档与真机验证

交付：

- 老项目“启用正式版本控制”引导；
- 首次认可基线流程；
- README/架构/AI 开发指南更新；
- CLI 与 UI 术语统一；
- 漏窗项目作为首个真实验收对象。

真机脚本：

1. 打开已有三式漏窗，真实编译成功；
2. 显式启用项目级 Git；
3. 查看首次认可提交预览，确认只有正式源文件；
4. 提交“三式稳定基线”；
5. 要求 AI 增加回纹，计划确认后修改；
6. 验证原三式未变、回纹有效、真实编译通过；
7. 不提交，发送“撤销刚才 AI 加的回纹”，确认恢复卡后恢复；
8. 验证三式源码与首次 commit 一致；
9. 再次增加回纹并认可提交；
10. 发送“回退到三式稳定基线”，确认采用向前记录的 revert；
11. 验证 Git 历史保留两个认可提交和一个回退提交；
12. 重启 obr，确认状态、HEAD、Revision 和项目内容一致。

## 9. 测试矩阵

后端目标集：

```text
tests/test_revisions.py
tests/test_workbench_services.py
tests/test_workbench_api.py
tests/test_modify_agent_loop.py
新增 tests/test_version_control_service.py
新增 tests/test_version_control_intent.py
```

前端目标集：

```text
frontend/src/state/workbenchStore.test.ts
frontend/src/workbench/diagnostics/RevisionPanel.test.tsx（或新 VersionControlPanel）
frontend/src/components/AssistantPanel.test.tsx
新增 versionControlActions / confirmation card 测试
```

每阶段目标测试通过后，合并前仍需：

```bash
python -m pytest tests/ -q
cd frontend
npx vitest run
npx tsc --noEmit -p tsconfig.app.json
```

P5 还必须运行 create/modify benchmark replay，证明正常 prompt 流零变化。只有实现
确实改变 prompt/tool schema 时才重录语料，并在提交说明中明确原因。

## 10. Review 门禁

维护者 review 每个实施提交时逐项检查：

### 数据安全

- 是否在任何路径使用了 `git reset --hard`、强制移动 HEAD 或删除历史；若有，拒绝。
- 恢复是否先解析精确目标并创建 safety revision。
- 恢复是否原子化，失败时工作副本是否字节不变。
- 是否通过 `HSFProject` 重新加载和刷新 source state。
- 是否处理 dirty editor buffer，避免恢复后又被旧 buffer 覆盖。

### 认可语义

- Save、Checkpoint、Commit 是否仍严格分离。
- AI 是否可能绕过确认自动 commit。
- Commit 是否要求与当前内容 hash 对应的验证证据。
- confirmation token 是否能阻止 TOCTOU。
- Git 提交范围是否采用集中 allowlist，而非依赖调用点自行拼接。

### LLM 边界

- LLM 是否只返回结构化目标，不执行 Git 命令或源码重写。
- 歧义是否返回候选并等待用户选择。
- version control intent 是否在 TaskPipeline 前终止。
- 普通 CREATE/MODIFY prompt 和工具 schema 是否保持不变。

### 架构

- `workbench_api.py` 是否仍是薄 adapter。
- Git primitive、Revision primitive、版本控制编排是否职责清楚。
- mutating routes 是否默认经过 request gate。
- 是否保持项目级 Git 和 flat workspace。
- 是否新增了小而明确的公共接口契约测试。

### UI

- 提交确认是否展示真实影响，而非只有“确定吗”。
- Restore/rollback 是否展示目标、丢弃的未认可改动和 safety revision。
- Revision 与正式 commit 是否在视觉和文案上可区分。
- 无 Git、无编译器、验证失败和 stale confirmation 是否有明确反馈。

## 11. 完成定义

只有同时满足以下条件，本计划才算完成：

- 用户可以在项目中区分 Draft、Modified、Verified、Accepted 和 Untracked。
- 手动保存源码不会被描述为认可或提交。
- Revision 明确是安全检查点，Git Commit 明确是正式认可。
- 正式提交前有可审查的确认卡和真实验证门禁。
- AI 永远不能自行正式提交。
- 用户可以说“撤销刚才 AI 修改”并得到确定目标的确认卡。
- 恢复由确定性后端执行，失败不破坏当前工作副本。
- 已认可 Git 历史不被 reset/rewrite。
- 漏窗三式→新增回纹→撤销→重新新增并提交→回退认可版本的真机流程通过。
- Python、Vitest、TypeScript 和 benchmark replay 全部门禁通过。

## 12. 实施顺序建议

先做 P0–P3，建立可信的认可提交和清晰术语；再做 P4 的确定性回滚；最后做 P5
自然语言入口。不要先把 rollback 工具塞进 LLM，再补数据安全和确认机制。

每个阶段由执行者提交独立 diff；架构/决策方只负责派单、逐行 review、独立复跑门禁
和是否接受，不代替执行者写实现代码。
