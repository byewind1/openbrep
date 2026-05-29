import type { AddParameterRequest } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createParameterActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async setDraftParameter(name: string, value: unknown) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      const preview = await api.fetchPreview(draftParameters)
      set({ preview, warnings: preview.warnings ?? [] })
      if (get().activeRailPanel === '2d') {
        await get().loadPreview2D()
      }
    },

    async addProjectParameter(parameter: AddParameterRequest) {
      set({ applying: true, lastError: null })
      const result = await api.addProjectParameter(parameter)
      if (!result.ok) {
        set({ applying: false, lastError: result.error ?? 'Failed to add parameter.' })
        return false
      }
      set({
        project: result.project,
        parameters: result.parameters,
        parameterIssues: [],
        preview: result.preview,
        warnings: result.warnings,
        draftParameters: {},
        applying: false,
      })
      await get().refreshProjectWorkspace({
        preferredScriptName: 'paramlist.xml',
        refreshAllScripts: true,
        refreshPreview: false,
        runDiagnostics: true,
      })
      return true
    },

    async validateProjectParameters() {
      const result = await api.validateProjectParameters()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to validate parameters.' })
        return
      }
      set({ parameterIssues: result.issues })
    },

    async applyDraftParameters() {
      const draft = get().draftParameters
      if (Object.keys(draft).length === 0) return
      set({ applying: true, lastError: null })
      const result = await api.applyParameters(draft)
      if (!result.ok) {
        set({
          applying: false,
          lastError: result.error ?? 'Failed to apply parameters.',
        })
        return
      }
      set({
        project: result.project,
        parameters: result.parameters,
        parameterIssues: [],
        preview: result.preview,
        warnings: result.warnings,
        draftParameters: {},
        applying: false,
      })
      await get().refreshProjectWorkspace({
        refreshAllScripts: true,
        refreshPreview: false,
        runDiagnostics: true,
      })
    },

    resetDraftParameters() {
      set({ draftParameters: {} })
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }
}
