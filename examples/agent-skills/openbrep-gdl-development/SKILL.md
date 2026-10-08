---
name: openbrep-gdl-development
description: Use OpenBrep MCP to inspect, modify, verify, preview, restore, and deliver an HSF GDL project.
---

# OpenBrep GDL development

Use OpenBrep as the deterministic GDL engineering harness. The calling Agent owns the model loop; OpenBrep tools do not need a second model configuration.

## Start with the tool contract

1. Call `capabilities` and read `contract_version`, supported parameter types, compile modes, per-call LP availability semantics, preview limits, host availability, requirement executors, result semantics, evidence fields, and error codes.
2. Call `load_project` before proposing a change. Treat the returned `source_fingerprint` as the source snapshot you inspected.
3. Keep the object’s purpose, requested change, explicit keep requirements, and parameter values in the task context. Do not infer a domain standard from the object name.

## Make a controlled change

- For a small parameter edit, call `apply_edit` with `mode="draft"` first. Use `set_parameters` with typed scalar values; use `set_script` only when replacing the complete named script is intended.
- Inspect the returned diff, compile mode/result, and semantic result. A draft does not change the original HSF project.
- Apply an accepted edit with `mode="apply"`. Supply a stable `operation_id` for a retryable action; reuse that exact ID only for the exact same request. A different payload needs a new ID.
- If the source changed after inspection, reload the project and reassess before applying. Do not retry a `source_changed` failure with a fresh operation ID until you have reviewed the new source.
- Preserve existing parameters, scripts, contract files, library references, and behaviors unless the user explicitly asks to change them.

## Verify what actually ran

After applying, inspect the compile result, then call `semantic_verify` and `render_evidence` when those checks fit the task. Read `mode`, `success`, `passed`, `source_current`, `source_fingerprint`, `evidence_id`, and `requirements_passed` separately.

- `ok` means the tool operation returned. It does not mean the object passed its checks.
- `mode="mock"` is structural simulation, not LP_XMLConverter or Archicad compilation.
- `source_current=false` means the evidence may refer to an outdated source snapshot; reload and rerun relevant checks.
- Local preview evidence is not Archicad host validation. State host validation as unavailable unless an actual host check ran.
- A missing, stale, unknown, or not-run required check is not a pass. Report the requirement IDs and the returned reasons.

## Recover and deliver

- If a change regresses a keep requirement or the user rejects it, call `rollback` with the returned revision ID (or `previous`) and rerun the checks needed to establish the restored state.
- Report the HSF project path, applied revision, compile mode and result, semantic/visual evidence IDs, required-check status, and any unavailable host or real-compiler validation.
- Do not describe a GSM as delivered unless a real compiler produced it. Preserve the HSF source project as the editable source of truth.

## Error handling

Use structured error `code` and `details` to choose the next action. Reload after source conflicts, correct invalid specs without broadening the edit, and do not turn a tool or compile failure into a success claim. Never hide warnings, stale evidence, mock compilation, or unresolved requirements in a summary.
