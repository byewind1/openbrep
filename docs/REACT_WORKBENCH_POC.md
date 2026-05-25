# React Workbench POC

This is a separate prototype for a modern OpenBrep workbench. It does not replace
the Streamlit UI yet.

## Goal

Prove the product direction:

- React + TypeScript + Vite shell
- Three.js / react-three-fiber live preview
- Zustand state for draft parameter values
- Python local RPC backend that reuses `openbrep/*`
- Parameter changes update preview immediately; applying changes is a separate action
- Real HSF directories can be loaded by local path without going through Streamlit

## Run

Terminal 1:

```bash
python -m openbrep.workbench_api --port 8765
```

Terminal 2:

```bash
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:5174
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8765`.

## Verification

```bash
python -m pytest tests/test_workbench_api.py -q
python -m pytest tests/ -q
cd frontend
npm test -- --run
npm run build
```

## Current Scope

Included:

- Demo bookshelf HSF project served by Python
- Real HSF directory loading through `/api/project/load`
- Native HSF directory picker through `/api/dialog/open-directory`
- `/api/snapshot`, `/api/preview`, `/api/apply`
- Parameter apply persists values back to the loaded HSF source directory
- Mock GSM compilation through `/api/compile`
- Compiler mode/path settings through `/api/settings/compiler`
- LP_XMLConverter compile path support when mode is `lp`
- Explanation assistant through `/api/assistant`
- Pipeline-backed AI generation/modification through `/api/assistant/generate`
- Left rail for dimensions
- Center live 3D preview
- Right rail for quantity/properties
- Right AI placeholder panel
- Bottom drawer for logs/warnings

Not included yet:

- Native file picker for LP_XMLConverter
- `.gsm` import/decompile
- Full chat history persistence
- Tauri packaging
- Archicad/Tapir integration

## Next Decisions

- Whether React becomes the primary UI line.
- Whether the backend should remain stdlib local RPC or move to FastAPI.
- Whether to package with Tauri and spawn the Python backend as a sidecar.
