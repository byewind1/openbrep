# OpenBrep Architecture

Date: 2026-08-05  
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

Architecture decision records currently live in Chinese:

- [ADR 0001: HSF 项目目录是 OpenBrep 的源格式](adr/0001-hsf-as-source.zh-CN.md)
- [ADR 0002: AI 生成写入由 generation service 边界承接](adr/0002-generation-service-boundary.zh-CN.md)
- [ADR 0003: 自定义 Skill 是用户经验的可追溯输入](adr/0003-custom-skill-workflow.zh-CN.md)

## Current State

The retired Streamlit `ui/` package no longer exists in the repository. The
product shell is now a React workbench (`frontend/`) talking to a local Python
API (`openbrep/workbench_api.py`), packaged as a Tauri v2 desktop app
(`src-tauri/`). The `obr` CLI (`cli/main.py`) and the React workbench share the
same domain core and generation pipeline.

Baseline:

```text
python tests: 1078 passed, 64 subtests passed
frontend: 166 passed (vitest) + tsc clean
```

`openbrep/workbench_api.py` (`WorkbenchSession`) is the composition root. Real
behavior lives in testable service modules under `openbrep/workbench/`.

## Architectural Layers

Use this seam model when adding or moving code:

```text
React workbench UI (pages, panels, store, actions)
  frontend/src/workbench/*
  frontend/src/components/*
  frontend/src/state/*

Local API session (composition root) + backend services
  openbrep/workbench_api.py
  openbrep/workbench/*_service.py

Tapir/Archicad workflow
  openbrep/tapir_bridge.py
  openbrep/tapir_controller.py
  openbrep/workbench/tapir_service.py
  openbrep/workbench_tapir.py

AI generation workflow
  openbrep/runtime/pipeline.py

Blender script → GDL importer (BS2G)
  openbrep/importers/blender_script/*
  primitive mode: parser.py / mapper.py / generator.py
  mesh mode: mesh_capture.py / loft_detect.py / loft_gdl.py / mesh_gdl.py

CLI (obr)
  cli/main.py

Domain logic
  openbrep/*
```

Rules:

- `WorkbenchSession` stays a thin adapter; real behavior goes into testable
  `openbrep/workbench/*_service.py` modules.
- Do not grow a service by mixing unrelated seams. Split when a module starts
  combining chat routing, source mutation, preview, knowledge, and adapter
  behavior.
- The React workbench is the only UI surface. Never reintroduce a retired
  Streamlit `ui/` package.
- Prefer a small, deep interface over many pass-through helpers.

## Runtime Flow

```text
User input
  ├─ natural language
  ├─ image
  ├─ .gdl / .txt import
  ├─ .gsm import
  ├─ Blender .py script import
  └─ HSF directory load

React workbench UI
  ├─ frontend/src/workbench/WorkbenchApp.tsx   (composition root)
  ├─ frontend/src/state/*                       (store + actions)
  └─ frontend/src/components/*                  (panels)

Local API boundary (HTTP/JSON over ThreadingHTTPServer)
  ├─ openbrep/workbench/http_server.py          (transport)
  └─ openbrep/workbench_api.py
     WorkbenchSession — composition root, session-level mutation lock

Service layer
  ├─ openbrep/workbench/project_service.py
  ├─ openbrep/workbench/compiler_service.py
  ├─ openbrep/workbench/assistant_service.py
  ├─ openbrep/workbench/preview_service.py
  ├─ openbrep/workbench/tapir_service.py
  ├─ openbrep/workbench/memory_service.py
  └─ openbrep/workbench/settings_service.py

Domain core
  ├─ openbrep/hsf_project.py
  ├─ openbrep/runtime/pipeline.py   (TaskPipeline)
  ├─ openbrep/runtime/router.py     (IntentRouter)
  ├─ openbrep/compiler.py
  ├─ openbrep/verification.py
  ├─ openbrep/gdl_parser.py
  ├─ openbrep/paramlist_builder.py
  ├─ openbrep/validator.py
  └─ openbrep/knowledge.py

Output
  ├─ editable HSF project directory
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
    .openbrep/
      knowledge/
        project.toml
        01_context.md
      memory/
        decisions.md
        learnings/
      revisions/
```

Rules:

- A `.gsm` file is a compiled deliverable, not the source format.
- A `.gdl` file alone is not enough to represent a complete library part.
- `paramlist.xml` and `scripts/*.gdl` must be treated as one source unit.
- Compiling must not create a new source directory.
- Importing `.gsm` may create a new stable HSF project directory.
- Modifying an object updates the current HSF project directory directly, with
  revision metadata providing traceability.
