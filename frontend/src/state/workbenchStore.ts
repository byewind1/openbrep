import { createStore } from 'zustand/vanilla'
import { applyParameters, fetchPreview, fetchSnapshot, loadProjectPath } from '../api/client'
import type { ApplyResult, PreviewPayload, WorkbenchParameter, WorkbenchProject, WorkbenchSnapshot } from '../api/types'

export interface WorkbenchApi {
  fetchSnapshot: () => Promise<WorkbenchSnapshot>
  fetchPreview: (parameters: Record<string, unknown>) => Promise<PreviewPayload>
  loadProjectPath: (path: string) => Promise<WorkbenchSnapshot>
  applyParameters: (parameters: Record<string, unknown>) => Promise<ApplyResult>
}

export interface WorkbenchState {
  project: WorkbenchProject | null
  parameters: WorkbenchParameter[]
  draftParameters: Record<string, unknown>
  preview: PreviewPayload | null
  warnings: string[]
  loading: boolean
  applying: boolean
  load: () => Promise<void>
  loadProjectPath: (path: string) => Promise<void>
  setDraftParameter: (name: string, value: unknown) => Promise<void>
  applyDraftParameters: () => Promise<void>
  hasDraftChanges: () => boolean
}

const defaultWorkbenchApi: WorkbenchApi = {
  fetchSnapshot,
  fetchPreview,
  loadProjectPath,
  applyParameters,
}

export function createWorkbenchStore(api: WorkbenchApi = defaultWorkbenchApi) {
  return createStore<WorkbenchState>((set, get) => ({
    project: null,
    parameters: [],
    draftParameters: {},
    preview: null,
    warnings: [],
    loading: false,
    applying: false,

    async load() {
      set({ loading: true })
      const snapshot = await api.fetchSnapshot()
      set({
        project: snapshot.project,
        parameters: snapshot.parameters,
        preview: snapshot.preview,
        warnings: snapshot.warnings,
        loading: false,
      })
    },

    async loadProjectPath(path) {
      const normalizedPath = path.trim()
      if (!normalizedPath) return
      set({ loading: true })
      const snapshot = await api.loadProjectPath(normalizedPath)
      set({
        project: snapshot.project,
        parameters: snapshot.parameters,
        preview: snapshot.preview,
        warnings: snapshot.warnings,
        draftParameters: {},
        loading: false,
      })
    },

    async setDraftParameter(name, value) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      const preview = await api.fetchPreview(draftParameters)
      set({ preview, warnings: preview.warnings ?? [] })
    },

    async applyDraftParameters() {
      const draft = get().draftParameters
      if (Object.keys(draft).length === 0) return
      set({ applying: true })
      const result = await api.applyParameters(draft)
      set({
        project: result.project,
        parameters: result.parameters,
        preview: result.preview,
        warnings: result.warnings,
        draftParameters: {},
        applying: false,
      })
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }))
}

export const workbenchStore = createWorkbenchStore()
