# OpenBrep Agent Instructions

This file is the repository entry point for AI coding agents.

中文维护者优先阅读：

- `docs/ARCHITECTURE.zh-CN.md`
- `docs/AI_DEVELOPMENT_GUIDE.zh-CN.md`

English / general agent references:

- `docs/ARCHITECTURE.md`
- `docs/AI_DEVELOPMENT_GUIDE.md`

## Project Mission

OpenBrep is a professional AI-assisted GDL workbench for Archicad users.
It is not a generic chatbot wrapper.

Core product contract:

```text
HSF-native source management
GDL generation, repair, explanation, and refactoring
compile-verified GSM output
traceable project and asset lifecycle
```

## Goal-Oriented Execution

Treat user requests as outcomes to deliver, not scripts to mechanically follow.
Before editing, infer the smallest useful success criteria for the current
request.

Default success criteria:

- The requested behavior, documentation, or product decision is actually
  delivered.
- Existing architecture boundaries remain intact.
- Relevant tests pass, and full tests pass before merge or push unless the user
  explicitly narrows scope.
- Completed work is committed, pushed, and verified against `origin/main`
  unless the user says otherwise.
- The final answer states what changed, how it was verified, and any remaining
  risk.

Then loop until done:

```text
inspect -> define success criteria -> change -> test -> fix -> retest -> finish
```

The rules in this file are guardrails. They do not replace the outcome. If a
user gives a goal, choose the implementation path yourself. If a user gives
specific steps, follow them while still verifying the final result against the
goal. Ask questions only when missing information blocks completion or a
reasonable assumption would be risky.

## Before Editing

Run:

```bash
git status --short --branch
```

Then read the relevant architecture guide above. Use `rg` to locate symbols
before opening large files.

## Non-Negotiable Rules

1. Do not add substantial new logic to `ui/app.py`.
2. Do not treat `.gsm` as editable source. HSF project directories are source.
3. Do not bypass `HSFProject` for source state.
4. Do not duplicate chat bubble rendering. Use `ui/chat_render.py`.
5. Do not scatter new `st.session_state` defaults. Use `ui/session_defaults.py`.
6. Do not rewrite `run_agent_generate` behavior without tests.
7. Do not change generation intent routing order without updating tests.
8. Do not remove compatibility wrappers in `ui/app.py` unless all callers/tests are migrated.
9. Do not make Streamlit views instantiate LLMs, compilers, or pipelines.
10. Do not break the current flat workspace layout.

## Where Code Belongs

```text
Streamlit page shell / CSS / optional dependency probing
  ui/app_shell.py

Streamlit panels
  ui/views/*

Chat turn orchestration
  ui/chat_controller.py

Chat rendering
  ui/chat_render.py

Project import / load / compile workflow
  ui/project_service.py
  ui/project_io.py

AI generation workflow
  ui/generation_service.py
  openbrep/runtime/pipeline.py

Vision/image workflow
  ui/vision_controller.py

Tapir/Archicad workflow
  ui/tapir_controller.py
  ui/tapir_views.py
  openbrep/tapir_bridge.py

UI formatting/parsing helpers
  ui/view_models.py

Domain logic
  openbrep/*
```

If unsure, keep `ui/app.py` as a thin adapter and place real behavior in a
testable module.

## Required Tests

Run targeted tests while editing, then full tests before merge:

```bash
python -m pytest tests/ -q
```

Common targeted sets:

```bash
python -m pytest tests/test_generation_service.py tests/test_llm.py tests/test_llm_adapter.py tests/test_config_service.py -q
python -m pytest tests/test_project_service.py tests/test_project_io.py tests/test_project_io_compile.py -q
python -m pytest tests/test_chat_flow.py tests/test_chat_controller_single_panel.py -q
python -m pytest tests/test_app_shell.py tests/test_session_defaults.py -q
```

## Current Baseline

As of 2026-04-27:

```text
ui/app.py: 1588 lines
test baseline: 473 passed, 6 subtests passed
```

Important architecture boundaries already exist:

```text
ui/project_service.py
ui/generation_service.py
ui/app_shell.py
ui/chat_controller.py
ui/chat_render.py
ui/session_defaults.py
ui/views/*
```

## Default Finish Sequence

Unless the user explicitly asks not to commit or push, finish completed code or
documentation work with this sequence:

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push
```

If work was done on a branch, merge it back to `main` after tests pass:

```bash
git switch main
git merge --no-ff branch-name -m "merge branch-name"
python -m pytest tests/ -q
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
```

The final state should normally be:

```text
main and origin/main point to the same commit
working tree is clean
```

## Manual Risk

Unit tests do not fully cover Streamlit UI behavior or real Archicad/Tapir
integration. For changes touching UI, compile, generation, vision, or Tapir,
perform or clearly request manual smoke testing:

```text
streamlit run ui/app.py
generate simple object
modify existing object
explain without mutation
import .gdl
import .gsm if LP_XMLConverter is available
load HSF directory
preview 2D/3D
compile versioned .gsm
test Tapir/Archicad when available
```