- Project-level `.openbrep/knowledge/` is a higher-priority context source than
  global knowledge; modification summaries and error lessons persist under
  `.openbrep/memory/` for later sessions.

See also: [project_layout.md](project_layout.md)

## Key Modules

### `frontend/src/workbench/WorkbenchApp.tsx`

Role: React workbench composition root.

Allowed responsibilities:

- Assemble the layout (`ResizableWorkspaceGrid`, left/right rails), top menu,
  and bottom drawer.
- Wire the Zustand store (`useWorkbenchStore`) to panel components.
- Lazy-load heavy panels (settings, revision history).

Avoid adding:

- Workflow orchestration. Extract a feature module under
  `frontend/src/workbench/<feature>/` when a workflow needs local state,
  validation, or multiple controls.
- Python/GDL interpretation logic in React modules.

### `openbrep/workbench_api.py`

Role: local API composition root (`WorkbenchSession`) and route dispatch.

Owns:

- A single `WorkbenchSession` instance holding current-project state
  (`HSFProject`), config, compiler mode, and service instances.
- `session_id` (one per backend process) and `project_epoch` (bumped on every
  project switch; the frontend discards stale async results).
- Serialization of mutating requests through a session-level RLock, so a slow AI
  generation cannot interleave with a fast compile/save on the same project.
- Route dispatch to the service layer.

Keep this module a thin adapter. Real behavior lives in `workbench/*_service.py`.

### `openbrep/workbench/http_server.py`

Role: ThreadingHTTPServer transport.

Owns HTTP request/response plumbing and static file serving (Tauri single-port
mode). Route dispatch lives in `workbench_api.py`.

### `openbrep/workbench/request_gate.py`

Role: request serialization policy for the HTTP transport.

- Mutating routes are serialized through the session-level lock.
- Read-only and native-dialog routes stay lock-free.
- New routes default to locked (safe direction).

### `openbrep/workbench/*_service.py`

Role: application-level business workflows behind the local API.

Examples:

- `project_service.py` — open/import/load/save HSF projects and snapshots.
- `compiler_service.py` — compile current project to versioned `.gsm`.
- `assistant_service.py` — AI generate/modify/explain/repair dispatch.
- `preview_service.py` / `three_preview.py` — 2D/3D preview payloads.
- `tapir_service.py` — Tapir/Archicad parameter read/write.
- `memory_service.py` — workspace-level learning memory.
- `settings_service.py` — runtime/compiler/LLM settings with draft state.
- `revision_service.py`, `project_parameter_service.py`,
  `project_script_service.py`, `project_session_service.py`,
  `blender_import_service.py`, `git_service.py`.

Rules:

- Keep real behavior in testable modules, not in `workbench_api.py`.
- Do not mix unrelated seams in one service module.

### `openbrep/runtime/pipeline.py`

Role: domain pipeline for LLM task execution (`TaskPipeline`).

Owns:

- Intent dispatch (via `IntentRouter`), task execution, tracing.
- Chat, GDL create, modify, debug, repair handlers.
- Deterministic micro-modify interception before the LLM modify path.
- Verification integration: after compile success, CREATE and
  MODIFY/DEBUG/REPAIR run semantic verification with bounded repair rounds.

The pipeline stays independent of the UI and HTTP layers. It can receive
callbacks such as `on_event` and `should_cancel`, but it must not depend on
session state or the HTTP transport.

### `openbrep/runtime/router.py`

Role: deterministic intent classification (`IntentRouter`).

Intent values: `CREATE`, `MODIFY`, `DEBUG`, `REPAIR`, `IMAGE`, `CHAT`. The
router classifies the first five; `REPAIR` is pinned explicitly by repair
callers. Classification is keyword/signature based (debug prefixes,
error-log signatures, knowledge questions) with an optional LLM fallback for
ambiguous no-project input. Do not change routing order without updating tests.

### `openbrep/runtime/micro_modify.py`

Role: deterministic parameter-value changes ("把层板数改成 5").

High-precision detection only: name/description resolution, Length unit
conversion, boolean words. Anything ambiguous, compound, or question-shaped
returns `None` and falls through to the LLM modify path unchanged. When it hits,
the change completes at zero token cost while still running compile.

### `openbrep/runtime/semantic_repair.py`

Role: bounded accept/rollback repair loop after semantic verification.

