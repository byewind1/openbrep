# Workbench Settings Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the React workbench Settings drawer so routine settings stay visible while low-frequency controls are collapsed and `SettingsDrawer.tsx` becomes a composition shell.

**Architecture:** Keep the right-side drawer. Add a reusable collapsible `SettingsSection` component and split settings content into focused panel components. Preserve existing draft/save/reload callbacks and LLM persistence semantics.

**Tech Stack:** React, TypeScript, Vitest, Testing Library, Vite.

---

## File Structure

- Create: `frontend/src/workbench/settings/SettingsSection.tsx`
  - Shared collapsible section wrapper.
  - Header button, summary, modified badge, and body visibility.
- Create: `frontend/src/workbench/settings/GeneralSettingsPanel.tsx`
  - Config path and global save/reload controls.
- Create: `frontend/src/workbench/settings/CompilerSettingsPanel.tsx`
  - Compiler mode, converter path, output directory.
- Create: `frontend/src/workbench/settings/WorkspaceSettingsPanel.tsx`
  - Recent projects, export, reset.
- Create: `frontend/src/workbench/settings/AiSettingsPanel.tsx`
  - AI source, model list, key/base/retries/preference, connection test.
- Modify: `frontend/src/workbench/settings/SettingsDrawer.tsx`
  - Keep drawer shell, resize, draft state, save orchestration, section expansion state.
  - Compose the extracted panels.
- Modify: `frontend/src/workbench/settings/SettingsDrawer.test.tsx`
  - Add default expanded/collapsed assertions and keep existing behavior tests.
- Modify: `frontend/src/styles.css`
  - Add collapsible section header styles and reuse existing field styles.

---

### Task 1: Add Collapsible SettingsSection

**Files:**
- Create: `frontend/src/workbench/settings/SettingsSection.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.test.tsx`

- [ ] **Step 1: Write failing tests for default section visibility**

Add a test that renders Settings and expects `General` and `AI` bodies visible while `Compiler`, `Workspace`, `Git`, and `Memory` bodies are hidden by default.

Use body markers with accessible text:

```tsx
expect(screen.getByText('Config file')).toBeTruthy()
expect(screen.getByText('Model')).toBeTruthy()
expect(screen.queryByText('LP_XMLConverter')).toBeNull()
expect(screen.queryByText('Recent HSF projects')).toBeNull()
expect(screen.queryByText('Project Git')).toBeNull()
expect(screen.queryByText('Learned error lessons')).toBeNull()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm test -- --run src/workbench/settings/SettingsDrawer.test.tsx
```

Expected: FAIL because Settings sections are not collapsible yet.

- [ ] **Step 3: Implement `SettingsSection.tsx`**

Create a component with this interface:

```tsx
import type { ReactNode } from 'react'

interface SettingsSectionProps {
  id: string
  title: string
  summary?: string
  modified?: boolean
  expanded: boolean
  onToggle: (id: string) => void
  children: ReactNode
}

export function SettingsSection({ id, title, summary, modified = false, expanded, onToggle, children }: SettingsSectionProps) {
  return (
    <section className={`settings-section${expanded ? ' expanded' : ' collapsed'}`} data-section={id}>
      <button
        type="button"
        className="settings-section-toggle"
        aria-expanded={expanded}
        aria-controls={`settings-section-${id}`}
        onClick={() => onToggle(id)}
      >
        <span>
          <strong>{title}</strong>
          {summary ? <small>{summary}</small> : null}
        </span>
        {modified ? <em>Modified</em> : null}
      </button>
      {expanded ? (
        <div className="settings-section-body" id={`settings-section-${id}`}>
          {children}
        </div>
      ) : null}
    </section>
  )
}
```

- [ ] **Step 4: Wire section expansion state in `SettingsDrawer.tsx`**

Add state:

```tsx
type SettingsSectionId = 'general' | 'ai' | 'compiler' | 'workspace' | 'git' | 'memory' | 'advanced'

const DEFAULT_EXPANDED_SECTIONS: Record<SettingsSectionId, boolean> = {
  general: true,
  ai: true,
  compiler: false,
  workspace: false,
  git: false,
  memory: false,
  advanced: false,
}
```

Add a toggle:

```tsx
function toggleSection(id: string) {
  setExpandedSections((sections) => ({
    ...sections,
    [id]: !sections[id as SettingsSectionId],
  }))
}
```

- [ ] **Step 5: Run section visibility test**

Run:

```bash
npm test -- --run src/workbench/settings/SettingsDrawer.test.tsx
```

Expected: PASS after sections are wrapped and collapsed correctly.

---

### Task 2: Extract General, Compiler, and Workspace Panels

**Files:**
- Create: `frontend/src/workbench/settings/GeneralSettingsPanel.tsx`
- Create: `frontend/src/workbench/settings/CompilerSettingsPanel.tsx`
- Create: `frontend/src/workbench/settings/WorkspaceSettingsPanel.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.test.tsx`

- [ ] **Step 1: Add panel props and components**

Create `GeneralSettingsPanel.tsx`:

```tsx
interface GeneralSettingsPanelProps {
  configPath: string
  saveState: 'saved' | 'dirty' | 'saving' | null
  saveError: string
  onReload: () => void
  onSave: () => void
}
```

Create `CompilerSettingsPanel.tsx`:

```tsx
import type { CompilerSettings } from '../../api/types'

interface CompilerSettingsPanelProps {
  draft: CompilerSettings
  onChange: (settings: CompilerSettings) => void
  onBrowseCompilerFile: () => void
  onBrowseOutputDirectory: () => void
}
```

