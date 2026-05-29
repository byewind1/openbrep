import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { buildMockCompileSummary, compileIssuesFromResult } from '../workbenchStoreUtils'

export function createCompileActions({ api, set }: WorkbenchActionContext) {
  return {
    async compileCurrentProject() {
      set({ compiling: true })
      const result = await api.compileProject()
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
    },

    async runMockCompile() {
      set({ compiling: true })
      const result = await api.mockCompile()
      const summary = buildMockCompileSummary(result)
      set((state) => ({
        compiling: false,
        mockCompileResult: result,
        compileLog: summary ? [summary, ...state.compileLog].slice(0, 20) : state.compileLog,
      }))
    },
  }
}
