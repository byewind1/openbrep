# Unified conversation / 统一对话入口

The default entry is `unified`. In the workbench, consultation and planning are read-only. Explicit modification requests execute directly; the one-turn “先出计划（不改项目）” control requests a plan and resets after sending. Selecting a proposal does not authorize writing; executing a proposal does.

咨询不保存编辑器草稿，不编译、不创建 revision/GSM，不刷新交付预览。检查事实与未验证假设分开；本地预览是近似结果，缺失 CALL 依赖、未覆盖的字符串分支和不支持的指令会显示部分覆盖。未运行的检查不能当成已验证结论。

执行先由后端保存本轮任务与不可变源/草稿快照，再保存编辑器草稿，使用服务端令牌执行。保存失败会停止。普通执行遇到 `SOURCE_CHANGED` 最多重新准备一次；显式计划遇到 `PLAN_STALE` 保留原计划供比较，需要重新生成。计划生成失败不会回落执行。

## 任务过程记录与连续对话

同一项目内可以连续咨询、修改和续做：AI 修改结果接入与 XML 保存重载是**同项目源刷新**，不递增项目会话代次（`project_epoch`）；打开/关闭/新建/导入/切换/另存/恢复 revision 仍会使旧请求失效。执行响应携带 `session_id` 与 `current_project_epoch`，过期令牌返回 `PROJECT_CHANGED`（提示“项目状态已变化，请重新确认当前项目后重试”），前端不跨项目盲重发。

每个 turn 的执行过程以真实事件为唯一事实源，追加写入 `<project>/.openbrep/memory/chats/tasks/<turn_id>.jsonl`（单条公开文本 ≤4KiB、单任务记录 ≤2MiB，超限写一次 `truncated` 事件；凭据、认证路径、完整 prompt、图像 base64 与工具完整源码永不入日志）。事件含 `accepted / preparing / waiting_model / public_commentary / tool_started / tool_finished / verification / source_changed / delivery / cancelled / failed / completed`；完成状态区分完整交付（delivered）、部分修改（partial）与无源码变化（no_change）——编译通过不等于任务完成。事件记录只用于展示与复盘，不进入任何 LLM prompt、质量评分或 benchmark。

- 只读查询：`GET /api/assistant/turn/events/<turn_id>`（不占用会话执行锁，执行中可查询）；任务索引 `GET /api/assistant/turn/events` 列出已开始的任务（含未终止项）——进程退出后重开即可发现"已开始未结束"的任务并展示其执行过程，不伪造最终答复。
- 持久化失败以结构化 `events_recording`（persisted/in_memory/degraded）随响应上报，界面明确提示"执行记录保存失败"；degraded 在 turn 内粘滞（丢失的事件无法补写）；无项目的咨询只保留会话内存记录，不创建任何目录。
- 工具事件携带 `tool_call_id`（start/finish 关联）与真实 `duration_ms`/`elapsed_ms`；超时/取消后迟到的工具写入在提交点被授权检查拒绝并隔离（`execution.abandoned_write_workers` > 0 表示非完整交付）。
- 聊天 meta 保存 `task_ref`（turn_id/run_id/schema_version）与时间线步骤；重开项目后据此恢复执行过程时间线。
- 旧记录没有过程数据时显示“旧记录未保存执行过程”，不补造历史；进程中断后未终止的记录按 interrupted/unknown 展示，不推测为完成。
- 事件记录保存失败不回滚已发生的源码修改，界面会提示执行记录保存失败。

## 超时语义

普通 `llm.timeout` 只约束单次文本调用，不再限制整个工具回合。Agent 执行超时独立配置（`config.toml`，必须为正整数，0/负数/非法值回退默认并记警告）：

```toml
[agent]
agent_idle_timeout = 180    # 无有效活动上限：公开模型输出、工具开始/结束、协议进展才续期
agent_task_timeout = 1800   # 整个任务总上限，跨轮共享，不按轮重置
agent_tool_timeout = 600    # 单工具执行上限；编译/预览沿用 compiler.timeout 独立预算
```

达到阈值时区分 `idle_timeout / task_deadline / tool_timeout / connection_error / cancelled`，结果以结构化 `execution.timeout_reason` 记录（布尔 `execution.timeout` 保留兼容）。超时与取消都保留已发生的部分修改与交付证据；有写入任务的执行不会透明重跑。已发出的工具调用会先被排空再判定超时，避免调度延迟误报。

