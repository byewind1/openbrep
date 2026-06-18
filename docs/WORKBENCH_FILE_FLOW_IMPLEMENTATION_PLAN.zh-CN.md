# Workbench File Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default demo-project startup/reset flow with an IDE-style empty workbench, New, Save, and Save As HSF lifecycle.

**Architecture:** Backend project lifecycle changes stay in `openbrep/workbench/project_session_service.py`, with thin wrappers in `openbrep/workbench_api.py`. Frontend lifecycle commands stay in Zustand project actions and focused toolbar/open-control components. `WorkbenchApp.tsx` remains composition only.

**Tech Stack:** Python local API, `HSFProject`, React + TypeScript, Zustand store, Vitest, pytest.

---

## File Structure

- Modify `openbrep/workbench/project_session_service.py`: add empty snapshot/new/save project operations and stop using demo as reset target.
- Modify `openbrep/workbench/project_service.py`: expose new/save through project service.
- Modify `openbrep/workbench_api.py`: add thin route wrappers for `/api/project/new` and `/api/project/save`.
- Modify `frontend/src/api/types.ts`: allow empty project snapshots and add project lifecycle result types.
- Modify `frontend/src/api/client.ts`: add `newProject()` and `saveProject()` clients; update fallback snapshot to empty workbench.
- Modify `frontend/src/state/workbenchStoreTypes.ts`: add `newProject`, `saveProject`, `saveProjectAs` actions.
- Modify `frontend/src/state/workbenchStore.ts`: wire new API functions into project actions.
- Modify `frontend/src/state/actions/projectActions.ts`: implement New, Save project, Save As project logic.
- Modify `frontend/src/workbench/project/ProjectOpenControls.tsx`: add `New` and `Save As` entry points in file controls.
- Modify `frontend/src/components/TopMenu.tsx`: pass through Save As and disable compile for unsaved projects.
- Modify `frontend/src/workbench/WorkbenchApp.tsx`: compose new actions and keep confirmation logic local.
- Update tests in `tests/test_workbench_api.py`, `frontend/src/state/workbenchStore.test.ts`, and component tests.

## Task 1: Backend Empty/New/Save Lifecycle

**Files:**
- Modify: `openbrep/workbench/project_session_service.py`
- Modify: `openbrep/workbench/project_service.py`
- Modify: `openbrep/workbench_api.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Write failing backend tests**

Add tests asserting:

```python
def test_workbench_session_starts_empty():
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    snapshot = session.snapshot()
    assert snapshot["project"] is None

def test_workbench_session_new_project_is_untitled(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    response = session.route("POST", "/api/project/new", {})
    assert response["ok"] is True
    assert response["project"]["source"] == "untitled"
    assert response["project"]["path"] == ""

def test_workbench_session_close_returns_empty(tmp_path):
    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.route("POST", "/api/project/new", {})
    response = session.route("POST", "/api/project/close", {})
    assert response["ok"] is True
    assert response["project"] is None
```

- [ ] **Step 2: Run backend tests and verify failure**

Run:

```bash
python -m pytest tests/test_workbench_api.py::test_workbench_session_starts_empty tests/test_workbench_api.py::test_workbench_session_new_project_is_untitled tests/test_workbench_api.py::test_workbench_session_close_returns_empty -q
```

Expected: fail because project is currently `Demo Bookshelf` and `/api/project/new` does not exist.

- [ ] **Step 3: Implement backend lifecycle**

Implement:

```python
def empty_project_snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        "project": None,
        "parameters": [],
        "preview": {"meshes": [], "wires": [], "warnings": []},
        "warnings": [],
    }
```

Add `new_project()` to create `HSFProject.create_new("Untitled GDL Object")`, set `source="untitled"`, `source_path=None`, and return snapshot.

Update `close_project()` to clear project state and return empty snapshot.

- [ ] **Step 4: Add save project route**

Add `save_project()` that:

```python
if self.session.project is None:
    return {"ok": False, "error": "No project to save."}
