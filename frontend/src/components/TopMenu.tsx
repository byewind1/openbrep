import type { WorkbenchProject } from '../api/types'
import type { ReactNode } from 'react'

interface TopMenuProps {
  project: WorkbenchProject | null
  projectControls: ReactNode
  hasDraftChanges: boolean
  onApply: () => void
  onCompile: () => void
  onMockCompile: () => void
  onSave: () => void
  onSaveAs: () => void
  onOpenSettings: () => void
  applying: boolean
  loading: boolean
  compiling: boolean
  saving: boolean
  hasDirtyScript: boolean
  lastError: string | null
  onClearError: () => void
}

export function TopMenu({
  project,
  projectControls,
  hasDraftChanges,
  onApply,
  onCompile,
  onMockCompile,
  onSave,
  onSaveAs,
  onOpenSettings,
  applying,
  loading,
  compiling,
  saving,
  hasDirtyScript,
  lastError,
  onClearError,
}: TopMenuProps) {
  const canCompile = Boolean(project?.path)
  const projectStatus = project ? (project.path ? 'Saved' : 'Unsaved') : 'Empty'

  return (
    <header className="topbar">
      <div className="brand-lockup">
        <span className="brand-mark">OB</span>
        <div>
          <strong>{project?.name ?? 'OpenBrep'}</strong>
          <span>{project?.source ?? 'workbench'}</span>
        </div>
      </div>
      {projectControls}
      <nav className="menu-row" aria-label="Migration status">
        <button type="button" data-testid="save-script-button" disabled={!project || saving} onClick={onSave}>
          {saving ? '...' : 'Save'}
        </button>
        <button type="button" disabled={!project || saving} onClick={onSaveAs}>
          Save As
        </button>
        <button type="button" data-testid="mock-compile-button" disabled={!canCompile || compiling} onClick={onMockCompile}>
          {compiling ? '...' : 'Mock'}
        </button>
        <button type="button" data-testid="compile-button" disabled={!canCompile || compiling} onClick={onCompile}>
          {compiling ? '...' : 'Compile'}
        </button>
        <button type="button" className="settings-trigger" onClick={onOpenSettings}>
          Settings
        </button>
      </nav>
      <div className="topbar-status">
        {lastError ? (
          <button type="button" className="error-pill" title={lastError} onClick={onClearError}>
            {lastError}
          </button>
        ) : null}
        <span className={projectStatus === 'Unsaved' ? 'status-pill changed' : 'status-pill'}>{projectStatus}</span>
        <span className={hasDirtyScript ? 'status-pill changed' : 'status-pill'}>{hasDirtyScript ? 'Dirty' : 'Clean'}</span>
        <span className={hasDraftChanges ? 'status-pill changed' : 'status-pill'}>{hasDraftChanges ? 'Params' : 'Stable'}</span>
        <button className="primary-action" disabled={!hasDraftChanges || applying} onClick={onApply}>
          {applying ? '...' : 'Apply'}
        </button>
      </div>
    </header>
  )
}
