import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot } from '../workbenchStoreUtils'
import type { WorkbenchSnapshot } from '../../api/types'

export function createProjectActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async load() {
      set({ loading: true, lastError: null })
      const snapshot = await api.fetchSnapshot()
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async loadProjectPath(path: string) {
      const normalizedPath = path.trim()
      if (!normalizedPath) return
      set({ loading: true, lastError: null })
      const snapshot = await api.loadProjectPath(normalizedPath)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? `Failed to open HSF project: ${normalizedPath}`,
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async importGdlFile(path = '') {
      set({ loading: true, lastError: null })
      const snapshot = await api.importGdlFile(path)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to import GDL file.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async importGsmFile(path = '') {
      set({ loading: true, lastError: null })
      const snapshot = await api.importGsmFile(path)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to import GSM file.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async closeProject() {
      set({ loading: true, lastError: null })
      const snapshot = await api.closeProject()
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to close current project.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async browseProjectDirectory() {
      set({ loading: true, lastError: null })
      const result = await api.chooseProjectDirectory()
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({
          loading: false,
          lastError: result.cancelled ? null : result.error ?? 'Failed to open HSF project directory.',
        })
        return
      }
      set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set({ loading: false })
    },

    async loadRecentProjects() {
      const result = await api.listRecentProjects()
      if (result.ok) {
        set({ recentProjects: result.projects ?? [] })
      }
    },
  }
}
