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
  onOpenSettings: () => void
  applying: boolean
  loading: boolean
  compiling: boolean
  saving: boolean
  hasDirtyScript: boolean
  activeScriptName: string | null
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
  onOpenSettings,
  applying,
  loading,
  compiling,
  saving,
  hasDirtyScript,
  activeScriptName,
  lastError,
  onClearError,
}: TopMenuProps) {
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
        <button type="button" data-testid="save-script-button" disabled={!activeScriptName || saving} onClick={onSave}>
          {saving ? '...' : 'Save'}
        </button>
        <button type="button" data-testid="mock-compile-button" disabled={!project?.path || compiling} onClick={onMockCompile}>
          {compiling ? '...' : 'Mock'}
        </button>
        <button type="button" data-testid="compile-button" disabled={!project?.path || compiling} onClick={onCompile}>
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
        <span className={hasDirtyScript ? 'status-pill changed' : 'status-pill'}>{hasDirtyScript ? 'Dirty' : 'Saved'}</span>
        <span className={hasDraftChanges ? 'status-pill changed' : 'status-pill'}>{hasDraftChanges ? 'Params' : 'Stable'}</span>
        <button className="primary-action" disabled={!hasDraftChanges || applying} onClick={onApply}>
          {applying ? '...' : 'Apply'}
        </button>
      </div>
    </header>
  )
}