`agent_task_timeout` 同时约束普通（非 Codex）agent loop 的整个任务：到期按当前进度如实收尾（非完整交付）。源码变更工具的写入在提交点做授权检查——任务已终止/取消/项目切换后，迟到的写入一律被拒绝并隔离，不会污染共享项目状态。

## Compatibility

Set the following in `config.toml`, then restart:

```toml
[llm]
conversation_entry = "legacy"
```

Remove the entry or set `"unified"` to restore the default. Legacy restores the old routing and default modification plan card, while preserving read-only/negation guards and fail-closed planning. It does not restore execution after a failed explicit plan.

## API contract

- `POST /api/assistant/turn`, `phase=prepare`: requires `client_turn_id`, `message`, `project_epoch`; optionally accepts history, images, draft scripts and `requested_mode=auto|consult|plan`. Returns `advice`, `awaiting_confirmation`, `ready_to_execute`, `failed` or `cancelled`.
- `phase=execute`: consumes `turn_id`. Plan approval also requires `approve=true`, `plan_id`, integer `plan_version`. Client-supplied replacement instructions or plan JSON cannot replace the stored task. `approve=false` cancels without saving drafts.
- Events carry `turn_id` and `project_epoch`. Duplicate tokens return the existing result; cancelled/expired tokens do not revive. Restart clears pending permissions.
- User constraints remain distinct from model assumptions. Completion requires actual change, a matching run/delivery reference and passing verification. Failed or incomplete work stays eligible for an explicit continuation; completed work is not automatically repeated. Clearing chat or switching projects clears pending permissions. Source changes invalidate relevant plans/evidence.
- Image CREATE retains its extraction-confirmation gate. Edited extraction fields may be approved only against the stored turn; they cannot replace its task.

## Evaluation

Frozen route cases: `tests/fixtures/assistant_route_eval.json` (138 cases). Frozen advisor questions/multi-round scripts use public synthetic snapshots in `tests/fixtures/advisor_quality_cases.json`. They contain no user project source or credentials. Ordinary-model and Codex acceptance slots are explicitly **未验收** in `advisor_quality_acceptance.json`.

Offline smoke (no paid calls):

```bash
python scripts/assistant_route_eval.py --mode mock
python scripts/advisor_quality_eval.py --mode mock --limit 1
python scripts/assistant_turn_browser_smoke.py
python scripts/task_feedback_browser_smoke.py
```

The browser runner uses the real React shell and local API with offline model/compiler doubles. Its screenshots and `result.json` default to `/private/tmp/assistant-turn-e2e/`. It covers consultation with/without a project, plan approval/cancellation, direct execution, negation, proposal discussion/reference, save failure, source changes, project switching, HTTP idempotency, legacy fallback and plan failure. It does not verify real Archicad compilation or hosted-model quality.

`scripts/task_feedback_browser_smoke.py` covers the task-feedback contract on the same real-browser basis (evidence defaults to `/private/tmp/task-feedback-e2e/`): continuous same-project modify rounds, live process timeline from real events, reopen replay from chat meta + event records, view-all beyond 12 steps, the `PROJECT_CHANGED` message, partial-change presentation and the per-turn event store contract. Long-duration timeouts (180s idle / 1800s task) are covered by virtual-clock unit tests, not wall-clock browser runs.

Maintainer-authorized real evaluation requires an explicit configuration and opt-in flag; run separately for an ordinary model and Codex:

```bash
python scripts/assistant_route_eval.py --mode real --config /path/to/config.toml --allow-paid
python scripts/advisor_quality_eval.py --mode real --config /path/to/config.toml --allow-paid --limit 0
```

Route failures remain in the denominator. Required gates: zero forbidden-write executions, ≥95% accuracy and ≥95% explicit-execution recall. A mock result is never formal acceptance; fixed-set success does not prove zero production misclassification.

Human scoring has five 0–2 dimensions: goal understanding, evidence, feasibility, tradeoffs and constraint retention. Compare legacy/advisor on all 30 questions and 12 multi-round scripts. Required advisor mean ≥8, ≥80% improved comparisons, and zero fabricated checks or ignored prohibitions. Blank scores and untested capabilities remain **未验收**. Record observed calls, token usage, model/provider, context version and duration; do not infer missing usage from reply length.
