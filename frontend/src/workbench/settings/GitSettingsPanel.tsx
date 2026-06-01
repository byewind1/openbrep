import type { ProjectGitStatus } from '../../api/types'

interface GitSettingsPanelProps {
  gitStatus: ProjectGitStatus | null
  gitBusy: boolean
  message: string
  onMessageChange: (message: string) => void
  onRefresh: () => void
  onInitialize: () => void
  onSetEnabled: (enabled: boolean) => void
  onCommit: (message: string) => void
}

export function GitSettingsPanel({
  gitStatus,
  gitBusy,
  message,
  onMessageChange,
  onRefresh,
  onInitialize,
  onSetEnabled,
  onCommit,
}: GitSettingsPanelProps) {
  return (
    <section className="settings-section">
      <div className="settings-section-heading">
        <strong>Git</strong>
        <span>Optional source control for the current HSF directory</span>
      </div>
      <label className="settings-row">
        <span>Enabled</span>
        <input
          type="checkbox"
          checked={Boolean(gitStatus?.enabled)}
          disabled={gitBusy || !gitStatus?.initialized}
          onChange={(event) => onSetEnabled(event.currentTarget.checked)}
        />
      </label>
      <div className="settings-metadata-grid">
        <span>Repository</span>
        <strong>{gitStatus?.initialized ? 'Initialized' : 'Not initialized'}</strong>
        <span>Status</span>
        <strong>{gitStatus?.dirty ? `${gitStatus.changes.length} changes` : 'Clean'}</strong>
        <span>Last commit</span>
        <strong>{gitStatus?.last_commit || '-'}</strong>
      </div>
      {gitStatus?.changes.length ? (
        <div className="settings-code-list">
          {gitStatus.changes.slice(0, 8).map((change) => (
            <code key={change}>{change}</code>
          ))}
        </div>
      ) : null}
      <label className="settings-field">
        <span>Commit message</span>
        <input type="text" value={message} onChange={(event) => onMessageChange(event.currentTarget.value)} />
      </label>
      <div className="settings-submit-row">
        <button type="button" disabled={gitBusy || Boolean(gitStatus?.initialized)} onClick={onInitialize}>
          Initialize Git
        </button>
        <button
          type="button"
          disabled={gitBusy || !gitStatus?.initialized || !gitStatus?.enabled}
          onClick={() => onCommit(message)}
        >
          Commit
        </button>
        <button type="button" disabled={gitBusy} onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </section>
  )
}
