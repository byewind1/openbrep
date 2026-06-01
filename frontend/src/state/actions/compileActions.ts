import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { buildMockCompileSummary, compileIssuesFromResult } from '../workbenchStoreUtils'

export function createCompileActions({ api, get, set }: WorkbenchActionContext) {
  async function saveDirtyScriptsBeforeCompile() {
    const dirtyScriptNames = Object.entries(get().dirtyScripts)
      .filter(([, dirty]) => dirty)
      .map(([name]) => name)
    let didSave = false

    for (const scriptName of dirtyScriptNames) {
      const content = get().scriptContents[scriptName]
      if (typeof content !== 'string') continue

      const result = await api.saveProjectScript(scriptName, content)
      if (!result.success) {
        const error = result.error ?? `Failed to save ${scriptName} before compile.`
        set((state) => ({
          compiling: false,
          lastError: error,
          compileLog: [`Compile stopped: ${error}`, ...state.compileLog].slice(0, 20),
        }))
        return false
      }

      set((state) => ({
        dirtyScripts: { ...state.dirtyScripts, [scriptName]: false },
        compileLog: [`Saved ${scriptName} before compile`, ...state.compileLog].slice(0, 20),
      }))
      didSave = true
    }

    return { ok: true, didSave }
  }

  async function refreshPreviewFromSavedSource() {
    const preview = await api.fetchPreview(get().draftParameters)
    set({ preview, warnings: preview.warnings ?? [] })
  }

  return {
    async compileCurrentProject() {
      set({ compiling: true })
      const saveResult = await saveDirtyScriptsBeforeCompile()
      if (!saveResult) return
      const result = await api.compileProject(get().compilerSettings.output_dir)
      const issues = compileIssuesFromResult(result)
      const message =
        result.ok && result.compile
          ? `${result.compile.mode === 'mock' ? 'Mock' : 'LP'} compile passed: ${result.compile.output_path}`
          : `Compile failed: ${result.error ?? 'Unknown error'}`
      set((state) => ({
        compileLog: [message, ...state.compileLog].slice(0, 20),
        mockCompileResult: {
          success: Boolean(result.compile?.success),
          mode: result.compile?.mode ?? state.compilerSettings.mode,
          issues,
          duration_ms: 0,
          output_path: result.compile?.output_path,
          gsm_size_bytes: result.compile?.gsm_size_bytes,
          parameter_count: result.compile?.parameter_count,
          error: result.error,
        },
        compiling: false,
      }))
      if (saveResult.didSave) {
        await refreshPreviewFromSavedSource()
      }
    },

    async runMockCompile() {
      set({ compiling: true })
      const saveResult = await saveDirtyScriptsBeforeCompile()
      if (!saveResult) return
      const result = await api.mockCompile(get().compilerSettings.output_dir)
      const summary = buildMockCompileSummary(result)
      set((state) => ({
        compiling: false,
        mockCompileResult: result,
        compileLog: summary ? [summary, ...state.compileLog].slice(0, 20) : state.compileLog,
      }))
      if (saveResult.didSave) {
        await refreshPreviewFromSavedSource()
      }
    },

    async revealCompileOutput(path = '') {
      const result = await api.revealArtifact(path)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to reveal artifact.' })
        return
      }
      set((state) => ({
        compileLog: [`Revealed ${result.path ?? path}`, ...state.compileLog].slice(0, 20),
      }))
    },
  }
}
