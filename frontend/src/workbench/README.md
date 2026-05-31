# React Workbench Structure

`App.tsx` must stay a thin entry point. The OpenBrep workbench shell lives here.

## Seams

- `WorkbenchApp.tsx`: composition root for the current React workbench.
- `../api/*`: local Python API adapter contracts.
- `../state/*`: workbench state and domain-facing actions.
- `../components/*`: reusable view modules. Do not put workflow orchestration here.

## Growth Rules

- New migrated Streamlit workflows should start as a small API contract plus one focused view module.
- Do not add substantial behavior to `App.tsx`.
- Do not grow `WorkbenchApp.tsx` with feature-specific logic. Extract a feature module when a workflow needs local state, validation, or multiple controls.
- Keep the main stage context-driven: code, preview, parameters, diagnostics, or AI may become primary depending on the task.
- Do not put Python/GDL interpretation logic in React modules.

## Planned Feature Folders

- `project/`: open/import/close/export project workflows.
- `editor/`: script tabs, Monaco integration, save state.
- `settings/`: runtime, compiler, and LLM settings.
- `assistant/`: AI explain/generate/modify/repair workflows.
- `diagnostics/`: compile issues, trace, revision history.
- `preview/`: 3D/2D viewport shells and preview controls.
