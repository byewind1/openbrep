# React Workbench Settings Organization Design

Date: 2026-06-03

## Problem

The React workbench Settings drawer has become visually and structurally crowded.
It exposes routine configuration, project actions, Git controls, memory controls,
compiler paths, and AI provider settings in one long scroll. This makes the
workbench feel less like a professional GDL tool and more like a debug panel.

The current `SettingsDrawer.tsx` is also too broad. It owns the drawer shell,
resize behavior, draft state, AI model selection, compiler fields, workspace
actions, Git wiring, memory wiring, and inline panel JSX. Future additions would
make the file harder to reason about.

## Goals

- Keep Settings as a right-side drawer.
- Make common settings immediately usable.
- Hide low-frequency, diagnostic, or potentially disruptive controls behind
  collapsible sections.
- Preserve explicit `Save Settings` and `Reload config` semantics.
- Reduce `SettingsDrawer.tsx` into a composition shell instead of a catch-all
  settings implementation.
- Keep labels and controls consistent with the current English React UI.

## Non-Goals

- Do not redesign the whole workbench shell.
- Do not move Settings to a separate route or full-screen modal.
- Do not change how config files are persisted.
- Do not add new LLM provider persistence semantics.
- Do not expose secrets beyond the current local UI behavior.

## Recommended Approach

Use a single-page settings drawer with collapsible sections.

Default expanded sections:

- `General`
- `AI`

Default collapsed sections:

- `Compiler`
- `Workspace`
- `Git`
- `Memory`
- `Advanced`

This keeps the current drawer model but makes it behave like a conventional
settings panel: routine settings are visible, lower-frequency settings are
available without being visually dominant.

## Section Structure

### General

Purpose: show runtime configuration context and global settings actions.

Fields and controls:

- Config file path, read-only.
- Save state.
- `Reload config`.
- `Save Settings`.

General is expanded by default.

### AI

Purpose: configure the workbench assistant and LLM provider.

Fields and controls:

- AI source segmented control: `Official`, `Custom`, `Exact ID`.
- Model selector.
- Exact model ID input.
- API Key.
- API Base URL.
- Max retries.
- Assistant preference.
- `Test connection`.

AI is expanded by default because it is one of the primary workbench setup tasks.
The current distinction between official provider keys and custom provider
credentials remains unchanged.

### Compiler

Purpose: configure compile verification.

Fields and controls:

- Compiler mode: `Mock` or `LP`.
- `LP_XMLConverter` path.
- Output directory.

Compiler is collapsed by default. It should show a title summary such as
`Compiler · Mock` or `Compiler · LP`.

### Workspace

Purpose: expose current project/session actions.

Fields and controls:

- Recent HSF projects.
- `Export HSF`.
- `Reset Current Project`.

Workspace is collapsed by default because recent project lists and reset actions
make the drawer noisy and are not part of every settings visit.

### Git

Purpose: manage optional project checkpoint support.

Existing `GitSettingsPanel.tsx` remains the panel body. The parent drawer wraps
it in a collapsible section and provides a short summary such as `Git · Enabled`
or `Git · Disabled`.

### Memory

Purpose: inspect and manage learned project memory.

Existing `MemoryLessonsPanel.tsx` remains the panel body. The parent drawer wraps
it in a collapsible section and provides a summary such as `Memory · 3 lessons`.

### Advanced

Purpose: reserve a place for future local runtime/debug controls.

If Advanced has no active controls, it may be hidden or shown as a collapsed
reserved section. It must not become a dumping ground for unrelated product
features.

## Interaction Rules

- Section headers are clickable and toggle expanded/collapsed state.
- Default expansion state is fixed on first drawer open: `General` and `AI`
  expanded; other sections collapsed.
- Each collapsed section header shows a concise status summary.
- Modified sections show a small `Modified` status in the header.
- `Save Settings` remains global and saves all dirty settings in the existing
  order.
- `Reload config` remains global and refreshes the draft from disk.
- Low-frequency or risky actions remain inside collapsed sections by default.

## Component Boundaries

Create or keep these components:

- `SettingsDrawer.tsx`
  - Drawer shell.
  - Resize behavior.
  - Global save/reload orchestration.
  - Section expansion state.
  - Composition of setting panels.
- `SettingsSection.tsx`
  - Shared collapsible section header/body.
  - Summary text.
  - Modified state display.
- `GeneralSettingsPanel.tsx`
  - Config path and global action presentation.
- `AiSettingsPanel.tsx`
  - AI source/model/key/base/retry/preference/test controls.
- `CompilerSettingsPanel.tsx`
  - Compiler mode and path fields.
- `WorkspaceSettingsPanel.tsx`
  - Recent projects and current session actions.
- `GitSettingsPanel.tsx`
  - Keep existing panel, adapt only as needed for collapsible wrapping.
- `MemoryLessonsPanel.tsx`
  - Keep existing panel, adapt only as needed for collapsible wrapping.

AI model-selection logic may initially live inside `AiSettingsPanel.tsx`. If it
continues to grow, extract it later into a focused hook such as
`useLlmModelSelection`.

## Data Flow

- The drawer receives current settings and callbacks from `WorkbenchApp.tsx`.
- Panel components receive draft values and update callbacks.
- Panel components do not call backend APIs directly.
- `SettingsDrawer.tsx` owns draft state and invokes the existing
  `onCompilerSettingsChange`, `onLlmSettingsChange`, and
  `onReloadRuntimeSettings` callbacks.
- `GitSettingsPanel.tsx` and `MemoryLessonsPanel.tsx` continue to use the
  callbacks already passed from the workbench app.

## Error Handling

- Save failures keep the drawer in a dirty state.
- Save errors remain visible near the global actions.
- LLM connection test results stay local to the AI section.
- Reload errors use the existing app-level callback behavior.

## Testing

Update or add frontend tests for:

- Default expanded sections: `General` and `AI`.
- Default collapsed sections: `Compiler`, `Workspace`, `Git`, `Memory`, and
  `Advanced`.
- Section summary text.
- Dirty state marking after editing compiler or AI draft fields.
- Existing explicit save order: compiler save, LLM save, reload.
- Existing LLM official/custom/exact switching behavior.
- Existing settings drawer resize bounds.

Backend tests are not required unless implementation changes persistence APIs.

## Success Criteria

- Opening Settings no longer exposes Workspace, Git, Memory, and Advanced
  content by default.
- AI setup remains immediately accessible.
- Compiler setup is one click away.
- `SettingsDrawer.tsx` is materially smaller and no longer contains every panel's
  JSX.
- Existing settings persistence behavior is unchanged.
- Frontend tests and build pass.
