# OpenBrep Architecture

Date: 2026-04-27  
Status: Active architecture guide for maintainers and AI coding agents
中文版本：[ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md)

OpenBrep is an AI-assisted GDL workbench for Archicad power users and GDL
developers. Its core product promise is:

```text
Natural language or imported library object
→ editable HSF project
→ AI-assisted generation, modification, debug, and explanation
→ compile-verified GSM output
→ traceable project and asset lifecycle
```

This document defines the current architecture, ownership boundaries, and
development rules. It is written for human maintainers and AI development tools.

## Current State

The Streamlit UI was previously concentrated in `ui/app.py`. The current main
branch has moved the application into explicit boundaries:

```text
ui/app.py: 1773 lines
tests: 452 passed, 6 subtests passed
```

`ui/app.py` is still larger than ideal, but it is no longer the place where new
features should accumulate. It is now the application shell and dependency
composition point.

## Architectural Layers

Use this layer model when adding or moving code:

```text
View layer
  ui/views/*
  Renders Streamlit controls and visual panels. Should be mostly stateless.

Controller layer
  ui/*_controller.py
  Orchestrates one user workflow. Can read/write session state through injected
  dependencies, but should not own core domain rules.

Service layer
  ui/*_service.py
  Owns application-level business workflows such as project import, compile,
  generation, and result application. Services are testable outside Streamlit
  where possible.

Shell/bootstrap layer
  ui/app_shell.py
  Owns page config, global CSS, optional dependency probing, and local runtime
  capability detection.

Domain layer
  openbrep/*
  Owns HSF/GDL parsing, compilation, validation, model routing, knowledge, and
  runtime pipeline semantics. This layer should not import Streamlit.

Tests
  tests/*
  Protect behavior across UI controllers, services, domain logic, and legacy
  compatibility wrappers.
```

## Runtime Flow

```text
User input
  ├─ natural language
  ├─ image
  ├─ .gdl / .txt import
  ├─ .gsm import
  └─ HSF directory load

Streamlit view
  └─ ui/views/*

Controller / service boundary
  ├─ ui/chat_controller.py
  ├─ ui/project_service.py
  ├─ ui/generation_service.py
  ├─ ui/vision_controller.py
  ├─ ui/revision_controller.py
  └─ ui/preview_controller.py

Domain core
  ├─ openbrep/hsf_project.py
  ├─ openbrep/runtime/pipeline.py
  ├─ openbrep/compiler.py
  ├─ openbrep/gdl_parser.py
  ├─ openbrep/paramlist_builder.py
  ├─ openbrep/validator.py
  └─ openbrep/knowledge.py

Output
  ├─ editable HSF project directory
  ├─ pending diffs for human confirmation
  ├─ revision metadata
  └─ compiled .gsm in workspace/output/
```

## Source Of Truth

OpenBrep treats an HSF project directory as the editable source of truth.

```text
workspace/
  Bookshelf/
    libpartdata.xml
    paramlist.xml
    ancestry.xml
    calledmacros.xml
    libpartdocs.xml
    scripts/
      1d.gdl
      2d.gdl
      3d.gdl
      vl.gdl
      ui.gdl
      pr.gdl
```

Rules:

- A `.gsm` file is a compiled deliverable, not the source format.
- A `.gdl` file alone is not enough to represent a complete library part.
- `paramlist.xml` and `scripts/*.gdl` must be treated as one source unit.
- Compiling must not create a new source directory.
- Importing `.gsm` may create a new stable HSF project directory.
- Modifying an object updates the current HSF project directory or produces
  pending diffs for review.

See also: [project_layout.md](project_layout.md)

## Key Modules

### `ui/app.py`

Role: application composition root.

Allowed responsibilities:

- Import dependencies and compatibility symbols.
- Initialize page shell and `session_state`.
- Load config globals used by the sidebar.
- Define thin wrappers required by existing tests or Streamlit callbacks.
- Compose the three-column UI layout.
- Inject dependencies into views, controllers, and services.

