interface GeneralSettingsPanelProps {
  configPath: string
  saveState: 'saved' | 'dirty' | 'saving' | null
  saveError: string
  onReload: () => void
  onSave: () => void
}

export function GeneralSettingsPanel({ configPath, saveState, saveError, onReload, onSave }: GeneralSettingsPanelProps) {
  return (
    <>
      <div className="settings-metadata-grid">
        <span>Config file</span>
        <strong title={configPath}>{configPath}</strong>
      </div>
      <div className="settings-actions inline">
        <button type="button" onClick={onReload}>
          Reload config
        </button>
        <button type="button" className="primary-action" disabled={saveState === 'saving'} onClick={onSave}>
          {saveState === 'saving' ? 'Saving...' : 'Save Settings'}
        </button>
        {saveState ? (
          <span
            className={
              saveState === 'dirty'
                ? 'settings-dirty-state'
                : saveState === 'saving'
                  ? 'settings-saving-state'
                  : 'settings-saved-state'
            }
          >
            {saveState === 'dirty' ? 'Unsaved changes' : saveState === 'saving' ? 'Saving' : 'Saved'}
          </span>
        ) : null}
        {saveError ? <span className="settings-save-error">{saveError}</span> : null}
      </div>
    </>
  )
}
