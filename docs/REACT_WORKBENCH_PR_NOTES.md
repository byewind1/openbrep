# React Workbench PR Notes

Date: 2026-05-31  
Branch: `react-workbench-poc`  
Current head: `f158f10`  
Compared against: `origin/main`

## Suggested PR Title

```text
feat(workbench): add React code-first OpenBrep Workbench
```

## Suggested PR Summary

This branch adds the React Workbench developer preview as a new code-first UI
path for OpenBrep while keeping the existing Streamlit UI intact.

Major additions:

- `./obr7` launcher for local Python API + React/Vite frontend.
- React + TypeScript + Zustand + Monaco + Three.js workbench shell.
- Local Workbench API adapter in `openbrep/workbench_api.py`.
- HSF project open/recent/import/export flows.
- Script/XML editing with save back through `HSFProject`.
- Mock compile diagnostics and compile settings.
- 3D and 2D preview panels.
- Parameter authoring/editing/deletion/validation.
- Assistant create/modify/explain flows with history and adopt-code support.
- Project memory review/summarize/edit/ignore/delete controls.
- Reference image attachment plumbing for assistant generation.
- Readiness and vision smoke scripts.

The branch intentionally does not remove or replace Streamlit. Streamlit remains
the fallback for Tapir/Archicad live bridge, Pro UX, and compatibility paths not
yet migrated.

## Current Diff Shape

```text
Commits ahead of origin/main: 59
Files changed: 74
Approximate diff: 16.2k insertions
Primary new areas:
  frontend/
  openbrep/workbench_api.py
  scripts/obr7.py
  scripts/workbench_readiness_gate.py
  scripts/workbench_vision_smoke.py
  tests/test_workbench_*.py
```

## Verification To Paste Into PR

Latest local verification:

```text
python -m pytest tests/test_obr7_launcher.py -q
  6 passed

python scripts/workbench_readiness_gate.py --full --pretty
  ok: true
  backend full tests: 770 passed, 2 warnings, 10 subtests passed
  backend vision smoke tests: 3 passed
  frontend tests: 64 passed
  frontend build: passed
  vision smoke: skip, config not found

./obr7 --no-open
  with an existing API already listening on 8765
  auto-shifted API to 8766
  started web on 5174
  /api/snapshot returned ok=true
  / returned OpenBrep Workbench HTML
```

Manual browser-level product smoke still recommended before making React the
documented default UI:

```text
open existing HSF
open 3d.gdl
edit one line
save
mock compile
switch 3D/2D preview
open Settings
verify compiler/LLM/memory panels
```

## Merge Policy

Recommended merge stance:

- Merge as an explicit developer-preview UI path.
- Keep `obr` / Streamlit available.
- Do not delete Streamlit code.
- Do not claim full Streamlit parity.
- Do not make React the only UI until Tapir/Pro/real vision smoke are handled.

Recommended post-merge documentation stance:

- Present `./obr7` as the recommended path for code-first HSF/GDL development.
- Present Streamlit as fallback for Tapir/Archicad live workflows and legacy
  compatibility.

## Known Risks

- `openbrep/workbench_api.py` is now a large local adapter. It is acceptable for
  this migration branch but should not keep growing without a split plan.
- Real vision smoke is skipped unless `~/.openbrep/config.toml` is configured.
- Tapir/Archicad live bridge is not migrated.
- Pro license / Pro knowledge package UX is not migrated.
- Frontend production bundle is currently large because Monaco/Three are in the
  main bundle; Vite reports a chunk-size warning, not a build failure.

## Suggested Follow-Up Issues

1. Split `openbrep/workbench_api.py` into route-only adapter plus focused
   workbench service modules.
2. Run and document a real vision-capable model smoke.
3. Migrate Tapir/Archicad live bridge into React Workbench or explicitly keep it
   Streamlit-only.
4. Decide React default policy after manual browser smoke.
5. Add code splitting for Monaco/Three if bundle size becomes a launch problem.

