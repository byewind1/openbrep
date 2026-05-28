import type {
  ApplyResult,
  AssistantMessage,
  AssistantResult,
  CompileResult,
  CompilerSettings,
  CompilerSettingsResult,
  CreateProjectResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  LlmSettings,
  LlmSettingsResult,
  MockCompileResponse,
  PreviewPayload,
  ProjectRevision,
  ProjectRevisionsResponse,
  ProjectScript,
  ProjectScriptContentResponse,
  ProjectScriptsResponse,
  RecentProject,
  RecentProjectsResponse,
  RestoreRevisionResponse,
  RuntimeSettingsResult,
  SaveRevisionResponse,
  SaveScriptResponse,
  WorkbenchParameter,
  WorkbenchProject,
  WorkbenchSnapshot,
} from '../api/types'

export interface WorkbenchApi {
  fetchSnapshot: () => Promise<WorkbenchSnapshot>
  fetchPreview: (parameters: Record<string, unknown>) => Promise<PreviewPayload>
  loadProjectPath: (path: string) => Promise<WorkbenchSnapshot>
  importGdlFile: (path?: string) => Promise<WorkbenchSnapshot>
  closeProject: () => Promise<WorkbenchSnapshot>
  chooseProjectDirectory: () => Promise<DirectoryChoiceResult>
  chooseCompilerFile: () => Promise<FileChoiceResult>
  compileProject: () => Promise<CompileResult>
  createProjectFromPrompt: (message: string, assistantSettings?: string) => Promise<CreateProjectResult>
  listProjectScripts: () => Promise<ProjectScriptsResponse>
  listRecentProjects: () => Promise<RecentProjectsResponse>
  listProjectRevisions: () => Promise<ProjectRevisionsResponse>
  getProjectScript: (scriptName: string) => Promise<ProjectScriptContentResponse | null>
  saveProjectScript: (scriptName: string, content: string) => Promise<SaveScriptResponse>
  saveProjectRevision: (message?: string) => Promise<SaveRevisionResponse>
  restoreProjectRevision: (revisionId: string) => Promise<RestoreRevisionResponse>
  mockCompile: () => Promise<MockCompileResponse>
  updateCompilerSettings: (settings: CompilerSettings) => Promise<CompilerSettingsResult>
  fetchRuntimeSettings: () => Promise<RuntimeSettingsResult>
  updateLlmSettings: (settings: LlmSettings) => Promise<LlmSettingsResult>
  askAssistant: (message: string) => Promise<AssistantResult>
  generateWithAssistant: (message: string, assistantSettings?: string) => Promise<GenerateResult>
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
  lastError: string | null
  compileLog: string[]
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  activeRailPanel: '3d' | 'ai'
  assistantBusy: boolean
  assistantMessages: AssistantMessage[]
  scripts: ProjectScript[]
  recentProjects: RecentProject[]
  revisions: ProjectRevision[]
  latestRevisionId: string | null
  revisionLoading: boolean
  activeScriptName: string | null
  scriptContents: Record<string, string>
  dirtyScripts: Record<string, boolean>
  scriptLoading: boolean
  scriptSaving: boolean
  mockCompileResult: MockCompileResponse | null
  load: () => Promise<void>
  loadProjectPath: (path: string) => Promise<void>
  importGdlFile: (path?: string) => Promise<void>
  closeProject: () => Promise<void>
  browseProjectDirectory: () => Promise<void>
  browseCompilerFile: () => Promise<void>
  setCompilerSettings: (settings: CompilerSettings) => Promise<void>
  setLlmSettings: (settings: LlmSettings) => Promise<void>
  reloadRuntimeSettings: () => Promise<void>
  compileCurrentProject: () => Promise<void>
  setActiveRailPanel: (panel: '3d' | 'ai') => void
  sendAssistantMessage: (message: string) => Promise<void>
  createProjectFromPrompt: (message: string) => Promise<void>
  generateAssistantChanges: (message: string) => Promise<void>
  setDraftParameter: (name: string, value: unknown) => Promise<void>
  applyDraftParameters: () => Promise<void>
  loadScripts: () => Promise<void>
  loadRecentProjects: () => Promise<void>
  loadRevisions: () => Promise<void>
  saveRevision: (message?: string) => Promise<void>
  restoreRevision: (revisionId: string) => Promise<void>
  openScript: (name: string) => Promise<void>
  updateActiveScriptContent: (content: string) => void
  saveActiveScript: () => Promise<void>
  runMockCompile: () => Promise<void>
  clearLastError: () => void
  hasDraftChanges: () => boolean
}

export type WorkbenchSet = (
  partial: Partial<WorkbenchState> | ((state: WorkbenchState) => Partial<WorkbenchState>),
) => void

export type WorkbenchGet = () => WorkbenchState

export interface WorkbenchActionContext {
  api: WorkbenchApi
  set: WorkbenchSet
  get: WorkbenchGet
}
