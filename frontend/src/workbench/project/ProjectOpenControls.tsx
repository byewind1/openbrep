import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { RecentProject, WorkbenchProject } from '../../api/types'

interface ProjectOpenControlsProps {
  project: WorkbenchProject | null
  loading: boolean
  /** SF1：源操作进行中禁用项目切换/导入入口（handler 自身另有守卫判断） */
  sourceBusy?: boolean
  recentProjects: RecentProject[]
  onNewProject: () => void
  onLoadProjectPath: (path: string) => void
  onBrowseProjectDirectory: () => void
  onImportGdlFile: () => void
  onImportGsmFile: () => void
  onImportBlenderScript: () => void
  onSaveProjectAs: () => void
}

export function ProjectOpenControls({
  project,
  loading,
  sourceBusy = false,
  recentProjects,
  onNewProject,
  onLoadProjectPath,
  onBrowseProjectDirectory,
  onImportGdlFile,
  onImportGsmFile,
  onImportBlenderScript,
  onSaveProjectAs,
}: ProjectOpenControlsProps) {
  const [path, setPath] = useState(project?.path ?? '')
  const [open, setOpen] = useState(false)
  const controlsLocked = loading || sourceBusy

  useEffect(() => {
    setPath(project?.path ?? '')
  }, [project?.path])

  function submitPath(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!path.trim()) return
    onLoadProjectPath(path)
    setOpen(false)
  }

  function displayName(recent: RecentProject) {
    return recent.name || recent.path.split(/[\\/]/).filter(Boolean).pop() || recent.path
  }

  function runProjectAction(action: () => void) {
    action()
    setOpen(false)
  }

  return (
    <div className="project-open-controls toolbar-menu">
      <button
        type="button"
        className="toolbar-menu-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="project-menu-panel"
        onClick={() => setOpen((value) => !value)}
      >
        Project
      </button>
      {open ? (
        <form id="project-menu-panel" className="toolbar-menu-panel project-menu-panel" aria-label="Project menu" onSubmit={submitPath}>
          <button type="button" disabled={controlsLocked} onClick={() => runProjectAction(onNewProject)}>
            New
          </button>
          <label className="project-path-field">
            <span>HSF path</span>
            <input
              id="hsf-path"
              type="text"
              aria-label="HSF project path"
              placeholder="HSF project path"
              value={path}
              onChange={(event) => setPath(event.currentTarget.value)}
            />
          </label>
          <div className="project-menu-row">
            <button type="submit" disabled={controlsLocked || path.trim().length === 0}>
              {loading ? '...' : 'Open'}
            </button>
            <button
              type="button"
              disabled={controlsLocked}
              onClick={() => runProjectAction(onBrowseProjectDirectory)}
              title="Browse for an HSF project directory"
              aria-label="Browse for an HSF project directory"
            >
              {loading ? 'Choosing...' : 'Browse'}
            </button>
          </div>
          <select
            aria-label="Recent HSF projects"
            value=""
            disabled={controlsLocked || recentProjects.length === 0}
            onChange={(event) => {
              const selectedPath = event.currentTarget.value
              if (selectedPath) {
                onLoadProjectPath(selectedPath)
                setOpen(false)
              }
            }}
          >
            <option value="">Recent</option>
            {recentProjects.map((recent) => (
              <option key={recent.path} value={recent.path} disabled={!recent.exists}>
                {displayName(recent)}
              </option>
            ))}
          </select>
          <div className="project-menu-row">
            <button type="button" disabled={controlsLocked} onClick={() => runProjectAction(onImportGdlFile)}>
              Import GDL
            </button>
            <button type="button" disabled={controlsLocked} onClick={() => runProjectAction(onImportGsmFile)}>
              Import GSM
            </button>
            <button type="button" disabled={controlsLocked} onClick={() => runProjectAction(onImportBlenderScript)}>
              Import Blender
            </button>
          </div>
          <button type="button" disabled={controlsLocked || !project} onClick={() => runProjectAction(onSaveProjectAs)}>
            Save As
          </button>
        </form>
      ) : null}
    </div>
  )
}
