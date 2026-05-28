import type { CompilerSettings } from '../api/types'

interface SettingsDrawerProps {
  open: boolean
  compilerSettings: CompilerSettings
  onClose: () => void
  onCompilerSettingsChange: (settings: CompilerSettings) => void
  onBrowseCompilerFile: () => void
}

export function SettingsDrawer({
  open,
  compilerSettings,
  onClose,
  onCompilerSettingsChange,
  onBrowseCompilerFile,
}: SettingsDrawerProps) {
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
        </section>

        <section className="settings-section muted">
          <div className="settings-section-heading">
            <strong>AI</strong>
            <span>Uses current OpenBrep LLM configuration</span>
          </div>
        </section>

        <section className="settings-section muted">
          <div className="settings-section-heading">
            <strong>Workspace</strong>
            <span>Project path stays in the top bar for now</span>
          </div>
        </section>

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
