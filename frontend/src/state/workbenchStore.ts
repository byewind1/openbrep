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
  getProjectScript,
  loadProjectPath,
  listProjectScripts,
  mockCompile,
  saveProjectScript,
  updateCompilerSettings,
} from '../api/client'
import type {
  ApplyResult,
  AssistantMessage,
  AssistantResult,
  CompileResult,
  CompileIssue,
  CompilerSettings,
  CompilerSettingsResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  MockCompileResponse,
  PreviewPayload,
  ProjectScript,
  ProjectScriptContentResponse,
  ProjectScriptsResponse,
  SaveScriptResponse,
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
  listProjectScripts: () => Promise<ProjectScriptsResponse>
  getProjectScript: (scriptName: string) => Promise<ProjectScriptContentResponse | null>
  saveProjectScript: (scriptName: string, content: string) => Promise<SaveScriptResponse>
  mockCompile: () => Promise<MockCompileResponse>
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
  scripts: ProjectScript[]
  activeScriptName: string | null
  scriptContents: Record<string, string>
  dirtyScripts: Record<string, boolean>
  scriptLoading: boolean
  scriptSaving: boolean
  mockCompileResult: MockCompileResponse | null
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
  loadScripts: () => Promise<void>
  openScript: (name: string) => Promise<void>
  updateActiveScriptContent: (content: string) => void
  saveActiveScript: () => Promise<void>
  runMockCompile: () => Promise<void>
  hasDraftChanges: () => boolean
}

const defaultWorkbenchApi: WorkbenchApi = {
  fetchSnapshot,
  fetchPreview,
  loadProjectPath,
  chooseProjectDirectory,
  chooseCompilerFile,
  compileProject,
  listProjectScripts,
  getProjectScript,
  saveProjectScript,
  mockCompile,
  updateCompilerSettings,
  askAssistant,
  generateWithAssistant,
  applyParameters,
}

const SCRIPT_FALLBACK_ORDER = ['3d.gdl', '2d.gdl', '1d.gdl', 'vl.gdl', 'pr.gdl', 'ui.gdl', 'paramlist.xml', 'libpartdata.xml']

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
    scripts: [],
    activeScriptName: null,
    scriptContents: {},
    dirtyScripts: {},
    scriptLoading: false,
    scriptSaving: false,
    mockCompileResult: null,

    async load() {
      set({ loading: true })
      const snapshot = await api.fetchSnapshot()
      set(hydrateSnapshot(snapshot, get().compilerSettings))
      await get().loadScripts()
      set({ loading: false })
    },

    async loadProjectPath(path) {
      const normalizedPath = path.trim()
      if (!normalizedPath) return
      set({ loading: true })
      const snapshot = await api.loadProjectPath(normalizedPath)
      set(hydrateSnapshot(snapshot, get().compilerSettings))
      await get().loadScripts()
      set({ loading: false })
    },

    async browseProjectDirectory() {
      set({ loading: true })
      const result = await api.chooseProjectDirectory()
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({ loading: false })
        return
      }
      set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings))
      await get().loadScripts()
      set({ loading: false })
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

    async openScript(name) {
      const target = name.trim()
      if (!target) return
      const cached = get().scriptContents[target]
      if (typeof cached === 'string') {
        set({ activeScriptName: target })
        return
      }
      set({ scriptLoading: true, activeScriptName: target })
      const result = await api.getProjectScript(target)
      set((state) => ({
        scriptLoading: false,
        activeScriptName: target,
        scriptContents: result ? { ...state.scriptContents, [target]: result.content } : state.scriptContents,
      }))
    },

    updateActiveScriptContent(content) {
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
      set({ scriptSaving: true })
      const result = await api.saveProjectScript(activeScriptName, content)
      if (result.success) {
        set((state) => ({
          scriptSaving: false,
          dirtyScripts: { ...state.dirtyScripts, [activeScriptName]: false },
          compileLog: [`Saved ${activeScriptName} at ${result.saved_at}`, ...state.compileLog].slice(0, 20),
        }))
        await get().loadScripts()
        return
      }
      set({ scriptSaving: false })
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

function hydrateSnapshot(snapshot: WorkbenchSnapshot, fallbackCompiler: CompilerSettings) {
  return {
    project: snapshot.project,
    parameters: snapshot.parameters,
    preview: snapshot.preview,
    warnings: snapshot.warnings ?? snapshot.preview?.warnings ?? [],
    compilerSettings: snapshot.compiler ?? fallbackCompiler,
    draftParameters: {},
    scripts: [],
    activeScriptName: null,
    scriptContents: {},
    dirtyScripts: {},
    mockCompileResult: null,
  }
}

function selectPreferredScript(scripts: ProjectScript[], current: string | null) {
  if (current && scripts.some((script) => script.name === current && script.exists)) return current
  for (const preferred of SCRIPT_FALLBACK_ORDER) {
    if (scripts.some((script) => script.name === preferred && script.exists)) return preferred
  }
  return scripts.find((script) => script.exists)?.name ?? null
}

function pruneDirtyScripts(dirtyScripts: Record<string, boolean>, scripts: ProjectScript[]) {
  const allowed = new Set(scripts.map((script) => script.name))
  return Object.fromEntries(Object.entries(dirtyScripts).filter(([name]) => allowed.has(name)))
}

function buildMockCompileSummary(result: MockCompileResponse) {
  if (!result.success && result.error) return `Mock compile failed: ${result.error}`
  const errors = countIssues(result.issues, 'error')
  const warnings = countIssues(result.issues, 'warning')
  if (errors === 0 && warnings === 0) return `Mock compile passed in ${result.duration_ms} ms`
  return `Mock compile finished in ${result.duration_ms} ms (${errors} errors, ${warnings} warnings)`
}

function countIssues(issues: CompileIssue[], severity: string) {
  return issues.filter((issue) => issue.severity === severity).length
}
