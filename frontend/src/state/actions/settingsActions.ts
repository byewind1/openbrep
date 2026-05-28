import type { CompilerSettings, LlmSettings } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createSettingsActions({ api, set }: WorkbenchActionContext) {
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

    async setLlmSettings(settings: LlmSettings) {
      const result = await api.updateLlmSettings(settings)
      if (result.ok && result.llm) {
        set({ llmSettings: result.llm })
      }
    },

    async reloadRuntimeSettings() {
      const result = await api.fetchRuntimeSettings()
      set((state) => ({
        compilerSettings: result.compiler ?? state.compilerSettings,
        llmSettings: result.llm ?? state.llmSettings,
      }))
    },
  }
}