Create `WorkspaceSettingsPanel.tsx`:

```tsx
import type { RecentProject } from '../../api/types'

interface WorkspaceSettingsPanelProps {
  recentProjects: RecentProject[]
  onOpenProjectPath: (path: string) => void
  onExportHsfProject: () => void
  onResetCurrentProject: () => void
}
```

- [ ] **Step 2: Move JSX from `SettingsDrawer.tsx` into these panels**

Keep the same labels and button names:

- `Config file`
- `Reload config`
- `Save Settings`
- `Compiler mode`
- `LP_XMLConverter`
- `Output directory`
- `Recent HSF projects`
- `Export HSF`
- `Reset Current Project`

- [ ] **Step 3: Preserve drawer callback behavior**

`SettingsDrawer.tsx` still owns:

```tsx
updateCompilerDraft()
saveSettings()
reloadRuntimeSettings()
browseCompilerDraft()
browseOutputDraft()
```

Panels receive only values and callbacks.

- [ ] **Step 4: Run tests**

Run:

```bash
npm test -- --run src/workbench/settings/SettingsDrawer.test.tsx
```

Expected: existing save order and resize tests still pass.

---

### Task 3: Extract AiSettingsPanel

**Files:**
- Create: `frontend/src/workbench/settings/AiSettingsPanel.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.test.tsx`

- [ ] **Step 1: Move AI model option derivation to `AiSettingsPanel.tsx`**

Move these derived values from `SettingsDrawer.tsx`:

```tsx
customModelOptions
officialModelOptions
fallbackModelOptions
knownModelIds
allModelOptions
selectedModelMeta
activeModelCategory
apiKeyHint
apiBaseHint
visibleModelOptions
```

- [ ] **Step 2: Define focused props**

`AiSettingsPanel.tsx` should receive:

```tsx
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

interface AiSettingsPanelProps {
  draft: LlmSettings
  testResult: LlmConnectionTestResult | null
  testing: boolean
  onChange: (settings: LlmSettings) => void
  onTestConnection: () => void
}
```

- [ ] **Step 3: Preserve existing AI behavior**

Keep these behaviors unchanged:

- Switching from Custom to Official clears `api_key` and `api_base`.
- Custom model selection uses custom provider `api_base`.
- Exact ID mode allows manual model input.
- Official/custom/exact segmented controls remain English.
- Official provider key hint remains visible.

- [ ] **Step 4: Run AI tests**

Run:

```bash
npm test -- --run src/workbench/settings/SettingsDrawer.test.tsx
```

Expected: official/custom/exact switching tests still pass.

---

### Task 4: Add Section Summaries and Modified Badges

**Files:**
- Modify: `frontend/src/workbench/settings/SettingsDrawer.tsx`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.test.tsx`

- [ ] **Step 1: Add summary helpers**

Add helpers in `SettingsDrawer.tsx`:

```tsx
function compilerSummary(settings: CompilerSettings) {
  return settings.mode === 'lp' ? 'LP' : 'Mock'
}

function aiSummary(settings: LlmSettings) {
  return settings.model || 'No model'
}

function workspaceSummary(recentProjects: RecentProject[]) {
  return `${recentProjects.length} recent`
}
```

- [ ] **Step 2: Add dirty helpers**

Add shallow comparisons:

```tsx
function compilerDirty(a: CompilerSettings, b: CompilerSettings) {
  return a.mode !== b.mode || a.converter_path !== b.converter_path || a.output_dir !== b.output_dir
}

function llmDirty(a: LlmSettings, b: LlmSettings) {
  return (
    a.model !== b.model ||
    a.api_key !== b.api_key ||
    a.api_base !== b.api_base ||
    a.max_retries !== b.max_retries ||
    a.assistant_settings !== b.assistant_settings
  )
}
```

- [ ] **Step 3: Add tests**

Assert:

```tsx
expect(screen.getByText('Mock')).toBeTruthy()
expect(screen.getByText('deepseek-chat')).toBeTruthy()
expect(screen.getByText('0 recent')).toBeTruthy()
```

After changing compiler mode:

```tsx
expect(screen.getByText('Modified')).toBeTruthy()
```

- [ ] **Step 4: Run tests**

Run:

```bash
npm test -- --run src/workbench/settings/SettingsDrawer.test.tsx
```

Expected: PASS.

---

### Task 5: CSS Polish and Full Verification

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/workbench/settings/SettingsDrawer.tsx`

- [ ] **Step 1: Add section toggle styles**

Add styles for:

```css
.settings-section-toggle
.settings-section-toggle span
.settings-section-toggle strong
.settings-section-toggle small
.settings-section-toggle em
.settings-section-body
.settings-section.collapsed
```

The header should be compact, horizontal, and readable at the minimum drawer
width.

- [ ] **Step 2: Remove obsolete nested section headings from extracted panels**

Panels should not render their own large section heading when wrapped by
`SettingsSection`. Field labels stay intact.

- [ ] **Step 3: Run frontend verification**

Run:

```bash
npm test -- --run
npm run build
```

Expected: all tests pass; build passes. Existing Vite chunk-size warning is
acceptable if unchanged.

- [ ] **Step 4: Run Python regression tests if backend was touched**

Only run if implementation changes backend files:

```bash
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add frontend/src/workbench/settings frontend/src/styles.css
git commit -m "feat: organize workbench settings drawer"
git push
```