If `verify_semantics` reports blocking issues, the loop runs a bounded number of
repair rounds, then accepts or rolls back. The repair outcome is recorded for
observability; it does not silently drop delivery-gate failures.

### `openbrep/verification.py`

Role: unified verification report.

Aggregates static/lint/compile/plan checks into a `VerificationReport`
(`passed`, `counts`, `compile_status`). `TaskResult.success` is the report's
`passed` — the delivery gate. Do not hardcode it back to `True`.

### `openbrep/semantic_verifier.py`

Role: deterministic geometry/behavior verification (`verify_semantics`).

Runs the lightweight `gdl_previewer` on generated scripts to catch
"compiles but the geometry is wrong": mesh non-empty/non-degenerate, bounding
box matches declared A/B/ZZYZX, and declared parameters actually move geometry.

### `openbrep/naming_alignment.py`

Role: pluggable parameter-naming convention.

Renames parameters to a synonym-dictionary-driven convention; A/B/ZZYZX/AC_*
are never rename sources, and string-literal references are replaced only on
whole-string match. `detect_reserved_param_misuse()` runs in CREATE and MODIFY
verification stages: reserved names used in the wrong dimensional role become
blocking `reserved_param_semantic_bug` checks (delivered with the warning, not
auto-repaired).

### `openbrep/importers/blender_script/*`

Role: Blender Python script → GDL importer (BS2G).

- Primitive mode: `parser.py` / `mapper.py` / `generator.py`.
- Mesh mode: `mesh_capture.py` / `loft_detect.py` / `loft_gdl.py` /
  `mesh_gdl.py`.
- `mathutils_shim.py` keeps bpy/bmesh/mathutils stubs in one place.
- Unsupported operations degrade to explicit warnings or clear errors, never to
  silent empty output.

### Tapir/Archicad workflow

Role: read selected object parameters and write safe edits back to Archicad.

```text
openbrep/tapir_bridge.py
openbrep/tapir_controller.py
openbrep/workbench/tapir_service.py
openbrep/workbench_tapir.py
```

### `cli/main.py` and `scripts/obr7.py`

Role: CLI (`obr`) and local UI startup orchestration.

- `cli/main.py` is the Typer app: `create`, `modify`, `compile`, `repair`,
  `chat`, `configure`, `doctor`, `history`, `rollback`, `compare`,
  `revision`, `memory`, `import-blender`.
- Running `obr` with no subcommand launches the React workbench through
  `scripts/obr7.py`: dev mode starts the local API plus the Vite dev server and
  opens the browser; `--tauri` mode serves the built frontend on one port
  without launching a browser; `--daemon` runs detached with a state file.

### `src-tauri/`

Role: Tauri v2 desktop shell (Rust).

`src/main.rs` spawns the Python sidecar, waits for the `OBR7_READY_URL` signal,
opens the Webview window, and on window close sends `/api/shutdown` and waits
for the Python process to exit (orphan-process protection).

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

Modification updates the current HSF project:

```text
auto_apply=True
  → write scripts/params immediately

auto_apply=False
  → legacy-compatible call path; generation plans still apply directly
```

### Compile

Compilation reads the current HSF project directory and writes:

```text
workspace/output/ObjectName_vN.gsm
```

Do not create a new HSF directory during compile.

## Generation Semantics

`TaskPipeline.execute()` is the stable high-level entry point used by the CLI,
the React workbench assistant service, and tests. Intent resolution happens in
`IntentRouter.classify()` when the caller does not pin an intent explicitly.

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

Do not change this order without updating tests.

Dispatch inside `execute()`:

- MODIFY/DEBUG/REPAIR default to the budgeted agent loop
  (`request.agent_loop`); deterministic micro-modify is tried first and wins at
  zero token cost when it matches.
- After compile succeeds, CREATE and MODIFY/DEBUG/REPAIR run `verify_semantics`;
  blocking issues trigger bounded accept/rollback repair rounds in
  `runtime/semantic_repair.py`.
- `TaskResult.success` is the verification report's `passed` (delivery gate).
  Do not hardcode it back to `True`.

## Verification Loop

Generated or modified scripts pass through a deterministic verification ring
before delivery:

