import type {
  AddParameterRequest,
  AddParameterResult,
  ApplyResult,
  AssistantCodeBlocksResult,
  AssistantHistoryResult,
  AssistantMessage,
  AssistantResult,
  ClearProjectMemoryResult,
  CompileResult,
  CompilerSettings,
  CompilerSettingsResult,
  CreateProjectResult,
  DeleteMemoryLessonResult,
  DirectoryChoiceResult,
  DeleteParameterResult,
  ErrorLesson,
  FileChoiceResult,
  GenerateResult,
  HsfExportResult,
  IgnoreMemoryLessonResult,
  LlmSettings,
  LlmSettingsResult,
  MockCompileResponse,
  Preview2DPayload,
  PreviewPayload,
  ProjectRevision,
  ProjectRevisionsResponse,
  ProjectLessonsResult,
  ProjectMemoryStatus,
  ProjectMemoryStatusResult,
  ProjectScript,
  ProjectScriptContentResponse,
  ProjectScriptsResponse,
  RecentProject,
  RecentProjectsResponse,
  RevealArtifactResult,
  RestoreRevisionResponse,
  RuntimeSettingsResult,
  SaveAssistantHistoryResult,
  SaveRevisionResponse,
  SaveScriptResponse,
  SummarizeMemoryResult,
  UpdateMemoryLessonRequest,
  UpdateMemoryLessonResult,
  UpdateParameterRequest,
  UpdateParameterResult,
  ValidateParametersResult,
  WorkbenchParameter,
  WorkbenchProject,
  WorkbenchSnapshot,
} from '../api/types'

export interface WorkbenchApi {
  fetchSnapshot: () => Promise<WorkbenchSnapshot>
  fetchPreview: (parameters: Record<string, unknown>) => Promise<PreviewPayload>
  fetchPreview2D: (parameters: Record<string, unknown>) => Promise<Preview2DPayload>
  loadProjectPath: (path: string) => Promise<WorkbenchSnapshot>
  importGdlFile: (path?: string) => Promise<WorkbenchSnapshot>
  importGsmFile: (path?: string) => Promise<WorkbenchSnapshot>
  exportHsfProject: (parentDir?: string, name?: string) => Promise<HsfExportResult>
  closeProject: () => Promise<WorkbenchSnapshot>
  chooseProjectDirectory: () => Promise<DirectoryChoiceResult>
  chooseCompilerFile: () => Promise<FileChoiceResult>
  chooseOutputDirectory: () => Promise<DirectoryChoiceResult>
  compileProject: (outputDir?: string) => Promise<CompileResult>
  createProjectFromPrompt: (message: string, assistantSettings?: string) => Promise<CreateProjectResult>
  listProjectScripts: () => Promise<ProjectScriptsResponse>
  listRecentProjects: () => Promise<RecentProjectsResponse>
  listProjectRevisions: () => Promise<ProjectRevisionsResponse>
  getProjectScript: (scriptName: string) => Promise<ProjectScriptContentResponse | null>
  saveProjectScript: (scriptName: string, content: string) => Promise<SaveScriptResponse>
  saveProjectRevision: (message?: string) => Promise<SaveRevisionResponse>
  restoreProjectRevision: (revisionId: string) => Promise<RestoreRevisionResponse>
  mockCompile: (outputDir?: string) => Promise<MockCompileResponse>
  revealArtifact: (path?: string) => Promise<RevealArtifactResult>
  updateCompilerSettings: (settings: CompilerSettings) => Promise<CompilerSettingsResult>
  fetchRuntimeSettings: () => Promise<RuntimeSettingsResult>
  updateLlmSettings: (settings: LlmSettings) => Promise<LlmSettingsResult>
  askAssistant: (message: string) => Promise<AssistantResult>
  listAssistantHistory: () => Promise<AssistantHistoryResult>
  saveAssistantHistory: (messages: AssistantMessage[]) => Promise<SaveAssistantHistoryResult>
  clearAssistantHistory: () => Promise<SaveAssistantHistoryResult>
  extractAssistantCodeBlocks: (content: string) => Promise<AssistantCodeBlocksResult>
  fetchMemoryStatus: () => Promise<ProjectMemoryStatusResult>
  fetchMemoryLessons: () => Promise<ProjectLessonsResult>
  summarizeProjectMemory: () => Promise<SummarizeMemoryResult>
  deleteMemoryLesson: (fingerprint: string) => Promise<DeleteMemoryLessonResult>
  ignoreMemoryLesson: (fingerprint: string) => Promise<IgnoreMemoryLessonResult>
  updateMemoryLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => Promise<UpdateMemoryLessonResult>
  clearProjectMemory: () => Promise<ClearProjectMemoryResult>
  generateWithAssistant: (message: string, assistantSettings?: string) => Promise<GenerateResult>
  applyParameters: (parameters: Record<string, unknown>) => Promise<ApplyResult>
  addProjectParameter: (parameter: AddParameterRequest) => Promise<AddParameterResult>
  updateProjectParameter: (parameter: UpdateParameterRequest) => Promise<UpdateParameterResult>
  deleteProjectParameter: (name: string) => Promise<DeleteParameterResult>
  validateProjectParameters: () => Promise<ValidateParametersResult>
}

