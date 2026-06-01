import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { RecentProject, WorkbenchProject } from '../../api/types'

interface ProjectOpenControlsProps {
  project: WorkbenchProject | null
  loading: boolean
  recentProjects: RecentProject[]
  onLoadProjectPath: (path: string) => void
  onBrowseProjectDirectory: () => void
  onImportGdlFile: () => void
  onImportGsmFile: () => void
}

export function ProjectOpenControls({
  project,
  loading,
  recentProjects,
  onLoadProjectPath,
  onBrowseProjectDirectory,
  onImportGdlFile,
  onImportGsmFile,
}: ProjectOpenControlsProps) {
  const [path, setPath] = useState(project?.path ?? '')

  useEffect(() => {
    setPath(project?.path ?? '')
  }, [project?.path])

  function submitPath(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onLoadProjectPath(path)
  }

  function displayName(recent: RecentProject) {
    return recent.name || recent.path.split(/[\\/]/).filter(Boolean).pop() || recent.path
  }

  return (
    <form className="project-open-controls" onSubmit={submitPath}>
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
      <button
        type="button"
        disabled={loading}
        onClick={onBrowseProjectDirectory}
        title="Browse for an HSF project directory"
        aria-label="Browse for an HSF project directory"
      >
        {loading ? 'Choosing...' : 'Browse'}
      </button>
      <select
        aria-label="Recent HSF projects"
        value=""
        disabled={loading || recentProjects.length === 0}
        onChange={(event) => {
          const selectedPath = event.currentTarget.value
          if (selectedPath) onLoadProjectPath(selectedPath)
        }}
      >
        <option value="">Recent</option>
        {recentProjects.map((recent) => (
          <option key={recent.path} value={recent.path} disabled={!recent.exists}>
            {displayName(recent)}
          </option>
        ))}
      </select>
      <button type="button" disabled={loading} onClick={onImportGdlFile}>
        Import GDL
      </button>
      <button type="button" disabled={loading} onClick={onImportGsmFile}>
        Import GSM
      </button>
    </form>
  )
}
