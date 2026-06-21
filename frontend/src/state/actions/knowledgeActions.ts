import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createKnowledgeActions({ api, set }: WorkbenchActionContext) {
  return {
    async loadKnowledgeStatus() {
      const result = await api.fetchKnowledgeStatus()
      if (!result.ok) {
        return
      }
      set({ knowledgeStatus: result })
    },

    async reloadKnowledge() {
      set({ knowledgeBusy: true })
      const result = await api.reloadKnowledge()
      set({ knowledgeBusy: false })
      if (result.ok) {
        set({ knowledgeStatus: result })
      } else {
        set({ lastError: result.error ?? 'Failed to reload knowledge base.' })
      }
    },
  }
}
