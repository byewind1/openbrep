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
Streamlit parity for long-running project workflows: history, memory,
image input, and Archicad/Tapir integration.

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
| Import/decompile `.gsm` | Yes when converter is available | LP mode import creates HSF project through existing converter path | Done |
| Script editing | Ace/text area, fullscreen dialog | Monaco, script tree, save, diagnostics | Better in React |
| Save script | Yes | Save + refresh + mock diagnostics | Done |
| 3D preview | Streamlit preview panel | Three.js viewport + right rail | Done |
| 2D preview | Available in Streamlit | Right rail 2D tab backed by existing preview path | Done |
| Floating preview | Streamlit-ish fullscreen patterns | Implemented but frozen by flag | Deferred |
| Parameter editing | View/edit/add/validate/paramlist preview | Edit/apply/reset values, metadata edit/delete, validate | Done |
| Manual parameter add | Yes | Compact add parameter control | Done |
| Paramlist XML preview | Yes | XML file can be opened, no dedicated preview | Partial |
| Mock compile | Yes | Mock compile + diagnostics | Done |
| Real compile | Yes | LP mode/path settings + compile | Done |
| Compile output selection | Streamlit has chooser | Settings drawer output directory + compile uses it + reveal output | Done |
| Revision save/list/restore | Yes | Bottom drawer panel | Done |
| AI create | Yes | Assistant creates HSF project | Done |
| AI modify | Yes | Assistant modifies current project, refreshes scripts/diagnostics | Done |
| Explain/chat | Yes | Basic assistant explain | Partial |
| Chat history browser | Yes | Assistant thread persists per HSF project; compact drawer browser is available from AI panel | Partial |
| Adopt code from chat record | Yes | Assistant replies with code can be adopted from thread or history drawer into dirty script buffers | Done |
| Image/vision input | Yes | Not implemented | Gap |
| LLM settings | Sidebar settings | Settings drawer | Done |
| Custom provider credentials | Yes | Settings drawer supports model/key/base | Partial but usable |
| Compiler settings | Sidebar settings | Settings drawer | Done |
| Work directory | Sidebar setting | Not explicit; project paths are direct | Gap |
| Memory/privacy panel | Error lessons, memory status, clear memory | Settings drawer shows project memory status, lesson review, per-lesson edit/ignore/delete, skill preview, summarize, and clear memory | Done |
| Wrong-answer notebook summarize | Streamlit workspace tool | Settings drawer can summarize lessons into learned skill via local API | Partial |
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
- Archicad/Tapir live integration.
- Advanced wrong-answer notebook curation beyond summarize/review.
- Pro licensing and Pro knowledge package import.
- Multi-message history browser actions beyond single-message code adoption.

## Recommended Migration Path

### P5A: Close Daily HSF/GDL Workflow Gaps

Goal: make React the default UI for daily code work.

Implemented:

- 2D preview tab using existing backend/domain preview path.
- Compile output directory choice or at least visible/openable output location.
- Better diagnostics grouping by script.
- Manual parameter add/edit validation and paramlist preview.

Remaining small follow-up:

- Dedicated paramlist preview/summary panel, beyond opening `paramlist.xml`.

Do not implement Tapir or packaging here.

### P5B: Restore AI Session Memory

Goal: recover the useful Streamlit chat-history workflows without recreating the
old crowded UI.

Implemented:

- Persist assistant messages per HSF project under `.openbrep/memory/chats/`.
- Clear assistant history from the AI panel.
- Adopt code blocks from assistant history into editable script buffers.
- Browse assistant history in a compact drawer from the AI panel.
- Show project memory status in Settings.
- Review project error lessons in Settings.
- Edit individual project error lessons from Settings.
- Ignore individual project error lessons from Settings without deleting audit records.
- Delete individual project error lessons from Settings.
- Summarize project memory into the learned skill from Settings.
- Preview the generated learned skill in Settings.
- Clear project memory from Settings.

Remaining:

- Bulk or multi-message history actions beyond single-message adoption.
- Advanced wrong-answer notebook curation: merge individual lessons.

This is more important than Pro/Tapir for day-to-day AI-assisted work.

### P5C: Import/Export Parity

Goal: make React cover source and artifact lifecycle.

Implemented:

- `.gsm` import/decompile through existing converter path.
- Output directory selection. Done as a P5A/P5C bridge.
- Open/reveal compiled `.gsm` output.
- Explicit HSF save-as/export through the Settings workspace panel.

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

Continue P5B session memory with lesson curation, then move to image input.

Reason: daily code work is now covered well enough. The next highest-value gap
is now polishing session continuity: persisted assistant history, drawer browsing,
code adoption, lesson review, per-lesson delete, summarize, and skill preview
exist, but users still need merge controls before this fully replaces
the Streamlit memory tools.