```text
compile gate
  openbrep/compiler.py
  MockHSFCompiler or HSFCompiler (LP_XMLConverter)

unified verification report
  openbrep/verification.py
  static / lint / compile / plan checks → VerificationReport.passed

semantic verification
  openbrep/semantic_verifier.py::verify_semantics
  mesh non-empty, bounding box vs A/B/ZZYZX, param responsiveness

repair loop
  openbrep/runtime/semantic_repair.py
  bounded accept/rollback rounds when blocking issues are found

naming alignment
  openbrep/naming_alignment.py
  reserved-param misuse → blocking reserved_param_semantic_bug checks
  (delivered with warning, not auto-repaired)

deterministic intercept
  openbrep/runtime/micro_modify.py
  pure parameter-value changes skip the LLM path entirely
```

The deliverable is only "successful" when the verification report passes.
`TaskResult.success` must not be unconditionally forced to `True`.

## Local API Session Contract

The React workbench talks to a stateful `WorkbenchSession` singleton over HTTP.
Treat the session contract as public application state.

Important fields:

```text
project            current HSFProject (or None)
source             "empty" | "hsf" | "gsm" | "gdl" | "blender"
source_path        origin of the loaded project
session_id         one per backend process
project_epoch      bumped on project switch; frontend drops stale async results
compiler_mode      "mock" | "lp"
recent_project_paths
```

Rules:

- Mutating requests are serialized by the session-level lock
  (`request_gate.py`); read-only routes stay lock-free.
- Clear preview state after script or parameter mutations.
- Capture a project snapshot before irreversible AI writes.
- Settings panels use draft state plus an explicit save action. Do not write
  user configuration from incidental UI changes.

## Testing Strategy

The current baseline is:

```text
python -m pytest tests/ -q
1078 passed, 64 subtests passed

frontend
cd frontend && npx vitest run
npx tsc --noEmit -p tsconfig.app.json
```

Required test scope by change type:

```text
Workbench API / service change
  → tests/test_workbench_api.py
  → tests/test_workbench_services.py
  → tests/test_workbench_concurrency.py

Generation change
  → tests/test_pipeline_create_compile.py
  → tests/test_pipeline_modify.py
  → tests/test_micro_modify.py
  → tests/test_pipeline_semantic_repair.py
  → full test suite before merge

Verification / naming change
  → tests/test_semantic_verifier.py
  → tests/test_naming_alignment.py
  → tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py

Blender importer change
  → tests/test_blender_script_importer.py
  → tests/test_bs2g_mesh_loft.py
  → tests/test_bs2g_shim.py

Preview change
  → tests/test_gdl_previewer.py tests/test_three_preview.py
  → preview smoke/manual check when rendering behavior changes

Tapir/Archicad change
  → unit tests with mocks
  → manual Archicad checklist before release

Frontend change
  → npx vitest run
  → npx tsc --noEmit -p tsconfig.app.json
```

Before merging to `main`:

```bash
python -m pytest tests/ -q
cd frontend && npx vitest run && npx tsc --noEmit -p tsconfig.app.json
```

## Benchmark Golden Corpus Regression

Hosted LLMs are nondeterministic even at temperature 0, so benchmark effect
verification uses a golden-corpus record/replay flow instead of rerunning live
models:

```text
record once with a real LLM
  python -m benchmark.runner --suite benchmark/tasks/create/ --llm-record benchmark/fixtures/llm_corpus/create.jsonl
  python -m benchmark.runner --suite benchmark/tasks/modify/ --llm-record benchmark/fixtures/llm_corpus/modify.jsonl

replay deterministically
  python -m benchmark.runner --suite benchmark/tasks/create/ --llm-replay benchmark/fixtures/llm_corpus/create.jsonl
```

CI's `benchmark-replay` job replays the committed corpus and compares against
`benchmark/baseline.json` (PASS→FAIL, added criteria_failures, or fewer passes
turn the job red).

Corpus rules:

- Re-record the corpus only when the prompt sent to the LLM changes
  (`knowledge/`, `user_knowledge/`, `skills/`, prompt assembly, model/provider,
  learning-memory injection).
- Post-generation processing (linter, static checker, naming alignment,
  semantic verifier, repair acceptance, compile wiring, assertion logic) does
  not change the prompt, so it is validated by replaying the existing corpus.
- A replay that misses the corpus is a feature: it blocks silent prompt changes
  that were not re-recorded.
- Baseline updates must not regress: `benchmark/update_baseline.py` rejects
  degradation-direction updates.

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

Avoid long-running unmerged branches for workbench/service refactors. The
session has many shared state paths, so stale branches become expensive quickly.

## Rules For AI Coding Agents

When an AI coding tool works on this repository, it must follow these rules:

