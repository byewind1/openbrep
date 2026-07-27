# OpenBrep Agent Instructions

This file is the repository entry point for AI coding agents.

## 接手任务前必读（Session Handoff）

**在开始任何开发工作前，先查阅以下内容，不要只靠 git log 推断背景：**

1. **最新 handoff 文档**（了解上次停在哪、未完成事项）：
   - 目录：`/Users/ren/Library/Mobile Documents/iCloud~md~obsidian/Documents/库/01-Projects/dev开发/OpenBrep 开发/`
   - 找最新的 `handoff-YYYY-MM-DD.md` 文件阅读

2. **相关主题文档**（了解历史决策和架构思考）：
   - 同上目录，找与当前任务相关的主题文档

3. **再看代码**。commit message 只是摘要，Obsidian 文档才有决策背景。

---

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

Product posture:

- OpenBrep must behave like a professional GDL workbench, not a demo toy or
  throwaway chatbot shell.
- File, source, compile, and settings flows need explicit state, clear
  persistence semantics, and visible user feedback.
- Do not silently write user configuration from incidental UI changes. Settings
  panels should use draft state plus an explicit save action, with saved/dirty
  feedback, unless the maintainer explicitly approves autosave for that exact
  control.
- Sample/demo objects may exist as explicit fixtures or examples, but must not
  replace real empty/new/open/save workspace states.

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

1. Do not reintroduce imports of the retired Streamlit `ui/` package. It was
   removed from the repo; code and tests must import from `openbrep.*` and pass
   in a clean environment (CI). Local site-packages leftovers are not a safety net.
2. Do not treat `.gsm` as editable source. HSF project directories are source.
3. Do not bypass `HSFProject` for source state.
4. Do not rewrite `run_agent_generate` behavior without tests.
5. Do not change generation intent routing order without updating tests.
6. Do not silently drop geometry in the Blender importer — unsupported
   operations degrade to explicit warnings or clear errors, never to silent
   empty output.
7. Keep bpy/bmesh/mathutils stubs in one place
   (`openbrep/importers/blender_script/mesh_capture.py`, `mathutils_shim.py`).
   Do not scatter ad-hoc fake modules.
8. Do not add runtime dependencies without declaring them in `pyproject.toml`
   (CI installs only declared deps).
9. Do not silently write user configuration from incidental UI changes (draft
   state + explicit save — see Project Mission).
10. Do not break the current flat workspace layout.

## Architecture Hygiene

Prevent architecture drift by coding to product seams, not buttons or temporary
UI flows. New behavior must first choose one seam:

```text
HSF Source Session
AI Workbench
Preview Verification
Knowledge Memory
Archicad Adapter
Blender Importer (BS2G)
React Workbench Shell
```

Rules:

- `openbrep/workbench_api.py` (`WorkbenchSession`) is the composition root.
  Keep real behavior in testable service modules (`openbrep/workbench/*_service.py`).
- Do not grow a service by mixing unrelated seams. Split when a module starts
  combining chat routing, source mutation, preview, knowledge, and adapter
  behavior.
- Every extracted module needs a small contract test for its public interface.
- Prefer a small, deep interface over many pass-through helpers.

## Where Code Belongs

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

If unsure, keep `workbench_api.py` as a thin adapter and place real behavior in
a testable module.

## Required Tests

Run targeted tests while editing, then full tests before merge:

```bash
python -m pytest tests/ -q
```

Frontend (when touching `frontend/`):

```bash
cd frontend
npx vitest run
npx tsc --noEmit -p tsconfig.app.json
```

Common targeted sets:

```bash
python -m pytest tests/test_blender_script_importer.py tests/test_bs2g_mesh_loft.py tests/test_bs2g_shim.py -q
python -m pytest tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py -q
python -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
python -m pytest tests/test_gdl_previewer.py tests/test_blender_script_importer.py -q
```

## Current Baseline

As of 2026-07-28:

```text
python tests: 990 passed, 28 subtests passed
frontend: 153 passed (vitest) + tsc clean
CI (Tests workflow): pytest / react-workbench / scorecard-mock all green
```

Architecture notes:

- The Streamlit `ui/` package was retired (see `40aa6af` and later cleanup) and
  must not be imported or resurrected. The shell is the React workbench
  (`frontend/`) talking to a local API (`openbrep/workbench_api.py`), packaged
  with a Tauri desktop shell (`src-tauri/`).
- Semantic repair loop (2026-07-28): after compile succeeds, CREATE and
  MODIFY/DEBUG/REPAIR run `verify_semantics`; blocking issues trigger bounded
  accept/rollback repair rounds in `openbrep/runtime/semantic_repair.py`.
  `TaskResult.success` is the verification report's `passed` (delivery gate) —
  do not hardcode it back to `True`.
- Naming alignment (2026-07-28): `openbrep/naming_alignment.py` renames
  parameters to a pluggable naming convention (synonym dictionary + role-aware
  reserved-name rules; A/B/ZZYZX/AC_* are never rename sources; string-literal
  references like `VALUES "name"` are replaced only on whole-string match).
  Currently consumed by the benchmark runner; production wiring
  (project-level `naming_convention.toml`) is deliberately not done yet.
- Benchmark CREATE tasks run through the production `TaskPipeline` path (not
  the legacy `GDLAgent.run`); `benchmark/runner.py --jobs N` parallelizes
  suites (default 4, 1 = serial).
- The workbench API serializes mutating requests through a session-level lock
  (`openbrep/workbench/request_gate.py`); new routes default to locked.
- Tests must stay isolated from the developer's real `./config.toml` via
  `tests/conftest.py` (`GDL_AGENT_CONFIG` points at a tmp copy).

