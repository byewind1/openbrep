# OpenBrep AI Development Guide

Date: 2026-04-27  
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

As of 2026-04-27:

```text
main should be clean and pushed before new work starts
ui/app.py: 1588 lines
test baseline: 469 passed, 6 subtests passed
```

Core refactor boundaries already merged:

```text
ui/project_service.py
ui/generation_service.py
ui/app_shell.py
ui/chat_controller.py
ui/chat_render.py
ui/session_defaults.py
ui/views/*
```

## Non-Negotiable Rules

1. Do not add substantial new logic to `ui/app.py`.
2. Do not bypass `HSFProject` for source state.
3. Do not treat `.gsm` as editable source.
4. Do not duplicate chat bubble rendering.
5. Do not add scattered `st.session_state` default initialization.
6. Do not rewrite `run_agent_generate` behavior without tests.
7. Do not change intent routing order casually.
8. Do not remove compatibility wrappers just because they look redundant.
9. Do not make Streamlit views instantiate LLMs, compilers, or pipelines.
10. Do not break the flat workspace layout.

## Placement Rules

Use this map when deciding where code belongs:

```text
Pure domain behavior
  openbrep/*

Streamlit page shell / CSS / optional dependency probe
  ui/app_shell.py

Session defaults
  ui/session_defaults.py

Project import / load / compile workflow
  ui/project_service.py
  ui/project_io.py

AI generation workflow
  ui/generation_service.py
  openbrep/runtime/pipeline.py

Vision/image workflow
  ui/vision_controller.py

Chat turn orchestration
  ui/chat_controller.py

Chat rendering
  ui/chat_render.py

Streamlit panels
  ui/views/*

Simple formatting / parsing helpers for UI
  ui/view_models.py

Tapir/Archicad workflow
  ui/tapir_controller.py
  ui/tapir_views.py
  openbrep/tapir_bridge.py
```

If the correct place is unclear, prefer a small adapter in `ui/app.py` and put
real behavior in a testable module.

## Compatibility Wrappers

Several functions in `ui/app.py` remain as public compatibility wrappers because
tests and UI callbacks patch or import them directly.

Examples:

```text
run_agent_generate
chat_respond
classify_and_extract
_handle_unified_import
_handle_hsf_directory_load
import_gsm
do_compile
_apply_generation_result
_apply_generation_plan
```

Do not remove or rename these wrappers unless you migrate all tests and callers
in the same change.

## Session State Discipline

Add new persistent keys in `ui/session_defaults.py`.

When changing scripts or parameters:

```text
clear preview data
clear preview warnings
reset preview metadata
bump editor version if editor content changes programmatically
capture snapshot before irreversible AI writes
```

Do not mutate important state from views directly. Pass callbacks into views.

## Generation Path Contract

The generation path currently flows like this:

```text
ui/app.py.run_agent_generate
  → ui/generation_service.GenerationService.run_agent_generate
  → openbrep.runtime.pipeline.TaskPipeline.execute
  → build_generation_result_plan
  → ui/actions.apply_generation_plan
  → ui/view_models.build_generation_reply
```

Intent routing order:

```text
debug intent                  → REPAIR
modify bridge prompt          → MODIFY
post clarification explain    → CHAT
post clarification check      → MODIFY
explainer intent              → CHAT
existing script content       → MODIFY
otherwise                     → CREATE
```

Tests to run for generation changes:

```bash
python -m pytest tests/test_generation_service.py tests/test_llm.py tests/test_llm_adapter.py tests/test_config_service.py -q
python -m pytest tests/ -q
```

## Project Lifecycle Contract

The project path currently flows like this:

```text
ui/app.py wrapper
  → ui/project_service.ProjectService
  → ui/project_io
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
python -m pytest tests/test_project_service.py tests/test_project_io.py tests/test_project_io_compile.py -q
python -m pytest tests/test_llm.py -q
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
Shell/bootstrap
  tests/test_app_shell.py

Session defaults
  tests/test_session_defaults.py

Chat renderer/panel/controller
  tests/test_chat_render.py
  tests/test_chat_panel_render.py
  tests/test_chat_controller_single_panel.py
  tests/test_chat_flow.py

Generation
  tests/test_generation_service.py
  tests/test_llm.py

Project lifecycle
  tests/test_project_service.py
  tests/test_project_io.py
  tests/test_project_io_compile.py

Preview
  tests/test_preview_controller.py

Vision
  tests/test_vision.py

Whole suite
  python -m pytest tests/ -q
```

## Manual Checks

For changes that affect UI, generation, compile, Tapir, or Archicad behavior,
manual smoke testing is expected:

```text
1. streamlit run ui/app.py
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
Could this break Streamlit UI manually even if unit tests pass?
Does the final answer mention untested manual risks?
```

## Latest Cleanup Milestone

Completed:

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