Start from the requested outcome and define success criteria before choosing
steps. Architecture rules are guardrails for that loop: inspect the current
boundary, make the smallest coherent change, test, fix, retest, and only finish
when the requested outcome is verified. Do not stop at a plan when the change is
implementable in the current session.

1. Read this document before changing architecture.
2. Check `git status --short --branch` before editing.
3. Do not reintroduce imports of the retired Streamlit `ui/` package.
4. Do not treat `.gsm` as editable source. HSF project directories are source.
5. Do not bypass `HSFProject` for source state.
6. Do not rewrite `run_agent_generate` behavior without tests.
7. Do not change generation intent routing order without updating tests.
8. Do not silently drop geometry in the Blender importer — unsupported
   operations degrade to explicit warnings or clear errors.
9. Keep bpy/bmesh/mathutils stubs in one place
   (`importers/blender_script/mesh_capture.py`, `mathutils_shim.py`).
10. Do not add runtime dependencies without declaring them in `pyproject.toml`.
11. Do not silently write user configuration from incidental UI changes.
12. Do not break the current flat workspace layout.

## Where To Put New Features

Use this decision table:

| Feature type | Preferred location |
|---|---|
| New workbench panel | `frontend/src/workbench/<feature>/`, `frontend/src/components/*` |
| New store/action logic | `frontend/src/state/actions/*` |
| New API route | `openbrep/workbench_api.py` (thin) + `openbrep/workbench/*_service.py` |
| New project import option | `openbrep/workbench/project_service.py`, `project_session_service.py` |
| New compile/version behavior | `openbrep/workbench/compiler_service.py`, `revision_service.py` |
| New AI generation behavior | `openbrep/runtime/pipeline.py` or a new `runtime/*` module |
| New deterministic parameter edit | `openbrep/runtime/micro_modify.py` |
| New verification rule | `openbrep/verification.py`, `openbrep/semantic_verifier.py` |
| New naming rule | `openbrep/naming_alignment.py` |
| New preview capability | `openbrep/workbench/preview_service.py` / `three_preview.py` + frontend viewport |
| New Tapir action | `openbrep/workbench/tapir_service.py`, `openbrep/tapir_bridge.py` |
| New GDL validation rule | `openbrep/validator.py`, `openbrep/gdl_linter.py`, `openbrep/static_checker.py` |
| New model/provider logic | `openbrep/config.py` (`PROVIDER_PROFILES`), `openbrep/llm.py` |
| New knowledge behavior | `openbrep/knowledge_selector.py`, `openbrep/knowledge.py` |
| New CLI command | `cli/main.py` |
| Blender importer change | `openbrep/importers/blender_script/*` |

## Current Refactor Milestones

Completed:

```text
Phase 4: React workbench becomes the default UI (v0.8.0)
Phase 5: Tauri desktop shell lands; the React workbench becomes the only UI (v1.0.0)
```

Completed in the latest cleanup:

```text
1. Domain logic migrated into openbrep/workbench/*_service.py behind
   workbench_api.py as the composition root.
2. Verification unified into openbrep/verification.py
   (static/lint/compile/plan checks → VerificationReport).
3. Semantic repair loop (runtime/semantic_repair.py) added after compile
   success; TaskResult.success is the verification report's passed.
4. Deterministic micro-modify (runtime/micro_modify.py) intercepts pure
   parameter-value changes before the LLM modify path.
5. Blender importer (BS2G) added for primitive and bmesh-mesh scripts.
```

## Manual Release Checklist

Before a release that touches UI, generation, compile, or Tapir:

```text
1. Launch the workbench (obr) or the Tauri app.
2. Create a simple object from natural language.
3. Modify an existing object.
4. Ask for explanation only and verify no script mutation occurs.
5. Import a .gdl file.
6. Import a .gsm file with LP_XMLConverter.
7. Import a Blender .py script (primitive + bmesh loft).
8. Load an existing HSF directory.
9. Run 2D/3D preview (all display modes).
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
frontend/src/workbench/*
  React workbench shell and panels

frontend/src/state/*
  store and actions

openbrep/workbench_api.py
  thin local API composition root

openbrep/workbench/*_service.py
  project, generation, compile, preview, memory, settings, tapir services

openbrep/runtime/*
  UI-independent domain pipeline, intent routing, repair

openbrep/*
  HSF/GDL domain engine

cli/main.py
  obr CLI

tests
  behavior contracts that protect AI/tool-driven refactors
```
