import { useState } from 'react'
import type { FormEvent } from 'react'
import { useT } from '../../i18n'
import type { WorkspaceInfo, WorkspaceProject, WorkspaceSearchHit } from '../../api/types'

interface WorkspacePanelProps {
  workspace: WorkspaceInfo | null
  busy: boolean
  searching: boolean
  searchQuery: string | null
  searchHits: WorkspaceSearchHit[]
  onOpenWorkspace: (path: string) => void
  onCloseWorkspace: () => void
  onRefreshWorkspace: () => void
  onSearchWorkspace: (query: string) => void
  onLoadProjectPath: (path: string) => void
}

/** 工作区面板（P3-d2）：项目列表 + 跨项目搜索。放左侧栏顶部，其他面板不动。 */
export function WorkspacePanel({
  workspace,
  busy,
  searching,
  searchQuery,
  searchHits,
  onOpenWorkspace,
  onCloseWorkspace,
  onRefreshWorkspace,
  onSearchWorkspace,
  onLoadProjectPath,
}: WorkspacePanelProps) {
  const t = useT()
  const [attachPath, setAttachPath] = useState('')
  const [query, setQuery] = useState('')

  function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!attachPath.trim()) return
    onOpenWorkspace(attachPath)
    setAttachPath('')
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSearchWorkspace(query)
  }

  function projectOriginKind(project: WorkspaceProject) {
    return project.origin?.imported_kind ?? null
  }

  function hitSummary(hit: WorkspaceSearchHit) {
    if (hit.line != null) return `${hit.project} · ${hit.location}:${hit.line}`
    return `${hit.project} · ${hit.location}`
  }

  return (
    <section className="workspace-panel" aria-label={t('workspace.title')}>
      <div className="script-tree-header workspace-panel-header">
        <span>{t('workspace.title')}</span>
        {workspace ? (
          <span className="workspace-panel-actions">
            <button type="button" className="workspace-icon-button" title={t('workspace.refresh')} disabled={busy} onClick={onRefreshWorkspace}>
              ⟳
            </button>
            <button type="button" className="workspace-icon-button" title={t('workspace.close')} onClick={onCloseWorkspace}>
              ✕
            </button>
          </span>
        ) : null}
      </div>

      {!workspace ? (
        <form className="workspace-empty" onSubmit={submitAttach}>
          <span className="workspace-empty-text">{t('workspace.notAttached')}</span>
          <input
            type="text"
            aria-label={t('workspace.attachPlaceholder')}
            placeholder={t('workspace.attachPlaceholder')}
            value={attachPath}
            onChange={(event) => setAttachPath(event.currentTarget.value)}
          />
          <button type="submit" disabled={busy || !attachPath.trim()}>
            {t('workspace.attach')}
          </button>
        </form>
      ) : (
        <div className="workspace-panel-body">
          <form className="workspace-search" onSubmit={submitSearch}>
            <input
              type="text"
              aria-label={t('workspace.searchPlaceholder')}
              placeholder={t('workspace.searchPlaceholder')}
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
            />
            <button type="submit" disabled={searching || !query.trim()}>
              {t('workspace.search')}
            </button>
          </form>
          <div className="workspace-projects">
            {workspace.projects.length === 0 ? (
              <div className="workspace-empty-text">{t('workspace.noProjects')}</div>
            ) : (
              workspace.projects.map((project) => (
                <button
                  type="button"
                  key={project.path}
                  className={`workspace-project-item${project.active ? ' active' : ''}`}
                  onClick={() => onLoadProjectPath(project.path)}
                >
                  <span className="workspace-project-name">{project.name}</span>
                  {projectOriginKind(project) ? (
                    <span className="workspace-badge" title="imported">
                      {projectOriginKind(project)}
                    </span>
                  ) : null}
                  {project.artifact_count > 0 ? (
                    <span className="workspace-badge" title="artifacts">
                      {project.artifact_count}
                    </span>
                  ) : null}
                </button>
              ))
            )}
          </div>
          {searchQuery ? (
            <div className="workspace-search-results">
              <div className="workspace-search-results-title">{t('workspace.searchResults')}</div>
              {searchHits.length === 0 ? (
                <div className="workspace-empty-text">{t('workspace.searchEmpty')}</div>
              ) : (
                searchHits.map((hit, index) => (
                  <button
                    type="button"
                    key={`${hit.project}-${hit.location}-${hit.line ?? ''}-${index}`}
                    className="workspace-search-hit"
                    onClick={() => {
                      onLoadProjectPath(
                        workspace.projects.find((project) => project.name === hit.project)?.path ??
                          workspace.path,
                      )
                      setQuery('')
                      onSearchWorkspace('')
                    }}
                  >
                    <span className="workspace-search-hit-title">{hitSummary(hit)}</span>
                    <span className="workspace-search-hit-snippet">{hit.snippet}</span>
                  </button>
                ))
              )}
            </div>
          ) : null}
        </div>
      )}
    </section>
  )
}
