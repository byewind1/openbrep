# Workbench Topbar Menu Design

## Goal

Make the React workbench topbar a single-row professional tool header. The
header must not wrap into two stacked rows when the normal 1180px minimum
workspace width is available.

## Decision

Use the approved Scheme A: grouped menu-style controls.

- Keep the brand and current project identity visible at the left.
- Move file lifecycle commands into a `Project` menu: New, Open path, Browse
  HSF, Recent projects, Import GDL, Import GSM, Save As.
- Move secondary build commands into a `Build` menu: Mock Compile and Compile.
- Keep high-frequency direct controls in the topbar: Save, Compile, Apply,
  Settings, and compact project/source/dirty/parameter status pills.
- Keep implementation in focused React components. Do not add domain logic to
  `WorkbenchApp.tsx`.

## Interaction

`Project` and `Build` are native menu buttons backed by lightweight popover
panels. Menus close after command selection. The HSF path input remains inside
the Project menu so it no longer consumes permanent topbar width.

## Testing

Add component tests that verify:

- Project file commands are available from the Project menu.
- Recent project selection still calls `onLoadProjectPath`.
- Direct Save, Compile, Settings, and Apply actions remain available.
- Topbar markup no longer contains the old always-visible project open control
  form.