Important architecture boundaries already exist:

```text
openbrep/workbench_api.py
openbrep/workbench/*_service.py
openbrep/workbench/request_gate.py
openbrep/runtime/pipeline.py
openbrep/runtime/semantic_repair.py
openbrep/naming_alignment.py
openbrep/importers/blender_script/*
frontend/src/workbench/*
frontend/src/state/*
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

## Release / Installer SOP

> ⚠️ 本节写于 Streamlit/PyInstaller 时代，安装包流水线正在向 Tauri
> （`.github/workflows/release-tauri.yml`）迁移；涉及 Streamlit 打包的段落
> （如 "Known PyInstaller/Streamlit packaging requirements"）已过期，发布前
> 请与维护者确认当前流程。

Only run this section when the user explicitly asks for a release, installer
build, version bump, or public package update. Do not tag or publish a release
for ordinary code/documentation changes.

Release tags are immutable. If a pushed tag or GitHub Release workflow is wrong,
do not rewrite or force-push that tag. Fix the issue in a new commit and publish
a follow-up patch tag.

### When To Use This SOP

Use it for requests such as:

- "做一个小版本更新"
- "发布 vX.Y.Z"
- "打包安装包"
- "让 GitHub Release 可下载"
- "更新安装方式并发布"

Do not use it for normal feature/fix/docs work unless the user explicitly asks
to publish the result.

### Preflight

```bash
git switch main
git status --short --branch
git rev-parse main
git rev-parse origin/main
python -m pytest tests/ -q
```

`main` and `origin/main` must point to the same commit before tagging. The
working tree should be clean except for explicitly ignored local artifacts.

### Version And Docs

For a release version `vX.Y.Z`, update all version sources and release-facing
docs together:

```text
pyproject.toml
openbrep/__init__.py
README.md
README.zh-CN.md
INSTALL_CN.md
docs/releases/vX.Y.Z.md
tests/test_pipeline_modify.py release/version assertions
```

If the release changes install or packaging behavior, also update:

```text
.github/workflows/build-installers.yml
docs/RELEASE_PROCESS.md
docs/install_distribution_strategy_*.md when relevant
```

### Verification

Run:

```bash
python -m pytest tests/ -q
python -m pip wheel . -w /tmp/openbrep_wheel_check --no-deps
```

For installer or packaged-launcher changes, package-level verification must
exercise the downloaded/generated zip itself, not the local `obr` command:

```bash
python scripts/package_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
python scripts/package_browser_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
```

Do not treat `/_stcore/health` alone as installer success. Streamlit can return
health while `/` is `404`, and `/` can load before the app script exposes a
missing frozen dependency. The browser smoke must pass with `ok=true`,
`page_ok=true`, and no `error_markers`.

Known PyInstaller/Streamlit packaging requirements:

```text
streamlit/static must be bundled at streamlit/static
streamlit_ace/frontend/build must be bundled at streamlit_ace/frontend/build
streamlit.runtime.scriptrunner hidden imports must be included
```

Always test from a fresh extraction directory. Do not reuse an old
`/tmp/openbrep-install-test` directory when validating a fixed installer.

For installer workflow changes, also validate workflow syntax locally if
possible:

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-installers.yml"); puts "yaml ok"'
```

### Commit And Tag

```bash
git add ...
git commit -m "type: concise release summary"
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
git tag -a vX.Y.Z -m "OpenBrep vX.Y.Z"
git rev-parse main
git rev-parse 'vX.Y.Z^{}'
git push origin vX.Y.Z
```

The two `rev-parse` commands for `main` and `vX.Y.Z^{}` must print the same
commit hash.

### GitHub Release Verification

After pushing a `v*` tag, check the installer workflow:

```bash
gh run list --workflow "Build installers" --limit 5
gh run watch <run-id> --exit-status
gh release view vX.Y.Z --json tagName,name,url,assets,isDraft,isPrerelease,targetCommitish
```

The GitHub Release should contain the expected installer assets:

```text
OpenBrep-free-macOS.zip
OpenBrep-free-Windows.zip
```

The Release notes must state platform compatibility explicitly:

```text
macOS CPU architecture: arm64 / x86_64 / universal
Minimum macOS version: derived from the packaged binary dependencies
Windows architecture and minimum tested version
```

For macOS, do not infer compatibility from the filename alone. Check the
published zip itself. The effective minimum macOS version is the highest
`minos` value among the frozen executable and bundled `.dylib` / `.so` files.

If the workflow fails after the tag is pushed, fix the workflow in a new commit
and publish the next patch tag. Do not delete, move, or overwrite the failed
release tag unless the human maintainer explicitly instructs that destructive
operation.

### Known Risks

- Installer builds are slower than normal tests and consume GitHub Actions
  minutes.
- Release tags are public coordination points; a mistaken tag can confuse users.
- GitHub Release assets may expose unfinished or untested packages if the tag is
  pushed too early.
- Packaging workflows may fail for platform-specific reasons not covered by unit
  tests.
- GitHub Actions and GitHub CLI behavior can change; always verify the release
  page and assets after the workflow completes.

## Manual Risk

Unit tests do not fully cover React workbench behavior or real Archicad/Tapir
integration. For changes touching UI, compile, generation, vision, or Tapir,
perform or clearly request manual smoke testing:

```text
obr                                  # launch the workbench UI
create simple object
modify existing object
explain without mutation
import .gdl
import .gsm if LP_XMLConverter is available
import Blender .py (primitive script + bmesh loft script)
load HSF directory
preview 2D/3D (all display modes)
compile versioned .gsm
test Tapir/Archicad when available
```
