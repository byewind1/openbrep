import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { CompilerSettings, WorkbenchProject } from '../api/types'

interface TopMenuProps {
  project: WorkbenchProject | null
  hasDraftChanges: boolean
  onApply: () => void
  onLoadProjectPath: (path: string) => void
  onBrowseProjectDirectory: () => void
  onCompile: () => void
  compilerSettings: CompilerSettings
  onCompilerSettingsChange: (settings: CompilerSettings) => void
  onBrowseCompilerFile: () => void
  applying: boolean
  loading: boolean
  compiling: boolean
}

export function TopMenu({
  project,
  hasDraftChanges,
  onApply,
  onLoadProjectPath,
  onBrowseProjectDirectory,
  onCompile,
  compilerSettings,
  onCompilerSettingsChange,
  onBrowseCompilerFile,
  applying,
  loading,
  compiling,
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
        <button type="button" disabled={loading} onClick={onBrowseProjectDirectory}>
          Browse
        </button>
      </form>
      <nav className="menu-row" aria-label="Migration status">
        <select
          aria-label="Compiler mode"
          value={compilerSettings.mode}
          onChange={(event) =>
            onCompilerSettingsChange({
              ...compilerSettings,
              mode: event.currentTarget.value === 'lp' ? 'lp' : 'mock',
            })
          }
        >
          <option value="mock">Mock</option>
          <option value="lp">LP</option>
        </select>
        <input
          className="converter-path-input"
          type="text"
          placeholder="LP_XMLConverter path"
          value={compilerSettings.converter_path}
          disabled={compilerSettings.mode !== 'lp'}
          onChange={(event) =>
            onCompilerSettingsChange({
              ...compilerSettings,
              converter_path: event.currentTarget.value,
            })
          }
        />
        <button type="button" disabled={compilerSettings.mode !== 'lp'} onClick={onBrowseCompilerFile}>
          Browse LP
        </button>
        <button type="button" disabled={!project?.path || hasDraftChanges || compiling} onClick={onCompile}>
          {compiling ? '编译中' : '编译 GSM'}
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
