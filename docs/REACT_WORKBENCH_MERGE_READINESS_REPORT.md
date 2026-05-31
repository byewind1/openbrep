# React Workbench Merge Readiness Report

Date: 2026-05-31  
Branch: `react-workbench-poc`  
Compared against: `origin/main` at `d6643960bbedcbc2d49f64bf39841e4127e8be45`  
Branch head before final launcher hardening: `44a7d4af15e219efd086a5bc4dbdc19ee72957f4`

Update: `origin/main` has since been merged into `react-workbench-poc`; the
`obr7` add/add conflict is resolved in this branch.

## Executive Decision

`react-workbench-poc` is now a credible primary UI candidate for daily HSF/GDL
work, but it should not be silently merged as the default UI without one final
manual product check.

Recommended next state:

- Open a merge/PR readiness review from `react-workbench-poc`.
- Keep Streamlit as fallback after merge.
- Do not delete Streamlit UI.
- Do not claim full Streamlit parity yet.
- Treat React Workbench as the default candidate for code-first GDL work after
  one manual smoke with `./obr7`.

The branch is no longer just a POC in the throwaway sense. It is a real
Workbench shell backed by tests and readiness gates. The remaining question is
product-default policy, not whether the React direction is viable.

## Evidence Snapshot

Branch scope:

```text
Commits ahead of origin/main: 53
Changed files: 68
Diff size: about 15.6k insertions
Primary additions:
  frontend/ React + Vite + Zustand + Three.js + Monaco workbench
  openbrep/workbench_api.py local API adapter
  scripts/obr7.py and ./obr7 launcher
  scripts/workbench_readiness_gate.py
  scripts/workbench_vision_smoke.py
```

Latest verified gates on this branch:

```text
python -m pytest tests/test_obr7_launcher.py -q
  6 passed

./obr7 --no-open
  verified with existing API on 8765
  auto-shifted API to 8766
  started web on 5174
  curl http://127.0.0.1:8766/api/snapshot returned ok=true
  curl http://127.0.0.1:5174/ returned OpenBrep Workbench HTML

python scripts/workbench_readiness_gate.py --pretty
  ok: true
  backend workbench api: 67 passed
  backend vision smoke tests: 3 passed
  frontend tests: 64 passed
  frontend build: passed
  vision smoke: skip, config not found

python scripts/workbench_readiness_gate.py --full --pretty
  ok: true
  backend full tests: 778 passed, 2 warnings, 10 subtests passed
  frontend tests: 64 passed
  frontend build: passed
  browser smoke: pass, temporary HSF loaded, Monaco edited, Save clicked,
  disk script write verified, Mock Compile clicked, Diagnostics passed

python -m pytest tests/ -q
  778 passed, 2 warnings, 10 subtests passed

cd frontend && npm run test -- --run
  64 passed

cd frontend && npm run build
  passed
```

## Capability Status

React Workbench now covers the daily HSF/GDL loop:

- Open HSF directory.
- Import `.gdl` as HSF.
- Import/decompile `.gsm` through existing converter path.
- Edit scripts in Monaco.
- Save scripts back to HSF.
- Run mock compile diagnostics.
- Configure real compiler mode/path/output.
- Reveal compiled artifact path.
- Preview 3D and 2D.
- Edit parameters, metadata, add/delete parameters, validate paramlist.
- Save/list/restore revisions.
- Create HSF projects from assistant prompts.
- Modify current HSF through assistant.
- Persist assistant history per project.
- Browse/adopt code from assistant history.
- Review/summarize/edit/ignore/delete project memory lessons.
- Attach reference image for create/generate and route it through existing
  pipeline vision fields.
- Run local readiness gate.

Remaining Streamlit parity gaps:

- Tapir/Archicad live bridge is not migrated.
- Pro license / Pro knowledge package UX is not migrated.
- Image/Vision has API/UI plumbing and smoke script, but still needs a real
  vision-capable model smoke before full parity claim.