export interface WorkbenchState {
  project: WorkbenchProject | null
  parameters: WorkbenchParameter[]
  parameterIssues: string[]
  draftParameters: Record<string, unknown>
  preview: PreviewPayload | null
  preview2d: Preview2DPayload | null
  warnings: string[]
  loading: boolean
  applying: boolean
  compiling: boolean
  lastError: string | null
  compileLog: string[]
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  activeRailPanel: '3d' | '2d' | 'ai'
  assistantBusy: boolean
  assistantMessages: AssistantMessage[]
  scripts: ProjectScript[]
  recentProjects: RecentProject[]
  revisions: ProjectRevision[]
  memoryStatus: ProjectMemoryStatus | null
  memoryLessons: ErrorLesson[]
  memorySkillPreview: string
  memoryBusy: boolean
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
  importGsmFile: (path?: string) => Promise<void>
  exportHsfProject: (parentDir?: string, name?: string) => Promise<void>
  closeProject: () => Promise<void>
  browseProjectDirectory: () => Promise<void>
  browseCompilerFile: () => Promise<void>
  browseOutputDirectory: () => Promise<void>
  setCompilerSettings: (settings: CompilerSettings) => Promise<void>
  setLlmSettings: (settings: LlmSettings) => Promise<void>
  reloadRuntimeSettings: () => Promise<void>
  compileCurrentProject: () => Promise<void>
  setActiveRailPanel: (panel: '3d' | '2d' | 'ai') => void
  loadAssistantHistory: () => Promise<void>
  clearAssistantHistory: () => Promise<void>
  adoptAssistantMessageCode: (index: number) => Promise<void>
  sendAssistantMessage: (message: string) => Promise<void>
  createProjectFromPrompt: (message: string) => Promise<void>
  generateAssistantChanges: (message: string) => Promise<void>
  setDraftParameter: (name: string, value: unknown) => Promise<void>
  addProjectParameter: (parameter: AddParameterRequest) => Promise<boolean>
  updateProjectParameter: (parameter: UpdateParameterRequest) => Promise<boolean>
  deleteProjectParameter: (name: string) => Promise<boolean>
  validateProjectParameters: () => Promise<void>
  applyDraftParameters: () => Promise<void>
  resetDraftParameters: () => void
  refreshProjectWorkspace: (options?: ProjectWorkspaceRefreshOptions) => Promise<void>
  loadScripts: () => Promise<void>
  loadRecentProjects: () => Promise<void>
  loadRevisions: () => Promise<void>
  loadMemoryStatus: () => Promise<void>
  loadMemoryLessons: () => Promise<void>
  summarizeProjectMemory: () => Promise<void>
  deleteMemoryLesson: (fingerprint: string) => Promise<void>
  ignoreMemoryLesson: (fingerprint: string) => Promise<void>
  updateMemoryLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => Promise<void>
  clearProjectMemory: () => Promise<void>
  saveRevision: (message?: string) => Promise<void>
  restoreRevision: (revisionId: string) => Promise<void>
  openScript: (name: string) => Promise<void>
  updateActiveScriptContent: (content: string) => void
  saveActiveScript: () => Promise<void>
  runMockCompile: () => Promise<void>
  revealCompileOutput: (path?: string) => Promise<void>
  loadPreview2D: () => Promise<void>
  clearLastError: () => void
  hasDraftChanges: () => boolean
}

export interface ProjectWorkspaceRefreshOptions {
  preferredScriptName?: string
  refreshAllScripts?: boolean
  refreshPreview?: boolean
  runDiagnostics?: boolean
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
