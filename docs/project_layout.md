# OpenBrep Project Layout

Date: 2026-04-26  
Status: Working convention before v0.7 revision implementation

## 1. Why This Exists

OpenBrep currently has a working HSF model, but the workspace semantics are not
explicit enough:

- Creating an object creates an HSF directory.
- Importing a `.gsm` creates an HSF directory.
- Compiling creates a versioned `.gsm`, but does not create a new HSF directory.
- UI `work_dir` also contains output files, local knowledge, Pro knowledge,
  license data, feedback, and future project metadata.

Before implementing revision management, the project/source/output lifecycle
needs a clear rule set.

## 2. Core Rule

One GDL object equals one stable HSF project directory.

```text
workspace/
  Bookshelf/
    libpartdata.xml
    paramlist.xml
    scripts/
      1d.gdl
      2d.gdl
      3d.gdl
      vl.gdl
      ui.gdl
```

The HSF directory is the source of truth for the current editable object.

Compilation must not create a new source directory. Compilation only creates a
`.gsm` deliverable derived from the current HSF source.

## 3. Current Compatible Workspace Layout

The current UI and CLI already use a flat workspace layout. Keep supporting it:

```text
workspace/
  Bookshelf/                 # HSF project directory
  WindowFrame/               # another HSF project directory
  output/                    # compiled GSM deliverables
    Bookshelf_v1.gsm
    Bookshelf_v2.gsm
  knowledge/                 # user flat knowledge
  skills/                    # user skills
  pro_knowledge/             # imported Pro knowledge package
  .openbrep/                 # workspace-level metadata
    license_v1.json
    tmp_pro_knowledge/
  feedback.jsonl
```

This is the current compatibility target. v0.7 should not require moving
existing projects into a new `projects/` folder.

## 4. Future Optional Workspace Layout

A more explicit layout may be introduced later:

```text
workspace/
  projects/
    Bookshelf/
    WindowFrame/
  output/
  knowledge/
  skills/
  pro_knowledge/
  .openbrep/
```

This layout is cleaner, but it is not required for v0.7. If introduced, it must
be an opt-in migration, not a breaking change.

## 5. HSF Project Directory

An HSF project directory is recognized by either:

- `libpartdata.xml`, or
- `scripts/`

Typical layout:

```text
Bookshelf/
  libpartdata.xml
  paramlist.xml
  ancestry.xml
  calledmacros.xml
  libpartdocs.xml
  scripts/
    1d.gdl
    2d.gdl
    3d.gdl
    vl.gdl
    ui.gdl
    pr.gdl
```

`HSFProject.root` should point at this directory. `HSFProject.work_dir` should be
its parent directory.

## 6. Object Lifecycle

### Create

Creating a new object creates one HSF project directory:

```text
workspace/Bookshelf/
```

If the name exists, creation should choose a non-overwriting name or ask the
user to load/replace the existing project.

### Import `.gsm`

Importing a `.gsm` decompiles it into a temporary HSF directory, then copies it
into the workspace as a stable HSF project directory:

```text
workspace/Chair/
```

If the directory exists, current behavior appends `_imported_2`,
`_imported_3`, etc. This behavior is acceptable for compatibility, but the UI
should eventually ask whether the user wants to:

- load existing project,
- import as copy,
- replace current project.

### Import `.gdl` or `.txt`

Importing raw GDL creates an HSF project wrapper around the parsed script:

```text
workspace/ImportedName/
  paramlist.xml
  scripts/
    3d.gdl
```

It becomes a normal HSF project after import.

### Modify

Modifying an object updates the current HSF project directory:

```text
workspace/Bookshelf/
```

It should not create `Bookshelf_2/` or `Bookshelf_modified/`.

### Compile

Compiling reads the current HSF project directory and writes a `.gsm` deliverable:

```text
workspace/Bookshelf/        # source remains stable
workspace/output/
  Bookshelf_v4.gsm          # compiled deliverable
```