- Work directory setting is not explicit in React; current flow uses direct
  project paths and export output choices.
- Advanced memory curation still lacks merge controls.
- Paramlist has editable XML access, but no dedicated summary/preview panel.

## Architecture Assessment

The branch mostly respects the current OpenBrep architecture rules:

- No substantial new behavior was added to `ui/app.py`.
- HSF directories remain source; `.gsm` remains artifact.
- Source mutation goes through `HSFProject`.
- React does not implement GDL parsing, compiling, preview logic, or image
  understanding.
- `openbrep/workbench_api.py` acts as a local API adapter over existing domain
  modules.
- Frontend state is split into action slices rather than one giant store file.
- `frontend/src/App.tsx` is thin.
- `frontend/src/workbench/WorkbenchApp.tsx` is a composition root, not a domain
  implementation module.

Main architectural risk:

```text
openbrep/workbench_api.py is now a large adapter module.
```

This was acceptable during migration because it kept the local RPC seam stable
and avoided creating premature backend modules. It should not continue growing
without a split plan.

Recommended future seam if growth continues:

- `openbrep/workbench_api.py`: routing and session lifecycle only.
- `openbrep/workbench_memory.py`: memory lesson API adapter.
- `openbrep/workbench_project_io.py`: open/import/export source workflows.
- `openbrep/workbench_generation.py`: assistant create/modify adapter.
- `openbrep/workbench_diagnostics.py`: compile/diagnostics adapter.

Do not split this before merge unless a specific bug forces it. The current
tests cover the adapter surface, and a pre-merge split would increase merge
risk without changing user-visible behavior.

## Product Readiness

Ready to use as primary path for:

- GDL developers who work directly with HSF source.
- AI-assisted create/modify of library parts.
- Code-first edit/save/compile/preview iteration.
- Local project memory and assistant history workflows.

Not ready to be the only UI for:

- Archicad/Tapir live workflows.
- Pro licensing and Pro knowledge management.
- Users relying on Streamlit-specific image workflows until real vision smoke
  passes with configured credentials.

## Recommended Merge Plan

1. Run the readiness gate:

```bash
python scripts/workbench_readiness_gate.py --full --pretty
```

2. Run one browser `./obr7` smoke. The launcher now auto-shifts from the default
   API port range to a high fallback range if needed, while explicit
   `--api-port` / `OBR7_API_PORT` values remain strict:

```text
python scripts/workbench_browser_smoke.py --pretty
```

This verifies that `obr7` starts both the API and React frontend, Playwright can
open the page, a temporary HSF project can be loaded, Monaco can edit
`3d.gdl`, Save writes the script back to disk, Mock Compile can be clicked, and
the Diagnostics drawer reports a successful compile. A human product smoke is
still useful before changing the default UI policy.

3. If a vision-capable model is configured, run:

```bash
python scripts/workbench_vision_smoke.py --config ~/.openbrep/config.toml --output-dir /tmp/openbrep-vision-smoke --pretty
```

4. Merge with Streamlit fallback intact.

5. After merge, make `obr7` / React Workbench the documented recommended path
for code-first HSF/GDL work, but keep Streamlit documented as fallback for Tapir,
Pro, and any remaining parity gaps.

## No-Go Conditions

Do not make React the default-only UI if any of these are true:

- `python scripts/workbench_readiness_gate.py --full --pretty` fails.
- `./obr7` cannot open and edit a real HSF directory.
- Local API port handling regresses.
- Monaco editor cannot save back to disk.
- Mock compile diagnostics no longer appear in the bottom drawer.
- Settings drawer cannot persist compiler/LLM changes.

## Next Best Work

If the manual smoke passes:

1. Prepare merge/PR notes.
2. Add README/INSTALL guidance for `./obr7`.
3. Keep Tapir/Pro migration as later focused tracks.

If the manual smoke fails:

1. Fix launch/open/save/compile first.
2. Do not continue adding features.
3. Keep React behind explicit `./obr7` until the readiness gate and manual smoke
   both pass.
