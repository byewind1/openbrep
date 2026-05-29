import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createParameterActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async setDraftParameter(name: string, value: unknown) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      const preview = await api.fetchPreview(draftParameters)
      set({ preview, warnings: preview.warnings ?? [] })
    },

    async applyDraftParameters() {
      const draft = get().draftParameters
      if (Object.keys(draft).length === 0) return
      set({ applying: true, lastError: null })
      const result = await api.applyParameters(draft)
      if (!result.ok) {
        set({
          applying: false,
          lastError: result.error ?? 'Failed to apply parameters.',
        })
        return
      }
      set({
        project: result.project,
        parameters: result.parameters,
        preview: result.preview,
        warnings: result.warnings,
        draftParameters: {},
        applying: false,
      })
      await get().loadScripts()
      await get().runMockCompile()
    },

    resetDraftParameters() {
      set({ draftParameters: {} })
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }
}