Compilation does not create a new HSF source directory.

## 7. Source Format

The source format is the HSF project directory, not a single `.gdl` file and not
the `.gsm` output.

The minimum versioned source set is:

```text
paramlist.xml
scripts/*.gdl
```

Reason:

- GDL scripts alone do not contain parameter definitions.
- `paramlist.xml` alone does not contain object behavior.
- AI changes often touch both scripts and parameters.
- `.gsm` is a compiled deliverable, not a source format.

## 8. Project-Level `.openbrep`

v0.7 revision management should store project-level metadata inside the HSF
project directory:

```text
workspace/
  Bookshelf/
    paramlist.xml
    scripts/
    .openbrep/
      project.toml
      revisions/
        r0001/
        r0002/
      latest
```

This is different from the existing workspace-level `.openbrep/` used for
license and temporary Pro package extraction.

When an existing HSF project is loaded or imported into the active workspace,
its project-level `.openbrep/` directory should be copied with the HSF source.
This keeps revision history attached to the GDL project instead of leaking or
resetting through the OpenBrep UI session.

Use this distinction:

```text
workspace/.openbrep/              # workspace metadata
workspace/Bookshelf/.openbrep/    # project metadata
```

## 9. Revision Relationship to GSM Output

Compiled GSM files should be linked to source revisions by metadata:

```text
r0004 source snapshot → output/Bookshelf_v4.gsm
```

The revision manifest should record:

```json
{
  "revision_id": "r0004",
  "compile": {
    "status": "passed",
    "gsm_path": "../output/Bookshelf_v4.gsm"
  }
}
```

Do not derive revision history from existing `.gsm` files. GSM files are
outputs; revision history belongs to source snapshots.

## 10. Editor Behavior

The script editor always edits the current HSF project state:

```text
workspace/Bookshelf/scripts/3d.gdl
```

The editor does not need to open old revision files directly.

History and rollback should be separate actions:

- `history`: list revision metadata.
- `compare`: show text diff between snapshots.
- `rollback`: copy a snapshot back into the current HSF project and create a
  new rollback revision.

## 11. Rollback Rule

Rollback must not delete or mutate history.

If the user rolls back from `r0008` to `r0005`, OpenBrep should create:

```text
r0009 = rollback to r0005
```

Current source becomes the content of `r0005`, but the historical sequence is
preserved.

## 12. Naming Rules

Project directory names should be stable and filesystem-safe.

Recommended behavior:

- Use object name as directory name.
- Strip invalid path characters.
- If importing and a directory exists, create a non-overwriting imported copy.
- If creating a new project and the name exists, ask the user before reusing it.

Compiled `.gsm` names may remain versioned:

```text
output/Bookshelf_v1.gsm
output/Bookshelf_v2.gsm
```

The revision id and the GSM version number do not need to be identical forever,
but the manifest must link them.

## 13. Implementation Implications

Before coding `RevisionStore`, enforce these assumptions:

- `RevisionStore(project_root)` receives the HSF project root, not the workspace root.
- Revision snapshots are stored under `project_root/.openbrep/revisions`.
- Compile output remains under `workspace/output` for compatibility.
- `.gsm` imports create or load an HSF project directory first; revisions attach
  to that directory after import.
- `HSFProject.save_to_disk()` remains the single writer for current source.

## 14. Open Questions

- Should `.openbrep/revisions` store only scripts and paramlist in v0.7, or full
  HSF metadata files too?
- Should imported `.gsm` immediately create `r0001`, or only after first edit?
- Should manual editor changes create a revision on every save, or only when the
  user clicks compile / confirm?
- Should output `.gsm` files live in workspace-level `output/` or project-level
  `Bookshelf/output/` in a future layout?

Recommended v0.7 answers:

- Store scripts and paramlist first.
- Import creates `r0001`.
- Manual editor changes create a revision when the user clicks compile or
  explicitly saves a snapshot.
- Keep workspace-level `output/` for compatibility.
