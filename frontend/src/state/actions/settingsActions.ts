import type { CompilerSettings, LlmSettings } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createSettingsActions({ api, set }: WorkbenchActionContext) {
  function applyGitResult(result: Awaited<ReturnType<typeof api.fetchProjectGitStatus>>) {
    if (!result.ok) {
      set({ lastError: result.error ?? 'Git operation failed.', gitBusy: false })
      return false
    }
    set({ gitStatus: result.git, gitBusy: false, lastError: null })
    return true
  }

  return {
    async setCompilerSettings(settings: CompilerSettings) {
      const result = await api.updateCompilerSettings(settings)
      if (result.ok && result.compiler) {
        set({ compilerSettings: result.compiler })
      }
    },

    async browseCompilerFile() {
      const result = await api.chooseCompilerFile()
      if (result.ok && result.compiler) {
        set({ compilerSettings: result.compiler })
      }
    },

    async browseOutputDirectory() {
      const result = await api.chooseOutputDirectory()
      if (result.ok && result.compiler) {
        set({ compilerSettings: result.compiler })
      }
    },

    async setLlmSettings(settings: LlmSettings) {
      const result = await api.updateLlmSettings(settings)
      if (result.ok && result.llm) {
        set({ llmSettings: result.llm })
      }
    },

    async testLlmConnection(settings: LlmSettings) {
      return api.testLlmConnection(settings)
    },

    async reloadRuntimeSettings() {
      const result = await api.fetchRuntimeSettings()
      set((state) => ({
        compilerSettings: result.compiler ?? state.compilerSettings,
        llmSettings: result.llm ?? state.llmSettings,
      }))
    },

    async loadProjectGitStatus() {
      set({ gitBusy: true })
      applyGitResult(await api.fetchProjectGitStatus())
    },

    async initializeProjectGit() {
      set({ gitBusy: true })
      applyGitResult(await api.initializeProjectGit())
    },

    async setProjectGitEnabled(enabled: boolean) {
      set({ gitBusy: true })
      applyGitResult(await api.updateProjectGitSettings(enabled))
    },

    async commitProjectGit(message = '') {
      set({ gitBusy: true })
      const result = await api.commitProjectGit(message)
      if (applyGitResult(result)) {
        set((state) => ({
          compileLog: [`Git commit: ${result.git.last_commit || result.message || 'no changes'}`, ...state.compileLog].slice(0, 20),
        }))
      }
    },
  }
}
