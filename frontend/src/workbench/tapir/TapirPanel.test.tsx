import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { TapirPanel } from './TapirPanel'

describe('TapirPanel', () => {
  test('shows degraded bridge status and exposes refresh/sync actions', () => {
    const onRefresh = vi.fn()
    const onSyncSelection = vi.fn()

    render(
      <TapirPanel
        status={{
          import_ok: false,
          available: false,
          archicad_connected: false,
          tapir_available: false,
          version: '',
          message: 'Tapir bridge 未导入',
          selected_guids: [],
          selected_details: [],
          selected_params: [],
          param_edits: {},
          last_error: '',
          last_sync_at: '',
        }}
        busy={false}
        onRefresh={onRefresh}
        onReloadLibraries={vi.fn()}
        onSyncSelection={onSyncSelection}
        onHighlightSelection={vi.fn()}
        onLoadParameters={vi.fn()}
        onApplyParameters={vi.fn()}
      />,
    )

    expect(screen.getByText('Tapir bridge 未导入')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    fireEvent.click(screen.getByRole('button', { name: 'Read Selection' }))

    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(onSyncSelection).toHaveBeenCalledTimes(1)
  })
})
