import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WorkbenchProject } from '../api/types'

interface TopMenuProps {
  project: WorkbenchProject | null
  hasDraftChanges: boolean
  onApply: () => void
  onLoadProjectPath: (path: string) => void
  applying: boolean
  loading: boolean
}

export function TopMenu({ project, hasDraftChanges, onApply, onLoadProjectPath, applying, loading }: TopMenuProps) {
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
          <strong>OpenBrep Studio</strong>
          <span>{project ? `${project.name} · ${project.source ?? 'workspace'}` : 'No project loaded'}</span>
        </div>
      </div>
      <form className="project-path-form" onSubmit={submitPath}>
        <label htmlFor="hsf-path">HSF project</label>
        <input
          id="hsf-path"
          type="text"
          placeholder="/path/to/Object"
          value={path}
          onChange={(event) => setPath(event.currentTarget.value)}
        />
        <button type="submit" disabled={loading || path.trim().length === 0}>
          {loading ? '加载中' : '加载'}
        </button>
      </form>
      <nav className="menu-row" aria-label="Migration status">
        <button type="button" disabled title="下一步接入旧版编译流程">
          编译 GSM
        </button>
        <button type="button" disabled title="下一步接入旧版 AI 生成 / 修改 / 解释">
          AI 协作
        </button>
        <button type="button" disabled title="后续接入 Archicad / Tapir">
          Archicad
        </button>
      </nav>
      <div className="topbar-status">
        <span className={hasDraftChanges ? 'status-pill changed' : 'status-pill'}>{hasDraftChanges ? '临时预览' : '已保存'}</span>
        <button className="primary-action" disabled={!hasDraftChanges || applying} onClick={onApply}>
          {applying ? '应用中' : '应用到 HSF'}
        </button>
      </div>
    </header>
  )
}
