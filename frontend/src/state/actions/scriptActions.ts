import type { ProjectWorkspaceRefreshOptions, WorkbenchActionContext } from '../workbenchStoreTypes'
import { normalizeScriptName, pruneDirtyScripts, selectPreferredScript } from '../workbenchStoreUtils'

export function createScriptActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async refreshProjectWorkspace(options: ProjectWorkspaceRefreshOptions = {}) {
      const preferredScriptName = normalizeScriptName(options.preferredScriptName ?? '')
      const refreshAllScripts = options.refreshAllScripts ?? true
      const refreshPreview = options.refreshPreview ?? true
      const runDiagnostics = options.runDiagnostics ?? false

      await get().loadScripts()
      const existingScripts = get().scripts.filter((script) => script.exists)
      const targetNames = refreshAllScripts
        ? existingScripts.map((script) => script.name)
        : [preferredScriptName || get().activeScriptName || ''].filter(Boolean)

      for (const scriptName of [...new Set(targetNames)]) {
        const updated = await api.getProjectScript(scriptName)
        if (updated) {
          set((state) => ({
            scriptContents: { ...state.scriptContents, [scriptName]: updated.content },
            dirtyScripts: { ...state.dirtyScripts, [scriptName]: false },
          }))
        }
      }

      if (preferredScriptName && existingScripts.some((script) => script.name === preferredScriptName)) {
        await get().openScript(preferredScriptName)
      }

      if (refreshPreview) {
        const preview = await api.fetchPreview({})
        set({ preview, warnings: preview.warnings ?? [] })
      }

      if (runDiagnostics) {
        await get().runMockCompile()
      }
    },

    async loadScripts() {
      set({ scriptLoading: true })
      const result = await api.listProjectScripts()
      const scripts = result.scripts ?? []
      const activeScriptName = selectPreferredScript(scripts, get().activeScriptName)
      set((state) => ({
        scripts,
        activeScriptName,
        dirtyScripts: pruneDirtyScripts(state.dirtyScripts, scripts),
        scriptLoading: false,
      }))
      if (activeScriptName && !get().scriptContents[activeScriptName]) {
        await get().openScript(activeScriptName)
      }
    },

    async openScript(name: string) {
      const target = name.trim()
      if (!target) return
      const cached = get().scriptContents[target]
      if (typeof cached === 'string') {
        set({ activeScriptName: target, lastError: null })
        return
      }
      set({ scriptLoading: true, lastError: null })
      const result = await api.getProjectScript(target)
      if (!result) {
        set({
          scriptLoading: false,
          lastError: `Failed to open script: ${target}`,
        })
        return
      }
      set((state) => ({
        scriptLoading: false,
        activeScriptName: target,
        scriptContents: { ...state.scriptContents, [target]: result.content },
      }))
    },

    updateActiveScriptContent(content: string) {
      const activeScriptName = get().activeScriptName
      if (!activeScriptName) return
      set((state) => ({
        scriptContents: { ...state.scriptContents, [activeScriptName]: content },
        dirtyScripts: { ...state.dirtyScripts, [activeScriptName]: true },
      }))
    },

    async saveActiveScript() {
      const activeScriptName = get().activeScriptName
      if (!activeScriptName) return
      const content = get().scriptContents[activeScriptName]
      if (typeof content !== 'string') return
      set({ scriptSaving: true, lastError: null })
      const result = await api.saveProjectScript(activeScriptName, content)
      if (result.success) {
        set((state) => ({
          scriptSaving: false,
          dirtyScripts: { ...state.dirtyScripts, [activeScriptName]: false },
          compileLog: [`Saved ${activeScriptName} at ${result.saved_at}`, ...state.compileLog].slice(0, 20),
        }))
        await get().refreshProjectWorkspace({
          preferredScriptName: activeScriptName,
          refreshAllScripts: false,
          refreshPreview: true,
          runDiagnostics: true,
        })
        return
      }
      set({
        scriptSaving: false,
        lastError: result.error ?? `Failed to save script: ${activeScriptName}`,
      })
    },
  }
}