Avoid adding:

- New large rendering blocks.
- New business workflows.
- New raw session initialization blocks.
- New LLM or compiler orchestration.
- New duplicated HTML/CSS.

If a new block in `app.py` grows beyond roughly 30-50 lines, it probably belongs
in a view, controller, or service.

### `ui/app_shell.py`

Role: Streamlit shell and local capability probing.

Owns:

- `configure_page(st)`
- global CSS
- `streamlit_ace` availability
- Plotly availability
- Tapir bridge availability
- Archicad process probing

Do not put product workflows here. This module should stay shallow and safe to
import.

### `ui/session_defaults.py`

Role: centralized `st.session_state` defaults.

All new session keys must be added here unless they are deliberately ephemeral
and created immediately before use.

Rules:

- Preserve existing key names unless doing an explicit migration.
- Group defaults by domain.
- Avoid initializing mutable defaults by reusing a shared object.
- Add tests when adding keys that controllers rely on.

### `ui/views/*`

Role: Streamlit rendering only.

Examples:

- `sidebar_panel.py`
- `chat_panel.py`
- `editor_panel.py`
- `parameter_panel.py`
- `project_tools_panel.py`
- `workspace_tools_panel.py`
- `preview_views.py`

Rules:

- Views receive behavior as callbacks.
- Views should not instantiate LLMs, compilers, pipelines, or services.
- Views may read simple state for rendering but should avoid owning workflow
  decisions.
- Use existing UI language and workflow structure.
- Do not add cards inside cards or decorative layouts that fight Streamlit's
  dense workbench design.

### `ui/chat_render.py`

Role: unified chat message rendering.

All historical and live chat output must use this module. Do not reintroduce
`st.chat_message` or duplicate chat bubble HTML in separate files.

### `ui/chat_controller.py`

Role: chat turn orchestration.

Owns:

- Tapir trigger dispatch from chat/sidebar events.
- Bridge follow-up resolution.
- Debug prefix resolution.
- Text vs image path dispatch.
- Chat anchor focus handling.

Does not own:

- GDL generation internals.
- Vision generation internals.
- Project import/compile semantics.
- Raw view rendering.

### `ui/project_service.py`

Role: project lifecycle service.

Owns:

- Compile current HSF project to versioned GSM.
- Import GSM through LP_XMLConverter.
- Load existing HSF directory.
- Import `.gdl` / `.txt` / `.gsm` through one service boundary.
- Finalize loaded project into session state.

This service delegates low-level IO to `ui/project_io.py` and state mutation to
existing action helpers.

### `ui/generation_service.py`

Role: AI generation workflow service.

Owns:

- Generation lifecycle state.
- Intent decision for generate/modify/repair/chat within the generation path.
- `TaskPipeline` request construction.
- Recent chat history trimming.
- Cancellation handling.
- Applying generation plans through injected callbacks.

Important compatibility rule:

`ui/app.py.run_agent_generate` remains the public compatibility wrapper. Tests
and other modules may patch symbols through `ui.app`; preserve this until the
test suite is explicitly migrated.

### `ui/vision_controller.py`

Role: image-driven generation/debug workflow.

Owns:

- Vision route setup.
- Vision generation lifecycle integration.
- Image error classification.
- Applying generated scripts or pending diffs.

Do not merge this back into `generation_service.py` until the image path has
dedicated tests for real-world UI behavior.

### `ui/gdl_checks.py`

Role: lightweight local GDL checks for editor feedback.

This is not a replacement for LP_XMLConverter compile verification. Treat it as
fast feedback only.

### `openbrep/runtime/pipeline.py`

Role: domain pipeline for LLM task execution.

The runtime pipeline should stay independent of Streamlit. It can receive
callbacks such as `on_event` and `should_cancel`, but it should not depend on
`st.session_state`.

## HSF And Compile Semantics

### Create

