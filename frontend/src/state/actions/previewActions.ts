import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createPreviewActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async loadPreview2D() {
      const preview2d = await api.fetchPreview2D(get().draftParameters)
      set({ preview2d, warnings: preview2d.warnings ?? [] })
    },
  }
}
