# Unified conversation / 统一对话入口

The default entry is `unified`. In the workbench, consultation and planning are read-only. Explicit modification requests execute directly; the one-turn “先出计划（不改项目）” control requests a plan and resets after sending. Selecting a proposal does not authorize writing; executing a proposal does.

咨询不保存编辑器草稿，不编译、不创建 revision/GSM，不刷新交付预览。检查事实与未验证假设分开；本地预览是近似结果，缺失 CALL 依赖、未覆盖的字符串分支和不支持的指令会显示部分覆盖。未运行的检查不能当成已验证结论。

执行先由后端保存本轮任务与不可变源/草稿快照，再保存编辑器草稿，使用服务端令牌执行。保存失败会停止。普通执行遇到 `SOURCE_CHANGED` 最多重新准备一次；显式计划遇到 `PLAN_STALE` 保留原计划供比较，需要重新生成。计划生成失败不会回落执行。

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
```

The browser runner uses the real React shell and local API with offline model/compiler doubles. Its screenshots and `result.json` default to `/private/tmp/assistant-turn-e2e/`. It covers consultation with/without a project, plan approval/cancellation, direct execution, negation, proposal discussion/reference, save failure, source changes, project switching, HTTP idempotency, legacy fallback and plan failure. It does not verify real Archicad compilation or hosted-model quality.

Maintainer-authorized real evaluation requires an explicit configuration and opt-in flag; run separately for an ordinary model and Codex:

```bash
python scripts/assistant_route_eval.py --mode real --config /path/to/config.toml --allow-paid
python scripts/advisor_quality_eval.py --mode real --config /path/to/config.toml --allow-paid --limit 0
```

Route failures remain in the denominator. Required gates: zero forbidden-write executions, ≥95% accuracy and ≥95% explicit-execution recall. A mock result is never formal acceptance; fixed-set success does not prove zero production misclassification.

Human scoring has five 0–2 dimensions: goal understanding, evidence, feasibility, tradeoffs and constraint retention. Compare legacy/advisor on all 30 questions and 12 multi-round scripts. Required advisor mean ≥8, ≥80% improved comparisons, and zero fabricated checks or ignored prohibitions. Blank scores and untested capabilities remain **未验收**. Record observed calls, token usage, model/provider, context version and duration; do not infer missing usage from reply length.