if self.session.source_path is None:
    return {"ok": False, "needs_save_as": True, "error": "Project has no HSF path. Use Save As HSF."}
self.session.project.save_to_disk()
return {"ok": True, "saved_to": str(self.session.source_path), **self.session.snapshot()}
```

- [ ] **Step 5: Run backend tests**

Run:

```bash
python -m pytest tests/test_workbench_api.py -q
```

Expected: pass after updating existing demo assumptions.

## Task 2: Frontend API And Store Lifecycle

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/state/workbenchStoreTypes.ts`
- Modify: `frontend/src/state/workbenchStore.ts`
- Modify: `frontend/src/state/actions/projectActions.ts`
- Test: `frontend/src/state/workbenchStore.test.ts`

- [ ] **Step 1: Write failing store tests**

Add tests asserting:

```ts
test('newProject loads an untitled project', async () => {
  await store.getState().newProject()
  expect(store.getState().project?.source).toBe('untitled')
})

test('saveProject reports save-as requirement for unsaved projects', async () => {
  await store.getState().saveProject()
  expect(store.getState().lastError).toContain('Save As')
})
```

- [ ] **Step 2: Add API clients**

Add:

```ts
export async function newProject(): Promise<WorkbenchSnapshot> {
  return requestJson('/api/project/new', { method: 'POST' }, fallbackSnapshot)
}

export async function saveProject(): Promise<HsfExportResult> {
  return requestJson('/api/project/save', { method: 'POST' }, { ok: false, error: 'OpenBrep local API is not available.' })
}
```

- [ ] **Step 3: Add store actions**

Add `newProject`, `saveProject`, and `saveProjectAs` to `projectActions.ts`. `saveProjectAs` should call existing `exportHsfProject()`.

- [ ] **Step 4: Run frontend store tests**

Run:

```bash
npm test -- --run frontend/src/state/workbenchStore.test.ts
```

Expected: pass.

## Task 3: Toolbar And Open Controls UX

**Files:**
- Modify: `frontend/src/workbench/project/ProjectOpenControls.tsx`
- Modify: `frontend/src/components/TopMenu.tsx`
- Modify: `frontend/src/workbench/WorkbenchApp.tsx`
- Test: `frontend/src/workbench/settings/SettingsDrawer.test.tsx` if affected
- Test: component tests for project/open controls

- [ ] **Step 1: Add controls**

Add `New` and `Save As` buttons to the project controls. Use compact English labels:

```tsx
<button type="button" disabled={loading} onClick={onNewProject}>New</button>
<button type="button" disabled={loading || !project} onClick={onSaveProjectAs}>Save As</button>
```

- [ ] **Step 2: Disable compile for unsaved project**

Use:

```ts
const canCompile = Boolean(project?.path)
```

Disable Compile and Mock when `canCompile` is false.

- [ ] **Step 3: Show status**

Top status should show:

```tsx
project ? (project.path ? 'Saved' : 'Unsaved') : 'Empty'
```

- [ ] **Step 4: Run frontend tests**

Run:

```bash
npm test -- --run
```

Expected: pass.

## Task 4: Browser Smoke And Full Verification

**Files:**
- Test only

- [ ] **Step 1: Run backend full tests**

```bash
python -m pytest tests/ -q
```

Expected: pass.

- [ ] **Step 2: Run frontend build/tests**

```bash
npm test -- --run
npm run build
```

Expected: pass.

- [ ] **Step 3: Run browser smoke**

```bash
python scripts/workbench_browser_smoke.py --pretty --timeout 60
```

Expected: pass, with no blank screen and no uncaught Settings/Open/New errors.

## Self-Review

- Spec coverage: startup empty, New, Save, Save As, disabled compile, dirty/status, backend boundaries, and frontend boundaries are covered.
- 占位扫描：没有未解决的占位标记。
- Type consistency: uses existing `WorkbenchSnapshot` and `HsfExportResult`; new store actions are named `newProject`, `saveProject`, and `saveProjectAs`.