Creating a new object creates one HSF project directory:

```text
workspace/ObjectName/
```

### Import `.gsm`

Importing `.gsm`:

```text
.gsm
→ LP_XMLConverter libpart2hsf
→ temporary HSF
→ stable workspace/ObjectName/
→ HSFProject.load_from_disk()
```

If the name exists, current behavior creates an imported copy suffix.

### Modify

Modification updates the current HSF project or produces pending diffs:

```text
auto_apply=True
  → write scripts/params immediately

auto_apply=False
  → session_state.pending_diffs
  → user confirms write
```

### Compile

Compilation reads the current HSF project directory and writes:

```text
workspace/output/ObjectName_vN.gsm
```

Do not create a new HSF directory during compile.

## Generation Semantics

`run_agent_generate` is still the stable high-level entry point used by chat,
elicitation, vision debug, and tests. Internally it delegates to
`GenerationService`.

Intent resolution order inside generation:

```text
debug intent                  → REPAIR
modify bridge prompt          → MODIFY
post clarification explain    → CHAT
post clarification check      → MODIFY
explainer intent              → CHAT
existing script content       → MODIFY
otherwise                     → CREATE
```

Do not change this order without updating tests.

Generation result handling:

- `TaskPipeline.execute()` returns a task result.
- `build_generation_result_plan()` converts result to an application plan.
- `ui/actions.py` applies the plan to current project or pending review state.
- `ui/view_models.py` formats replies for the chat surface.

## Session State Contract

The UI is stateful. Treat `session_state` as a public application contract.

Important keys:

```text
project
work_dir
chat_history
pending_diffs
pending_ai_label
pending_gsm_name
script_revision
editor_version
preview_2d_data
preview_3d_data
preview_warnings
preview_meta
active_generation_id
generation_status
generation_cancel_requested
last_project_snapshot
tapir_selected_guids
tapir_param_edits
model_api_keys
assistant_settings
```

Rules:

- Add defaults in `ui/session_defaults.py`.
- Keep names stable unless a migration is implemented.
- Clear preview state after script or parameter mutations.
- Bump editor version after programmatic script changes.
- Capture a project snapshot before irreversible AI writes.
- Do not mutate `project` from a view module except through injected callbacks.

## Testing Strategy

The current baseline is:

```text
python -m pytest tests/ -q
452 passed, 6 subtests passed
```

Required test scope by change type:

```text
View rendering change
  → targeted view tests
  → relevant controller tests

Session key change
  → tests/test_session_defaults.py
  → affected controller/service tests

Chat flow change
  → tests/test_chat_flow.py
  → tests/test_chat_controller_single_panel.py
  → tests/test_chat_panel_render.py

Generation change
  → tests/test_generation_service.py
  → tests/test_llm.py
  → full test suite before merge

Project import/compile change
  → tests/test_project_service.py
  → tests/test_project_io.py
  → tests/test_project_io_compile.py
  → relevant `tests/test_llm.py` import flow tests

Preview change
  → tests/test_preview_controller.py
  → preview smoke/manual check when rendering behavior changes

Tapir/Archicad change
  → unit tests with mocks
  → manual Archicad checklist before release
```

Before merging to `main`:

```bash
python -m py_compile ui/app.py
python -m pytest tests/ -q
```

For substantial UI changes, also run:

```bash
streamlit run ui/app.py
```

## Branching And Merge Policy

Use `main` as the verified, runnable branch.

Recommended branch naming:

```text
refactor-*
feature-*
fix-*
```

Workflow:

```text
1. Start from clean main.
2. Create a focused branch.
3. Keep commits small and scoped.
4. Run targeted tests while editing.
5. Run full tests before merge.
6. Merge back to main only after tests pass.
7. Push main after merge.
```

Avoid long-running unmerged branches for UI/service refactors. The Streamlit app
has many shared state paths, so stale branches become expensive quickly.

## Rules For AI Coding Agents

