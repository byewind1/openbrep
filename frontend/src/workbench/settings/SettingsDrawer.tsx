import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type {
  CompilerSettings,
  ErrorLesson,
  LlmSettings,
  ProjectMemoryStatus,
  RecentProject,
  UpdateMemoryLessonRequest,
} from '../../api/types'
import { MemoryLessonsPanel } from './MemoryLessonsPanel'

interface SettingsDrawerProps {
  open: boolean
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  recentProjects: RecentProject[]
  memoryStatus: ProjectMemoryStatus | null
  memoryLessons: ErrorLesson[]
  memorySkillPreview: string
  memoryBusy: boolean
  onClose: () => void
  onCompilerSettingsChange: (settings: CompilerSettings) => void
  onLlmSettingsChange: (settings: LlmSettings) => void
  onReloadRuntimeSettings: () => void
  onBrowseCompilerFile: () => void
  onBrowseOutputDirectory: () => void
  onOpenProjectPath: (path: string) => void
  onExportHsfProject: () => void
  onResetCurrentProject: () => void
  onLoadMemoryLessons: () => void
  onSummarizeProjectMemory: () => void
  onUpdateMemoryLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => void
  onDeleteMemoryLesson: (fingerprint: string) => void
  onIgnoreMemoryLesson: (fingerprint: string) => void
  onClearProjectMemory: () => void
}

