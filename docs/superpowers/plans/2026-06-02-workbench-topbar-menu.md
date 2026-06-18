# Workbench Topbar Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the React workbench topbar into a one-row grouped menu header.

**Architecture:** Keep file-opening behavior in `ProjectOpenControls`, but render it as a compact Project menu instead of a full-width form. Keep `TopMenu` responsible for layout composition and direct primary actions only. CSS owns one-row density and menu popover positioning.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library.

---

### Task 1: Project Menu Controls

**Files:**
- Modify: `frontend/src/workbench/project/ProjectOpenControls.tsx`
- Modify: `frontend/src/workbench/project/ProjectOpenControls.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing tests**

Add tests that open the `Project` menu, click `New`, click `Save As`, and select a recent project. Assert the callbacks fire.

- [ ] **Step 2: Run tests to verify failure**

Run: `npm test -- --run src/workbench/project/ProjectOpenControls.test.tsx`

Expected: FAIL because there is no `Project` menu button yet.

- [ ] **Step 3: Implement compact Project menu**

Render a `Project` button and a positioned panel containing New, path input,
Open, Browse, Recent, Import GDL, Import GSM, and Save As.

- [ ] **Step 4: Run tests to verify pass**

Run: `npm test -- --run src/workbench/project/ProjectOpenControls.test.tsx`

Expected: PASS.

### Task 2: Topbar Direct Actions and Build Menu

**Files:**
- Create: `frontend/src/components/TopMenu.test.tsx`
- Modify: `frontend/src/components/TopMenu.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing tests**

Add tests that render `TopMenu`, verify `Project` is present through the
project controls slot, verify direct buttons `Save`, `Compile`, `Settings`,
and `Apply`, and verify `Mock Compile` is available only through the `Build`
menu.

- [ ] **Step 2: Run tests to verify failure**

Run: `npm test -- --run src/components/TopMenu.test.tsx`

Expected: FAIL because `Build` menu does not exist yet.

- [ ] **Step 3: Implement Build menu and one-row topbar classes**

Move Mock Compile into a Build menu, keep direct Compile visible, and tune
topbar CSS so the normal workspace width does not wrap controls into rows.

- [ ] **Step 4: Run tests to verify pass**

Run: `npm test -- --run src/components/TopMenu.test.tsx src/workbench/project/ProjectOpenControls.test.tsx`

Expected: PASS.

### Task 3: Verification

**Files:**
- Modify as needed based on verification output.

- [ ] **Step 1: Run targeted frontend tests**

Run: `npm test -- --run src/components/TopMenu.test.tsx src/workbench/project/ProjectOpenControls.test.tsx`

- [ ] **Step 2: Run all frontend tests**

Run: `npm test -- --run`

- [ ] **Step 3: Build frontend**

Run: `npm run build`

- [ ] **Step 4: Run browser smoke**

Run: `python scripts/workbench_browser_smoke.py --pretty --timeout 60`
