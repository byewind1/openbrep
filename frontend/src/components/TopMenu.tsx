import type { WorkbenchProject } from '../api/types'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { useT } from '../i18n'

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
  lastSavedAt: string | null
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
  lastSavedAt,
  lastError,
  onClearError,
}: TopMenuProps) {
  const t = useT()
  const [buildMenuOpen, setBuildMenuOpen] = useState(false)
  const canCompile = Boolean(project?.path)
  const projectStatusKey = project ? (project.path ? 'saved' : 'unsaved') : 'empty'
  const projectStatusLabel = {
    saved: t('topMenu.status.saved'),
    unsaved: t('topMenu.status.unsaved'),
    empty: t('topMenu.status.empty'),
  }[projectStatusKey]

  function runBuildAction(action: () => void) {
    action()
    setBuildMenuOpen(false)
  }

  return (
    <header className="topbar">
      <div className="brand-lockup" title={project?.path ?? ''}>
        <span className="brand-mark">OB</span>
        <div>
          <strong>{project?.name ?? 'OpenBrep'}</strong>
          <span className="brand-project-path">{project?.path ?? project?.source ?? 'workbench'}</span>
        </div>
      </div>
      {projectControls}
      <nav className="menu-row" aria-label="Workbench actions">
        <button type="button" data-testid="save-script-button" disabled={!project || saving} onClick={onSave}>
          {saving ? '...' : t('topMenu.save')}
        </button>
        <button type="button" data-testid="compile-button" disabled={!canCompile || compiling} onClick={onCompile}>
          {compiling ? '...' : t('topMenu.compile')}
        </button>
        <div className="toolbar-menu build-menu">
          <button
            type="button"
            className="toolbar-menu-trigger"
            aria-haspopup="menu"
            aria-expanded={buildMenuOpen}
            aria-controls="build-menu-panel"
            onClick={() => setBuildMenuOpen((value) => !value)}
          >
            {t('topMenu.build')}
          </button>
          {buildMenuOpen ? (
            <div id="build-menu-panel" className="toolbar-menu-panel build-menu-panel" role="menu" aria-label="Build menu">
              <button
                type="button"
                role="menuitem"
                data-testid="mock-compile-button"
                disabled={!canCompile || compiling}
                onClick={() => runBuildAction(onMockCompile)}
              >
                {compiling ? '...' : t('topMenu.mockCompile')}
              </button>
              <button type="button" role="menuitem" disabled={!canCompile || compiling} onClick={() => runBuildAction(onCompile)}>
                {compiling ? '...' : t('topMenu.compile')}
              </button>
            </div>
          ) : null}
        </div>
        <button type="button" className="settings-trigger" onClick={onOpenSettings}>
          {t('topMenu.settings')}
        </button>
      </nav>
      <div className="topbar-status">
        {lastError ? (
          <button type="button" className="error-pill" title={lastError} onClick={onClearError}>
            {lastError}
          </button>
        ) : null}
        <span className={projectStatusKey === 'unsaved' ? 'status-pill changed' : 'status-pill'}>{projectStatusLabel}</span>
        <span className={hasDirtyScript ? 'status-pill changed' : 'status-pill'}>
          {hasDirtyScript ? t('topMenu.status.dirty') : t('topMenu.status.clean')}
        </span>
        <span className={hasDraftChanges ? 'status-pill changed' : 'status-pill'}>
          {hasDraftChanges ? t('topMenu.status.params') : t('topMenu.status.stable')}
        </span>
        {lastSavedAt ? <span className="status-pill">{t('topMenu.status.savedAt', { time: lastSavedAt })}</span> : null}
        <button className="primary-action" disabled={!hasDraftChanges || applying} onClick={onApply}>
          {applying ? '...' : t('topMenu.apply')}
        </button>
      </div>
    </header>
  )
}
