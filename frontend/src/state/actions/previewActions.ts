import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createPreviewActions({ api, get, set }: WorkbenchActionContext) {
  function dirtyScriptBuffers() {
    return Object.fromEntries(
      Object.entries(get().dirtyScripts)
        .filter(([, dirty]) => dirty)
        .map(([name]) => [name, get().scriptContents[name]])
        .filter(([, content]) => typeof content === 'string'),
    ) as Record<string, string>
  }

  return {
    async loadPreview3D() {
      const preview = await api.fetchPreview(get().draftParameters, dirtyScriptBuffers())
      set({ preview, warnings: preview.warnings ?? [] })
    },

    async loadPreview2D() {
      const preview2d = await api.fetchPreview2D(get().draftParameters, dirtyScriptBuffers())
      set({ preview2d, warnings: preview2d.warnings ?? [] })
    },
  }
}
