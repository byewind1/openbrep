import type { PreviewQuality } from '../../api/types'
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
      const preview = await api.fetchPreview(get().draftParameters, dirtyScriptBuffers(), get().previewQuality)
      set({ preview, warnings: preview.warnings ?? [] })
    },

    async loadPreview2D() {
      const preview2d = await api.fetchPreview2D(get().draftParameters, dirtyScriptBuffers(), get().previewQuality)
      set({ preview2d, warnings: preview2d.warnings ?? [] })
    },

    async setPreviewQuality(quality: PreviewQuality) {
      set({ previewQuality: quality })
      await get().loadPreview3D()
      // 2D 同一参数链：当前 2D tab 活跃时一并按新档刷新
      if (get().activeRailPanel === '2d') {
        await get().loadPreview2D()
      }
    },
  }
}
