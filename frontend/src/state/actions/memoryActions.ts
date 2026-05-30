import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createMemoryActions({ api, set }: WorkbenchActionContext) {
  return {
    async loadMemoryStatus() {
      const result = await api.fetchMemoryStatus()
      if (!result.ok) {
        if (result.error) {
          set({ lastError: result.error })
        }
        return
      }
      set({ memoryStatus: result.memory ?? null })
    },

    async clearProjectMemory() {
      const result = await api.clearProjectMemory()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to clear project memory.' })
        return
      }
      const refreshed = await api.fetchMemoryStatus()
      set((state) => ({
        memoryStatus: refreshed.ok ? refreshed.memory ?? null : state.memoryStatus,
        assistantMessages: [],
        compileLog: ['Cleared project memory', ...state.compileLog].slice(0, 20),
        lastError: refreshed.ok ? null : refreshed.error ?? state.lastError,
      }))
    },
  }
}
