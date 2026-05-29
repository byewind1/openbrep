# React Workbench Main UI Audit

Date: 2026-05-30

## Conclusion

`react-workbench-poc` is now strong enough to continue as the primary UI direction
for OpenBrep, but it is not yet a full Streamlit replacement.

The branch has crossed the important product threshold: it is no longer just a
visual shell. It can open HSF sources, edit scripts, apply parameters, run AI
generation/modification, preview 3D, compile, inspect diagnostics, manage
revisions, configure compiler/LLM settings, and start with one command.

The remaining gap is not basic workbench viability. The remaining gap is
Streamlit parity for long-running project workflows: history, memory, 2D preview,
GSM import/decompile, parameter authoring, and Archicad/Tapir integration.

## Architecture Map

Current React workbench boundaries:

```text
frontend/src/App.tsx
  Thin entry point only.

frontend/src/workbench/WorkbenchApp.tsx
  React composition root.

frontend/src/components/*
  Reusable visual components:
  TopMenu, ScriptTree, ScriptEditor, ParameterRail, PreviewViewport,
  BottomDrawer, AssistantPanel.

frontend/src/workbench/*
  Domain panels:
  project/ProjectOpenControls
  settings/SettingsDrawer
  diagnostics/RevisionPanel
  preview/FloatingPreviewWindow

frontend/src/state/*
  Zustand store plus action slices:
  project, script, parameter, compile, assistant, revision, settings.

openbrep/workbench_api.py
  Local API adapter reusing OpenBrep domain modules.
```

This is acceptable for the next stage. `WorkbenchApp.tsx` is still a composition
root, not a business-logic dump. The store split is holding.

## Capability Parity

| Workflow | Streamlit | React Workbench | Status |
| --- | --- | --- | --- |
| One-command launch | Streamlit command/package launcher | `./obr7` starts API + Vite | Done |
| Open HSF directory | Yes | Path input + native directory picker | Done |
| Import single `.gdl` | Yes | Native import creates HSF project | Done |
| Import/decompile `.gsm` | Yes when converter is available | Not implemented | Gap |
| Script editing | Ace/text area, fullscreen dialog | Monaco, script tree, save, diagnostics | Better in React |
| Save script | Yes | Save + refresh + mock diagnostics | Done |
| 3D preview | Streamlit preview panel | Three.js viewport + right rail | Done |
| 2D preview | Available in Streamlit | Disabled tab only | Gap |
| Floating preview | Streamlit-ish fullscreen patterns | Implemented but frozen by flag | Deferred |
| Parameter editing | View/edit/add/validate/paramlist preview | Edit/apply/reset existing params | Partial |
| Manual parameter add | Yes | Not implemented | Gap |
| Paramlist XML preview | Yes | XML file can be opened, no dedicated preview | Partial |
| Mock compile | Yes | Mock compile + diagnostics | Done |
| Real compile | Yes | LP mode/path settings + compile | Done |
| Compile output selection | Streamlit has chooser | React uses backend defaults | Gap |
| Revision save/list/restore | Yes | Bottom drawer panel | Done |
| AI create | Yes | Assistant creates HSF project | Done |
| AI modify | Yes | Assistant modifies current project, refreshes scripts/diagnostics | Done |
| Explain/chat | Yes | Basic assistant explain | Partial |
| Chat history browser | Yes | Not implemented | Gap |
| Adopt code from chat record | Yes | Not implemented | Gap |
| Image/vision input | Yes | Not implemented | Gap |
| LLM settings | Sidebar settings | Settings drawer | Done |
| Custom provider credentials | Yes | Settings drawer supports model/key/base | Partial but usable |
| Compiler settings | Sidebar settings | Settings drawer | Done |
| Work directory | Sidebar setting | Not explicit; project paths are direct | Gap |
| Memory/privacy panel | Error lessons, memory status, clear memory | Not implemented | Gap |
| Wrong-answer notebook summarize | Streamlit workspace tool | Not implemented | Gap |
| Pro license/knowledge package | Streamlit sidebar | Not implemented | Defer |
| Tapir/Archicad bridge | Streamlit controls | Not implemented | Defer |

## Main UI Readiness

React Workbench is ready to become the primary path for these users:

- GDL developers editing existing HSF projects.
- Users who create or modify HSF projects through AI.
- Users who need fast code/preview/diagnostics iteration.
- Users who can accept `./obr7` as the launch command.

React Workbench is not ready to fully replace Streamlit for these workflows:

- Image-to-GDL generation.
- `.gsm` import/decompile workflows.
- Archicad/Tapir live integration.
- Workspace memory and wrong-answer notebook management.
- Pro licensing and Pro knowledge package import.
- Chat-record reuse and code adoption from prior sessions.

## Recommended Migration Path

### P5A: Close Daily HSF/GDL Workflow Gaps

Goal: make React the default UI for daily code work.

Implement:

- 2D preview tab using existing backend/domain preview path.
- Compile output directory choice or at least visible/openable output location.
- Better diagnostics grouping by script.
- Manual parameter add/edit validation and paramlist preview.

Do not implement Tapir or packaging here.

### P5B: Restore AI Session Memory

Goal: recover the useful Streamlit chat-history workflows without recreating the
old crowded UI.

Implement:

- Persist assistant messages per workspace/project.
- Chat history browser in a drawer/modal.
- Re-apply/adopt code from prior assistant outputs.
- Memory/privacy panel: status, clear memory, summarize wrong-answer notebook.

This is more important than Pro/Tapir for day-to-day AI-assisted work.

### P5C: Import/Export Parity

Goal: make React cover source and artifact lifecycle.

Implement:

- `.gsm` import/decompile through existing converter path.
- Output directory selection.
- Open/reveal compiled `.gsm` output.
- Explicit HSF save-as/export workflow if needed.

### P5D: Defer Heavy Integrations

Keep these out until the core React workbench is stable:

- Tapir/Archicad live bridge.
- Pro license and Pro knowledge package UX.
- Tauri packaging.
- Floating/dockable preview state management.

## Architecture Guardrails

Keep these rules while migrating:

- Do not grow `WorkbenchApp.tsx` into a controller. It should compose panels only.
- Keep local API behavior in `openbrep/workbench_api.py` as a thin adapter over
  existing domain modules.
- Keep store behavior in action slices by domain.
- Do not reimplement GDL parsing, preview, compile, or HSF mutation in React.
- Add a focused test for each migrated workflow before broadening UI scope.
- Prefer drawers/tabs for secondary controls; do not recreate Streamlit's long
  sidebar of stacked settings.

## Next Recommendation

Do P5A first, starting with 2D preview and parameter authoring.

Reason: the React workbench already wins at code editing. The next highest-value
gap is making the right rail and left inspector cover the same object-development
surface as Streamlit without bringing back the crowded form UI.

