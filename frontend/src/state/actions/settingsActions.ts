import type { CompilerSettings } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createSettingsActions({ api, set, get }: WorkbenchActionContext) {
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
        return result.compiler
      }
      const error = result.error ?? 'Compiler settings were not saved.'
      set({ lastError: error })
      throw new Error(error)
    },

    async browseCompilerFile() {
      const result = await api.chooseCompilerFile()
      if (result.ok && result.compiler) {
        return result.compiler
      }
      return null
    },

    async browseOutputDirectory() {
      const result = await api.chooseOutputDirectory()
      if (result.ok && result.compiler) {
        return result.compiler
      }
      return null
    },

    async openConfig() {
      await api.openConfig()
    },

    async testLlmConnection() {
      return api.testLlmConnection()
    },

    async switchLlmModel(model: string) {
      const result = await api.updateLlmModel(model)
      if (result.ok && result.llm) {
        set({ llmSettings: result.llm })
        return
      }
      // 抛给调用方（设置面板内联展示），同时写 lastError 供顶栏兜底
      const error = result.error ?? 'Model switch failed.'
      set({ lastError: error })
      throw new Error(error)
    },

    async saveLlmApiKey(model: string, apiKey: string) {
      const result = await api.updateLlmApiKey(model, apiKey)
      if (result.ok && result.llm) {
        set({ llmSettings: result.llm, lastError: null })
        return result.llm
      }
      const error = result.error ?? 'API key was not saved.'
      set({ lastError: error })
      throw new Error(error)
    },

    async reloadRuntimeSettings() {
      const result = await api.fetchRuntimeSettings()
      set((state) => ({
        compilerSettings: result.compiler ?? state.compilerSettings,
        llmSettings: result.llm ?? state.llmSettings,
      }))
    },

    async pollConfigRevision() {
      const revision = await api.fetchConfigRevision()
      if (!revision.ok || !revision.revision) return
      const previous = get().configRevision
      if (previous === null) {
        set({ configRevision: revision.revision })
        return
      }
      if (revision.revision === previous) return
      // config.toml was edited outside the workbench (e.g. adding a model/provider
      // in a text editor). Re-read settings, but only apply the LLM slice so a
      // background refresh never clobbers an unsaved compiler draft in the drawer.
      const result = await api.fetchRuntimeSettings()
      set((state) => ({
        configRevision: revision.revision,
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
