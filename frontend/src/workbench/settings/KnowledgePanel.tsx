import type { KnowledgeStatus } from '../../api/types'

interface KnowledgePanelProps {
  status: KnowledgeStatus | null
  busy: boolean
  onRefresh: () => void
  onReload: () => void
}

export function KnowledgePanel({ status, busy, onRefresh, onReload }: KnowledgePanelProps) {
  return (
    <div className="knowledge-panel settings-section-body">
      <div className="knowledge-actions">
        <button
          type="button"
          className="settings-icon-btn"
          title="Refresh knowledge status"
          disabled={busy}
          onClick={onRefresh}
        >
          ↺
        </button>
        <button
          type="button"
          className="settings-save-btn"
          title="Hot-reload knowledge base from disk"
          disabled={busy}
          onClick={onReload}
        >
          {busy ? 'Reloading…' : 'Reload KB'}
        </button>
      </div>

      {status == null ? (
        <p className="knowledge-hint">Loading…</p>
      ) : !status.ok ? (
        <p className="knowledge-error">{status.error ?? 'Failed to load knowledge status.'}</p>
      ) : (
        <>
          <div className="knowledge-tier-row">
            <span className="knowledge-badge knowledge-badge-free">Free</span>
            <span className="knowledge-count">{status.free_doc_count} docs</span>
          </div>

          <div className="knowledge-tier-row">
            <span className={`knowledge-badge ${status.has_pro ? 'knowledge-badge-pro' : 'knowledge-badge-inactive'}`}>
              Pro {status.has_pro ? '✓' : '—'}
            </span>
            <span className="knowledge-count">{status.pro_doc_count} docs</span>
          </div>

          {!status.has_pro && (
            <div className="knowledge-hint">
              <p>Put <code>.md</code> files here to activate Pro:</p>
              <code className="knowledge-path">{status.pro_dir}</code>
            </div>
          )}

          {status.has_pro && status.pro_doc_names.length > 0 && (
            <details className="knowledge-pro-list">
              <summary>{status.pro_doc_count} Pro docs loaded</summary>
              <ul>
                {status.pro_doc_names.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  )
}
