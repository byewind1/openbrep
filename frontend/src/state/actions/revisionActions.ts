import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot } from '../workbenchStoreUtils'
import type { WorkbenchSnapshot } from '../../api/types'

export function createRevisionActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async loadRevisions() {
      set({ revisionLoading: true })
      const result = await api.listProjectRevisions()
      if (!result.ok) {
        set({
          revisionLoading: false,
          revisions: [],
          latestRevisionId: null,
        })
        return
      }
      set({
        revisionLoading: false,
        revisions: result.revisions ?? [],
        latestRevisionId: result.latest_revision_id ?? null,
      })
    },

    async saveRevision(message = '') {
      set({ revisionLoading: true, lastError: null })
      const result = await api.saveProjectRevision(message)
      if (!result.ok) {
        set({
          revisionLoading: false,
          lastError: result.error ?? 'Failed to save revision.',
        })
        return
      }
      await get().loadRevisions()
      set((state) => ({
        compileLog: [`Saved revision ${result.revision?.revision_id ?? ''}`.trim(), ...state.compileLog].slice(0, 20),
      }))
    },

    async restoreRevision(revisionId: string) {
      const target = revisionId.trim()
      if (!target) return
      set({ revisionLoading: true, lastError: null })
      const result = await api.restoreProjectRevision(target)
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({
          revisionLoading: false,
          lastError: result.error ?? `Failed to restore revision: ${target}`,
        })
        return
      }
      set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings, get().llmSettings))
      await get().loadScripts()
      await get().loadRevisions()
      set((state) => ({
        revisionLoading: false,
        compileLog: [`Restored revision ${target}`, ...state.compileLog].slice(0, 20),
      }))
    },
  }
}
