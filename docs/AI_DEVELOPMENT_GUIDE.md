# OpenBrep AI Development Guide

Date: 2026-10-02  
Audience: Codex, Claude Code, Qwen Code, Cursor, Copilot agents, and human
maintainers using AI-assisted development tools.
中文版本：[AI_DEVELOPMENT_GUIDE.zh-CN.md](AI_DEVELOPMENT_GUIDE.zh-CN.md)

This guide is the operational contract for AI agents working on OpenBrep. Read
it together with [ARCHITECTURE.md](ARCHITECTURE.md).

When touching source format, generation boundaries, or custom Skill behavior,
also read the Chinese architecture decision records:

- [ADR 0001: HSF 项目目录是 OpenBrep 的源格式](adr/0001-hsf-as-source.zh-CN.md)
- [ADR 0002: AI 生成写入由 generation service 边界承接](adr/0002-generation-service-boundary.zh-CN.md)
- [ADR 0003: 自定义 Skill 是用户经验的可追溯输入](adr/0003-custom-skill-workflow.zh-CN.md)

## Mission

OpenBrep is not a generic chatbot. It is a professional GDL code workbench for
Archicad users.

Every change should strengthen at least one of these product pillars:

```text
HSF-native source management
GDL code generation, repair, explanation, and refactoring
compile-verified GSM output
asset and revision traceability
efficient expert UI for repeated daily use
```

## Goal-Oriented Agent Contract

OpenBrep expects AI development tools to work from success criteria, not only
from step-by-step instructions. Operational rules in this guide are guardrails
for quality and architecture; they are not a substitute for delivering the
requested outcome.

Before making a change, define a concise done condition for the current request.
A good done condition says:

- What user-visible behavior, document, or engineering outcome must exist.
- Which architecture boundary must be preserved.
- Which tests or manual checks prove the result.
- Whether the work must be committed, pushed, and synced with `origin/main`.

After that, run an autonomous loop:

```text
inspect context
define success criteria
make the smallest coherent change
run targeted checks
fix failures
run the required final checks
commit, push, and verify sync when applicable
report outcome, verification, and residual risk
```

Do not stop at planning when the requested work is implementable in the current
session. Ask the human only when missing information blocks success or a
reasonable assumption would create product or data risk.

## First Actions For Any AI Agent

Before editing:

```bash
git status --short --branch
rg -n "relevant_symbol" .
python -m pytest tests/ -q
```

If full tests are too slow for the current step, run targeted tests first and
full tests before merge.

Do not start by rewriting large files. Understand the current boundary first.

## Current Safe Baseline

As of 2026-10-02:

```text
main should be clean and pushed before new work starts
python tests: 3098 passed, 87 subtests passed
frontend: 780 passed (vitest) + tsc clean
```

Core seams already in place:

```text
frontend/src/workbench/*, frontend/src/state/*, frontend/src/components/*
openbrep/workbench_api.py (composition root, thin adapter)
openbrep/workbench/*_service.py
openbrep/runtime/pipeline.py
```

The retired Streamlit `ui/` package no longer exists; do not use it as a
reference for new code.

## Non-Negotiable Rules

1. Do not reintroduce imports of the retired Streamlit `ui/` package.
2. Do not bypass `HSFProject` for source state.
3. Do not treat `.gsm` as editable source.
4. Do not grow `workbench_api.py` beyond a thin adapter; put real behavior in
   `openbrep/workbench/*_service.py`.
5. Do not silently write user configuration from incidental UI changes
   (settings use draft state plus an explicit save action).
6. Do not rewrite `run_agent_generate` behavior without tests.
7. Do not change intent routing order casually.
8. Do not mix unrelated seams in one service module.
9. Do not make React views instantiate LLMs, compilers, or pipelines — they go
   through the local API and services.
10. Do not break the flat workspace layout.

## Placement Rules

Use this map when deciding where code belongs:

```text
Pure domain behavior
  openbrep/*

React workbench UI (pages, panels, store, actions)
  frontend/src/workbench/*
  frontend/src/components/*
  frontend/src/state/*

Local API composition root (thin adapter)
  openbrep/workbench_api.py

Backend services
  openbrep/workbench/*_service.py

AI generation workflow
  openbrep/runtime/pipeline.py

Deterministic parameter edit
  openbrep/runtime/micro_modify.py

Blender script → GDL importer (BS2G)
  openbrep/importers/blender_script/*

Tapir/Archicad workflow
  openbrep/tapir_bridge.py
  openbrep/tapir_controller.py
  openbrep/workbench/tapir_service.py
  openbrep/workbench_tapir.py

CLI (obr)
  cli/main.py
```

If the correct place is unclear, keep `workbench_api.py` a thin adapter and put
real behavior in a testable service module.

## Compatibility Wrappers

The Streamlit-era `ui/app.py` wrappers (`run_agent_generate`, `chat_respond`,
...) were removed together with the retired `ui/` package. The current stable
entry points are:

```text
openbrep.runtime.pipeline.TaskPipeline.execute     (CLI + workbench generation)
openbrep/workbench_api.py WorkbenchSession routes  (local API contract)
```

Do not change their behavior or route payloads without migrating all tests and
callers in the same change.

## Session State Discipline

The React workbench keeps UI state in the Zustand store
(`frontend/src/state/`). Server-side session state lives in `WorkbenchSession`
(`openbrep/workbench_api.py`) and is the public application contract.

When changing scripts or parameters:

```text
clear preview data
clear preview warnings
reset preview metadata
bump editor version if editor content changes programmatically
capture snapshot before irreversible AI writes
```

