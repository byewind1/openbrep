import type { TapirStatus } from '../../api/types'

interface TapirPanelProps {
  status: TapirStatus | null
  busy: boolean
  onRefresh: () => void
  onReloadLibraries: () => void
  onSyncSelection: () => void
  onHighlightSelection: () => void
  onLoadParameters: () => void
  onApplyParameters: () => void
}

export function TapirPanel({
  status,
  busy,
  onRefresh,
  onReloadLibraries,
  onSyncSelection,
  onHighlightSelection,
  onLoadParameters,
  onApplyParameters,
}: TapirPanelProps) {
  const message = status?.message ?? 'Tapir status not loaded'
  const selectedCount = status?.selected_guids.length ?? 0
  const parameterCount = status?.selected_params.reduce((total, row) => {
    const params = row.gdlParameters
    return total + (Array.isArray(params) ? params.length : 0)
  }, 0) ?? 0

  return (
    <div className="tapir-panel">
      <div className="tapir-panel-header">
        <div>
          <strong>Archicad</strong>
          <span>{message}</span>
        </div>
        <button type="button" onClick={onRefresh} disabled={busy}>
          Refresh
        </button>
      </div>

      <div className="tapir-status-grid">
        <StatusItem label="Bridge" value={status?.import_ok ? 'Loaded' : 'Missing'} />
        <StatusItem label="Archicad" value={status?.archicad_connected ? 'Connected' : 'Offline'} />
        <StatusItem label="Tapir" value={status?.tapir_available ? 'Available' : 'Unavailable'} />
        <StatusItem label="Selection" value={`${selectedCount}`} />
      </div>

      {status?.version ? <p className="tapir-version">{status.version}</p> : null}
      {status?.last_error ? <p className="tapir-error">{status.last_error}</p> : null}

      <div className="tapir-action-grid">
        <button type="button" onClick={onReloadLibraries} disabled={busy || !status?.import_ok}>
          Reload Libraries
        </button>
        <button type="button" onClick={onSyncSelection} disabled={busy}>
          Read Selection
        </button>
        <button type="button" onClick={onHighlightSelection} disabled={busy || selectedCount === 0}>
          Highlight
        </button>
        <button type="button" onClick={onLoadParameters} disabled={busy || selectedCount === 0}>
          Read Params
        </button>
        <button type="button" onClick={onApplyParameters} disabled={busy || parameterCount === 0}>
          Write Params
        </button>
      </div>

      {selectedCount ? (
        <div className="tapir-selection-list">
          {status?.selected_details.map((detail, index) => (
            <div className="tapir-selection-item" key={String(detail.guid ?? status.selected_guids[index] ?? index)}>
              <strong>{String(detail.name ?? detail.type ?? status.selected_guids[index] ?? 'Selected element')}</strong>
              <span>{String(detail.guid ?? status.selected_guids[index] ?? '')}</span>
            </div>
          ))}
        </div>
      ) : (
        <span className="settings-empty">No Archicad selection loaded</span>
      )}

      {status?.last_sync_at ? <p className="tapir-sync">Last sync: {status.last_sync_at}</p> : null}
    </div>
  )
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="tapir-status-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
