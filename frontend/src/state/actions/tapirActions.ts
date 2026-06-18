import type { TapirActionResult, TapirStatusResult } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createTapirActions({ api, set, get }: WorkbenchActionContext) {
  async function runTapirAction(action: () => Promise<TapirActionResult>) {
    set({ tapirBusy: true, lastError: null })
    const result = await action()
    set((state) => ({
      tapirStatus: result.tapir ?? state.tapirStatus,
      tapirBusy: false,
      lastError: result.ok ? null : result.message ?? result.error ?? state.lastError,
      compileLog: result.message ? [result.message, ...state.compileLog].slice(0, 20) : state.compileLog,
    }))
  }

  function applyStatus(result: TapirStatusResult) {
    set((state) => ({
      tapirStatus: result.tapir ?? state.tapirStatus,
      tapirBusy: false,
      lastError: result.ok ? state.lastError : result.error ?? state.lastError,
    }))
  }

  return {
    async refreshTapirStatus() {
      set({ tapirBusy: true })
      applyStatus(await api.fetchTapirStatus())
    },

    async reloadTapirLibraries() {
      await runTapirAction(api.reloadTapirLibraries)
    },

    async syncTapirSelection() {
      await runTapirAction(api.syncTapirSelection)
    },

    async highlightTapirSelection() {
      await runTapirAction(api.highlightTapirSelection)
    },

    async loadTapirParameters() {
      await runTapirAction(api.loadTapirParameters)
    },

    async applyTapirParameters() {
      await runTapirAction(() => api.applyTapirParameterEdits(get().tapirStatus?.param_edits ?? {}))
    },
  }
}