Do not mutate important state from views directly. Pass callbacks/actions
through the store, and keep settings writes behind draft state plus an explicit
save action.

## Generation Path Contract

The generation path currently flows like this:

```text
CLI or React workbench assistant route
  → openbrep/workbench/assistant_service.py
  → openbrep.runtime.pipeline.TaskPipeline.execute
  → compile gate + verify_semantics (bounded repair rounds)
  → TaskResult (success = verification report passed)
```

Intent routing order (`IntentRouter.classify()`):

```text
pure chat / GDL teaching question          → CHAT
debug prefix / error log / strong debug    → DEBUG
explicit modify/check keyword              → MODIFY
explicit creation keyword                  → CREATE
generic GDL keyword                        → MODIFY if project loaded, else CREATE
image present, unclear text                → IMAGE
project loaded, ambiguous                  → MODIFY
no project, ambiguous                      → LLM fallback, else CHAT
```

Tests to run for generation changes:

```bash
python -m pytest tests/test_pipeline_create_compile.py tests/test_pipeline_modify.py tests/test_micro_modify.py tests/test_pipeline_semantic_repair.py -q
python -m pytest tests/ -q
```

## Project Lifecycle Contract

The project path currently flows like this:

```text
workbench route
  → openbrep/workbench/project_service.py / project_session_service.py
  → openbrep.hsf_project.HSFProject
  → openbrep.compiler
```

Rules:

```text
Import .gsm creates or loads an HSF project directory.
Import .gdl/.txt wraps parsed code into an HSF project.
Load HSF opens an existing source directory.
Compile writes output/ObjectName_vN.gsm.
Compile does not create a new source directory.
```

Tests to run for project changes:

```bash
python -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
python -m pytest tests/ -q
```

## UI Design Rules

OpenBrep is a workbench, not a marketing page.

Prefer:

```text
dense but readable controls
clear workflow sections
stable panel dimensions
action-oriented labels
tables, tabs, segmented controls, toggles, and compact buttons
```

Avoid:

```text
large decorative hero layouts
nested cards
heavy gradients
one-off CSS scattered in views
duplicated chat rendering
explanatory UI text that belongs in docs
```

## Testing Matrix

Use the smallest useful test set while editing, then full tests before merge.

```text
Workbench API / services
  tests/test_workbench_api.py
  tests/test_workbench_services.py
  tests/test_workbench_concurrency.py

Generation
  tests/test_pipeline_create_compile.py
  tests/test_pipeline_modify.py
  tests/test_micro_modify.py
  tests/test_pipeline_semantic_repair.py

Verification / naming
  tests/test_semantic_verifier.py
  tests/test_naming_alignment.py
  tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py

Blender importer
  tests/test_blender_script_importer.py
  tests/test_bs2g_mesh_loft.py
  tests/test_bs2g_shim.py

Preview
  tests/test_gdl_previewer.py tests/test_three_preview.py

Vision
  tests/test_vision.py

Frontend
  cd frontend && npx vitest run
  npx tsc --noEmit -p tsconfig.app.json

Whole suite
  python -m pytest tests/ -q
```

## Manual Checks

For changes that affect UI, generation, compile, Tapir, or Archicad behavior,
manual smoke testing is expected:

```text
1. obr  (launch the workbench)
2. Generate a simple object.
3. Modify the generated object.
4. Ask for explanation only and verify no code mutation.
5. Import .gdl.
6. Import .gsm if LP_XMLConverter is available.
7. Load existing HSF directory.
8. Run local script check.
9. Run 2D/3D preview.
10. Compile to versioned .gsm.
11. If Archicad/Tapir is available, read selected object parameters.
12. If Archicad/Tapir is available, write one safe parameter edit.
```

## Branch Workflow

Use branch isolation for non-trivial work:

```bash
git switch main
git pull
git switch -c refactor-something
```

After changes:

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push -u origin branch-name
```

Merge only after tests pass:

```bash
git switch main
git merge --no-ff branch-name -m "merge branch-name"
python -m pytest tests/ -q
git push
```

Default finish sequence:

Unless the user explicitly asks not to commit or push, completed work should end
with commit, push, and main/origin synchronization. For direct `main` work:

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
```

For branch work, push the branch first, then merge to `main`, run full tests,
push `main`, and verify `main` equals `origin/main`.

## Review Checklist For AI Changes

Before finalizing a change, answer these:

```text
Did this add logic to the right layer?
Did this preserve HSF as source of truth?
Did this preserve existing wrapper compatibility?
Did this update session defaults if new state was added?
Did this add or update tests?
Did this run the right targeted tests?
Did this run full tests before merge?
Could this break the workbench UI manually even if unit tests pass?
Does the final answer mention untested manual risks?
```

## Latest Cleanup Milestone

Completed (Streamlit era, before the React workbench migration — kept as
history; the current state lives in ARCHITECTURE.md):

```text
1. Config/model source handling moved to ui/config_service.py.
2. tests/test_llm.py split into focused LLM adapter and config service tests.
3. ADRs added for HSF-as-source, generation-service, and custom Skill workflow.
4. ui/app.py reduced into the 1400-1600 line target range without deleting wrappers.
```

## Product Direction Reminder

When in doubt, optimize for a professional GDL developer:

```text
fast import
clear editable source
trustworthy AI changes
compile validation
traceable outputs
repeatable workflows
low-friction Archicad handoff
```

Do not optimize only for demo appeal. OpenBrep should feel like a serious GDL
engineering workbench.
