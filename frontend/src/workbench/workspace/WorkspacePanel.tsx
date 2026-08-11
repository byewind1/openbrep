import { useState } from 'react'
import type { FormEvent } from 'react'
import { useT } from '../../i18n'
import { useThemedDialog } from '../../components/ThemedDialog'
import type { WorkspaceInfo, WorkspaceProject, WorkspaceSearchHit } from '../../api/types'

interface WorkspacePanelProps {
  workspace: WorkspaceInfo | null
  busy: boolean
  searching: boolean
  searchQuery: string | null
  searchHits: WorkspaceSearchHit[]
  workspaceInitHint: string | null
  onOpenWorkspace: (path: string) => void
  onInitWorkspace: (path: string) => void
  onCloseWorkspace: () => void
  onRefreshWorkspace: () => void
  onSearchWorkspace: (query: string) => void
  onBrowseDirectory: () => Promise<string | null>
  onDismissInitHint: () => void
  onTrashWorkspaceProject: (path: string) => void
  onLoadProjectPath: (path: string) => void
}

/** 工作区面板（P3-d2）：项目列表 + 跨项目搜索；空态支持浏览/初始化并附着（P3-d2b）。 */
export function WorkspacePanel({
  workspace,
  busy,
  searching,
  searchQuery,
  searchHits,
  workspaceInitHint,
  onOpenWorkspace,
  onInitWorkspace,
  onCloseWorkspace,
  onRefreshWorkspace,
  onSearchWorkspace,
  onBrowseDirectory,
  onDismissInitHint,
  onTrashWorkspaceProject,
  onLoadProjectPath,
}: WorkspacePanelProps) {
  const t = useT()
  const { confirm, dialogNode } = useThemedDialog()
  const [attachPath, setAttachPath] = useState('')
  const [query, setQuery] = useState('')

  function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!attachPath.trim()) return
    onOpenWorkspace(attachPath)
    setAttachPath('')
  }

  function initAttach() {
    if (!attachPath.trim()) return
    onInitWorkspace(attachPath)
    setAttachPath('')
  }

  async function browse() {
    const path = await onBrowseDirectory()
    if (path) {
      setAttachPath(path)
    }
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

  async function trashProject(project: WorkspaceProject) {
    if (project.active) return
    const confirmed = await confirm({
      title: t('workspace.delete'),
      message: t('workspace.deleteConfirm', { name: project.name }),
      danger: true,
    })
    if (!confirmed) return
    onTrashWorkspaceProject(project.path)
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
        <div className="workspace-empty">
          {workspaceInitHint ? (
            <div className="workspace-init-hint">
              <span className="workspace-init-hint-text">{t('workspace.notWorkspaceHint')}</span>
              <button
                type="button"
                className="workspace-init-hint-action"
                disabled={busy}
                onClick={() => onInitWorkspace(workspaceInitHint)}
              >
                {t('workspace.initAttach')}
              </button>
              <button
                type="button"
                className="workspace-icon-button"
                title={t('workspace.dismiss')}
                aria-label={t('workspace.dismiss')}
                onClick={onDismissInitHint}
              >
                ✕
              </button>
            </div>
          ) : null}
          <span className="workspace-empty-text">{t('workspace.notAttached')}</span>
          <form id="workspace-attach-form" className="workspace-empty-row" onSubmit={submitAttach}>
            <input
              type="text"
              aria-label={t('workspace.attachPlaceholder')}
              placeholder={t('workspace.attachPlaceholder')}
              value={attachPath}
              onChange={(event) => setAttachPath(event.currentTarget.value)}
            />
            <button type="button" className="workspace-browse-button" disabled={busy} onClick={() => void browse()}>
              {t('workspace.browse')}
            </button>
          </form>
          <div className="workspace-empty-actions">
            <button type="submit" form="workspace-attach-form" disabled={busy || !attachPath.trim()}>
              {t('workspace.attach')}
            </button>
            <button type="button" disabled={busy || !attachPath.trim()} onClick={initAttach}>
              {t('workspace.initAttach')}
            </button>
          </div>
        </div>
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
                <div
                  key={project.path}
                  className={`workspace-project-row${project.active ? ' active' : ''}`}
                >
                  <button
                    type="button"
                    className="workspace-project-item"
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
                  <button
                    type="button"
                    className="workspace-icon-button workspace-project-trash"
                    disabled={project.active}
                    title={project.active ? t('workspace.deleteActiveDisabled') : t('workspace.delete')}
                    aria-label={t('workspace.delete')}
                    onClick={() => trashProject(project)}
                  >
                    🗑
                  </button>
                </div>
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
      {dialogNode}
    </section>
  )
}
