import { createStore } from 'zustand/vanilla'
import {
  applyParameters,
  askAssistant,
  chooseCompilerFile,
  chooseProjectDirectory,
  compileProject,
  fetchPreview,
  fetchSnapshot,
  generateWithAssistant,
  loadProjectPath,
  updateCompilerSettings,
} from '../api/client'
import type {
  ApplyResult,
  AssistantMessage,
  AssistantResult,
  CompileResult,
  CompilerSettings,
  CompilerSettingsResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  PreviewPayload,
  WorkbenchParameter,
  WorkbenchProject,
  WorkbenchSnapshot,
} from '../api/types'

export interface WorkbenchApi {
  fetchSnapshot: () => Promise<WorkbenchSnapshot>
  fetchPreview: (parameters: Record<string, unknown>) => Promise<PreviewPayload>
  loadProjectPath: (path: string) => Promise<WorkbenchSnapshot>
  chooseProjectDirectory: () => Promise<DirectoryChoiceResult>
  chooseCompilerFile: () => Promise<FileChoiceResult>
  compileProject: () => Promise<CompileResult>
  updateCompilerSettings: (settings: CompilerSettings) => Promise<CompilerSettingsResult>
  askAssistant: (message: string) => Promise<AssistantResult>
  generateWithAssistant: (message: string) => Promise<GenerateResult>
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
  compiling: boolean
  compileLog: string[]
  compilerSettings: CompilerSettings
  assistantBusy: boolean
  assistantMessages: AssistantMessage[]
  load: () => Promise<void>
  loadProjectPath: (path: string) => Promise<void>
  browseProjectDirectory: () => Promise<void>
  browseCompilerFile: () => Promise<void>
  setCompilerSettings: (settings: CompilerSettings) => Promise<void>
  compileCurrentProject: () => Promise<void>
  sendAssistantMessage: (message: string) => Promise<void>
  generateAssistantChanges: (message: string) => Promise<void>
  setDraftParameter: (name: string, value: unknown) => Promise<void>
  applyDraftParameters: () => Promise<void>
  hasDraftChanges: () => boolean
}

const defaultWorkbenchApi: WorkbenchApi = {
  fetchSnapshot,
  fetchPreview,
  loadProjectPath,
  chooseProjectDirectory,
  chooseCompilerFile,
  compileProject,
  updateCompilerSettings,
  askAssistant,
  generateWithAssistant,
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
    compiling: false,
    compileLog: [],
    compilerSettings: { mode: 'mock', converter_path: '' },
    assistantBusy: false,
    assistantMessages: [],

    async load() {
      set({ loading: true })
      const snapshot = await api.fetchSnapshot()
      set({
        project: snapshot.project,
        parameters: snapshot.parameters,
        preview: snapshot.preview,
        warnings: snapshot.warnings,
        compilerSettings: snapshot.compiler ?? get().compilerSettings,
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
        compilerSettings: snapshot.compiler ?? get().compilerSettings,
        draftParameters: {},
        loading: false,
      })
    },

    async browseProjectDirectory() {
      set({ loading: true })
      const result = await api.chooseProjectDirectory()
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({ loading: false })
        return
      }
      set({
        project: result.project,
        parameters: result.parameters,
        preview: result.preview,
        warnings: result.warnings ?? result.preview.warnings ?? [],
        compilerSettings: result.compiler ?? get().compilerSettings,
        draftParameters: {},
        loading: false,
      })
    },

    async setCompilerSettings(settings) {
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

    async compileCurrentProject() {
      set({ compiling: true })
      const result = await api.compileProject()
      const message =
        result.ok && result.compile
          ? `${result.compile.mode === 'mock' ? 'Mock' : 'LP'} compile passed: ${result.compile.output_path}`
          : `Compile failed: ${result.error ?? 'Unknown error'}`
      set((state) => ({
        compileLog: [message, ...state.compileLog].slice(0, 20),
        compiling: false,
      }))
    },

    async sendAssistantMessage(message) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.askAssistant(trimmed)
      const reply = result.ok && result.assistant ? result.assistant.reply : result.error ?? 'Assistant request failed.'
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
      }))
    },

    async generateAssistantChanges(message) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.generateWithAssistant(trimmed)
      const changedFiles = result.assistant?.changed_files ?? []
      const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
      const reply =
        result.ok && result.assistant
          ? `${result.assistant.reply}${suffix}`
          : result.error ?? 'Generation request failed.'
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
        preview: result.preview ?? state.preview,
        warnings: result.warnings ?? result.preview?.warnings ?? state.warnings,
        draftParameters: {},
      }))
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }))
}

export const workbenchStore = createWorkbenchStore()
