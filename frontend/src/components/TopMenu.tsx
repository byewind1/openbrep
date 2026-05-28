import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WorkbenchProject } from '../api/types'

interface TopMenuProps {
  project: WorkbenchProject | null
  hasDraftChanges: boolean
  onApply: () => void
  onLoadProjectPath: (path: string) => void
  onBrowseProjectDirectory: () => void
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
  hasDraftChanges,
  onApply,
  onLoadProjectPath,
  onBrowseProjectDirectory,
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
  const [path, setPath] = useState(project?.path ?? '')

  useEffect(() => {
    setPath(project?.path ?? '')
  }, [project?.path])

  function submitPath(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onLoadProjectPath(path)
  }

  return (
    <header className="topbar">
      <div className="brand-lockup">
        <span className="brand-mark">OB</span>
        <div>
          <strong>{project?.name ?? 'OpenBrep'}</strong>
          <span>{project?.source ?? 'workbench'}</span>
        </div>
      </div>
      <form className="project-path-form" onSubmit={submitPath}>
        <input
          id="hsf-path"
          type="text"
          aria-label="HSF project path"
          placeholder="HSF project path"
          value={path}
          onChange={(event) => setPath(event.currentTarget.value)}
        />
        <button type="submit" disabled={loading || path.trim().length === 0}>
          {loading ? '...' : 'Open'}
        </button>
        <button type="button" disabled={loading} onClick={onBrowseProjectDirectory}>
          ...
        </button>
      </form>
      <nav className="menu-row" aria-label="Migration status">
        <button type="button" disabled={!activeScriptName || saving} onClick={onSave}>
          {saving ? '...' : 'Save'}
        </button>
        <button type="button" disabled={!project?.path || compiling} onClick={onMockCompile}>
          {compiling ? '...' : 'Mock'}
        </button>
        <button type="button" disabled={!project?.path || compiling} onClick={onCompile}>
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