When an AI coding tool works on this repository, it must follow these rules:

Start from the requested outcome and define success criteria before choosing
steps. Architecture rules are guardrails for that loop: inspect the current
boundary, make the smallest coherent change, test, fix, retest, and only finish
when the requested outcome is verified. Do not stop at a plan when the change is
implementable in the current session.

1. Read this document before changing architecture.
2. Check `git status --short --branch` before editing.
3. Do not put new feature logic directly into `ui/app.py` unless it is a thin
   compatibility wrapper or dependency composition.
4. Prefer existing modules:
   - UI rendering → `ui/views/*`
   - workflow orchestration → `ui/*_controller.py`
   - business workflow → `ui/*_service.py`
   - pure formatting/parsing helpers → `ui/view_models.py`
   - domain logic → `openbrep/*`
5. Preserve existing public wrapper names unless tests and callers are migrated.
6. Add or update tests with each behavior change.
7. Do not change GDL/HSF source semantics casually.
8. Do not treat `.gsm` as source.
9. Do not remove compatibility with existing flat workspace layout.
10. Keep Streamlit views dense and workbench-oriented.

## Where To Put New Features

Use this decision table:

| Feature type | Preferred location |
|---|---|
| New sidebar control | `ui/views/sidebar_panel.py` plus callback injection |
| New project import option | `ui/project_service.py` and `ui/project_io.py` |
| New compile/version behavior | `ui/project_service.py`, revision controller, tests |
| New chat action | `ui/views/chat_panel.py`, `ui/chat_controller.py`, `ui/chat_render.py` |
| New AI generation behavior | `ui/generation_service.py` or `openbrep/runtime/pipeline.py` |
| New image generation/debug behavior | `ui/vision_controller.py` |
| New preview capability | `ui/preview_controller.py`, `ui/views/preview_views.py`, domain previewer |
| New Tapir action | `ui/tapir_controller.py`, `ui/tapir_views.py` |
| New GDL validation rule | `ui/gdl_checks.py` for fast UI check, `openbrep/validator.py` for domain validation |
| New model/provider logic | `openbrep/config.py`, `openbrep/llm.py`, sidebar callbacks |
| New knowledge behavior | `ui/knowledge_access.py`, `openbrep/knowledge.py` |

## Current Refactor Milestones

Completed:

```text
Phase 1: project service boundary
Phase 2: generation service boundary
Phase 3: app shell boundary
```

Remaining high-value work:

```text
1. Move config/model source management out of app.py.
2. Split tests/test_llm.py into focused test modules.
3. Add architecture decision records for major workflow choices.
4. Continue reducing app.py toward 1400-1600 lines without breaking wrappers.
```

## Manual Release Checklist

Before a release that touches UI, generation, compile, or Tapir:

```text
1. Start Streamlit UI.
2. Create a simple object from natural language.
3. Modify an existing object.
4. Ask for explanation only and verify no script mutation occurs.
5. Import a .gdl file.
6. Import a .gsm file with LP_XMLConverter.
7. Load an existing HSF directory.
8. Run local script check.
9. Run 2D/3D preview.
10. Compile to versioned .gsm.
11. If Archicad is available, reload library and read selected object params.
12. If Tapir is available, write one safe parameter change back to Archicad.
```

## Design Direction

OpenBrep should become a top-tier GDL code workbench, not a generic AI chat
wrapper. Prioritize:

- HSF-native project management.
- Compile-verified output.
- Traceable revisions and GSM artifacts.
- Expert GDL explanation, repair, and refactoring.
- Dense, efficient workflows for Archicad power users.
- Explicit boundaries that make AI-assisted development safe.

The long-term architecture target:

```text
ui/app.py
  thin composition root

ui/views
  all Streamlit rendering

ui/controllers
  workflow orchestration

ui/services
  project, generation, revision, license, knowledge services

openbrep
  Streamlit-free domain engine

tests
  behavior contracts that protect AI/tool-driven refactors
```
