import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WorkbenchProject } from '../../api/types'

interface ProjectOpenControlsProps {
  project: WorkbenchProject | null
  loading: boolean
  onLoadProjectPath: (path: string) => void
  onBrowseProjectDirectory: () => void
  onImportGdlFile: () => void
  onImportGsmFile: () => void
}

export function ProjectOpenControls({
  project,
  loading,
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
      <button type="button" disabled={loading} onClick={onBrowseProjectDirectory}>
        ...
      </button>
      <button type="button" disabled={loading} onClick={onImportGdlFile}>
        Import GDL
      </button>
      <button type="button" disabled={loading} onClick={onImportGsmFile}>
        Import GSM
      </button>
    </form>
  )
}
