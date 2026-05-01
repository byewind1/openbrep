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
test baseline: 474 passed, 6 subtests passed
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

## Release / Installer SOP

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