export function SettingsDrawer({
  open,
  compilerSettings,
  llmSettings,
  recentProjects,
  memoryStatus,
  memoryLessons,
  memorySkillPreview,
  memoryBusy,
  onClose,
  onCompilerSettingsChange,
  onLlmSettingsChange,
  onReloadRuntimeSettings,
  onBrowseCompilerFile,
  onBrowseOutputDirectory,
  onOpenProjectPath,
  onExportHsfProject,
  onResetCurrentProject,
  onLoadMemoryLessons,
  onSummarizeProjectMemory,
  onUpdateMemoryLesson,
  onDeleteMemoryLesson,
  onIgnoreMemoryLesson,
  onClearProjectMemory,
}: SettingsDrawerProps) {
  const [llmDraft, setLlmDraft] = useState(llmSettings)

  useEffect(() => {
    setLlmDraft(llmSettings)
  }, [llmSettings])

  useEffect(() => {
    if (open) {
      onLoadMemoryLessons()
    }
  }, [open, onLoadMemoryLessons])

  function submitLlmSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onLlmSettingsChange(llmDraft)
  }

  return (
    <>
      {open ? <button className="settings-scrim" type="button" aria-label="Close settings" onClick={onClose} /> : null}
      <aside className={`settings-drawer${open ? ' open' : ''}`} aria-hidden={!open} aria-label="Workbench settings">
        <div className="settings-header">
          <div>
            <strong>Settings</strong>
            <span>Workbench runtime</span>
          </div>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="settings-actions">
          <button type="button" onClick={onReloadRuntimeSettings}>
            Reload config
          </button>
        </div>

        <section className="settings-section">
          <div className="settings-section-heading">
            <strong>Compiler</strong>
            <span>Mock or LP_XMLConverter</span>
          </div>
          <label className="settings-row">
            <span>Mode</span>
            <select
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
          </label>
          <label className="settings-field">
            <span>LP_XMLConverter</span>
            <div className="settings-path-row">
              <input
                type="text"
                placeholder="/Applications/.../LP_XMLConverter"
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
                Browse
              </button>
            </div>
          </label>
          <label className="settings-field">
            <span>Output directory</span>
            <div className="settings-path-row">
              <input
                type="text"
                placeholder="Project sibling /output"
                value={compilerSettings.output_dir}
                onChange={(event) =>
                  onCompilerSettingsChange({
                    ...compilerSettings,
                    output_dir: event.currentTarget.value,
                  })
                }
              />
              <button type="button" onClick={onBrowseOutputDirectory}>
                Browse
              </button>
            </div>
          </label>
        </section>

        <form className="settings-section" onSubmit={submitLlmSettings}>
          <div className="settings-section-heading">
            <strong>AI</strong>
            <span>Model, endpoint and collaboration preference</span>
          </div>
          <label className="settings-field">
            <span>Model</span>
            <input
              type="text"
              list="openbrep-models"
              value={llmDraft.model}
              onChange={(event) => setLlmDraft({ ...llmDraft, model: event.currentTarget.value })}
            />
            <datalist id="openbrep-models">
              {llmDraft.models.map((model) => (
                <option value={model} key={model} />
              ))}
            </datalist>
          </label>
          <label className="settings-field">
            <span>API Key</span>
            <input
              type="password"
              value={llmDraft.api_key}
              placeholder="Provider API key"
              onChange={(event) => setLlmDraft({ ...llmDraft, api_key: event.currentTarget.value })}
            />
          </label>
          <label className="settings-field">
            <span>API Base URL</span>
            <input
              type="text"
              value={llmDraft.api_base}
              placeholder="Optional endpoint override"
              onChange={(event) => setLlmDraft({ ...llmDraft, api_base: event.currentTarget.value })}
            />
          </label>
          <label className="settings-row">
            <span>Max retries</span>
            <input
              type="number"
              min={1}
              max={10}
              value={llmDraft.max_retries}
              onChange={(event) =>
                setLlmDraft({
                  ...llmDraft,
                  max_retries: Number(event.currentTarget.value),
                })
              }
            />
          </label>
          <label className="settings-field">
            <span>Assistant preference</span>
            <textarea
              value={llmDraft.assistant_settings}
              placeholder="例如：先解释再给最小修改；优先保证可编译；不要大改结构。"
              onChange={(event) => setLlmDraft({ ...llmDraft, assistant_settings: event.currentTarget.value })}
            />
          </label>
          <div className="settings-submit-row">
            <button type="submit" className="primary-action">
              Save AI
            </button>
          </div>
        </form>

        <section className="settings-section">
          <div className="settings-section-heading">
            <strong>Workspace</strong>
            <span>Recent HSF projects and current session</span>
          </div>
          <div className="recent-project-list">
            {recentProjects.length ? (
              recentProjects.map((project) => (
                <button
                  type="button"
                  className="recent-project-item"
                  disabled={!project.exists}
                  key={project.path}
                  onClick={() => onOpenProjectPath(project.path)}
                  title={project.path}
                >
                  <span>{project.name || project.path}</span>
                  {project.parent_dir ? <small>{project.parent_dir}</small> : null}
                  {!project.exists ? <em>missing</em> : null}
                </button>
              ))
            ) : (
              <span className="settings-empty">No recent HSF projects</span>
            )}
          </div>
          <div className="settings-submit-row">
            <button type="button" onClick={onExportHsfProject}>
              Export HSF
            </button>
            <button
              type="button"
              onClick={onResetCurrentProject}
              title="Reset the current workbench session without deleting files on disk"
            >
              Reset Current Project
            </button>
          </div>
        </section>

        <MemoryLessonsPanel
          memoryStatus={memoryStatus}
          lessons={memoryLessons}
          skillPreview={memorySkillPreview}
          busy={memoryBusy}
          formatBytes={formatBytes}
          onRefresh={onLoadMemoryLessons}
          onSummarize={onSummarizeProjectMemory}
          onUpdateLesson={onUpdateMemoryLesson}
          onDeleteLesson={onDeleteMemoryLesson}
          onIgnoreLesson={onIgnoreMemoryLesson}
          onClear={onClearProjectMemory}
        />

        <section className="settings-section muted">
          <div className="settings-section-heading">
            <strong>Advanced</strong>
            <span>Reserved for later local runtime controls</span>
          </div>
        </section>
      </aside>
    </>
  )
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
