# React Workbench Developer Preview

This is the modern OpenBrep workbench line. It is no longer a throwaway UI
prototype, but it should still be launched explicitly with `./obr7` until it is
merged and documented as the default path. Keep the Streamlit UI as fallback for
Tapir/Archicad live workflows, Pro UX, and remaining parity gaps.

## Goal

Provide a code-first HSF/GDL workbench:

- React + TypeScript + Vite shell
- Monaco editor for HSF scripts and XML source files
- Three.js / react-three-fiber preview panels
- Zustand state for local workbench state and actions
- Python local RPC backend that reuses `openbrep/*`
- Script edits save back through `HSFProject`
- Mock compile diagnostics appear in the bottom drawer
- Real HSF directories can be loaded by local path without going through Streamlit
- Assistant create/modify/explain uses the existing OpenBrep pipeline

## Run

```bash
./obr7
```

Open:

```text
http://127.0.0.1:<printed web port>
```

`obr7` starts the Python API and the React dev server together. If default ports
are busy, it automatically picks the next available ports. If the nearby default
range is also busy, it falls back to high ports near `19065` / `19074`. The
actual URLs are always printed.

Manual overrides:

```bash
OBR7_API_PORT=8765 OBR7_WEB_PORT=5174 ./obr7
./obr7 --api-port 8770 --web-port 5180 --no-open
```

Explicit ports are strict. If `OBR7_API_PORT=8765` or `--api-port 8765` is set
and that port is already occupied, `obr7` fails instead of silently changing the
port. Use a different explicit port or unset the environment variable.

## Verification

```bash
python -m pytest tests/test_obr7_launcher.py -q
python -m pytest tests/test_workbench_api.py -q
python scripts/workbench_readiness_gate.py --full --pretty
cd frontend
npm test -- --run
npm run build
```

## Current Scope

Included:

- Demo bookshelf HSF project served by Python
- Real HSF directory loading through `/api/project/load`
- Recent project persistence and reopen flow
- Native HSF directory picker through `/api/dialog/open-directory`
- Native LP_XMLConverter file picker through `/api/dialog/open-file`
- `/api/snapshot`, `/api/preview`, `/api/apply`
- Parameter apply persists values back to the loaded HSF source directory
- Monaco script/XML editing with save back to HSF
- Mock GSM compilation and diagnostics
- Compiler mode/path settings through `/api/settings/compiler`
- LP_XMLConverter compile path support when mode is `lp`
- HSF export and compiled artifact reveal
- Parameter authoring, editing, deletion, validation
- Explanation assistant through `/api/assistant`
- Pipeline-backed AI generation/modification through `/api/assistant/generate`
- Assistant history persistence and adopt-code flow
- Project memory status/review/summarize/preview/clear
- Memory lesson delete/ignore/edit
- Reference image attachment for assistant generation
- Left rail for project/scripts/settings entry points
- Main stage for code-first editing
- Right rail preview tabs for 3D / 2D / AI context
- Bottom drawer for diagnostics, revisions, and logs

Not included yet:

- Tauri packaging
- Archicad/Tapir integration
- Pro license / Pro knowledge package UX
- Full visual smoke with a real configured vision-capable model
- Dedicated paramlist summary/preview panel

## Next Decisions

- When to merge `react-workbench-poc` into `main`.
- How to split `openbrep/workbench_api.py` once the local adapter grows again.
- When to migrate Tapir/Archicad live workflows.
- Whether packaging should stay localhost-first or move to Tauri later.
