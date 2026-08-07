import type {
  AddParameterRequest,
  AddParameterResult,
  ApplyResult,
  AssistantCodeBlocksResult,
  AssistantHistoryResult,
  AssistantImageAttachment,
  AssistantMessage,
  AssistantResult,
  ClearProjectMemoryResult,
  CompileResult,
  CompilerSettings,
  CompilerSettingsResult,
  ConfigRevisionResult,
  CreateProjectResult,


  DeleteMemoryLessonResult,
  DirectoryChoiceResult,
  DeleteParameterResult,
  ErrorLesson,
  FileChoiceResult,
  GenerateResult,
  HsfExportResult,
  IgnoreMemoryLessonResult,
  KnowledgeStatus,
  LlmSettings,
  LlmConnectionTestResult,
  LlmSettingsResult,
  MockCompileResponse,
  Preview2DPayload,
  PreviewPayload,
  ProjectRevision,
  ProjectRevisionsResponse,
  ProjectLessonsResult,
  ProjectGitResponse,
  ProjectGitStatus,
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
  TapirActionResult,
  TapirStatus,
  TapirStatusResult,
  UpdateMemoryLessonRequest,
  UpdateMemoryLessonResult,
  UpdateParameterRequest,
  UpdateParameterResult,
  ValidateParametersResult,
  WorkbenchParameter,
  WorkbenchProject,
  WorkbenchSnapshot,
  WorkspaceInfo,
  WorkspaceScanResult,
  WorkspaceSearchHit,
  WorkspaceSearchResult,
  WorkspaceTrashResult,
} from '../api/types'

export interface WorkbenchApi {
  fetchSnapshot: () => Promise<WorkbenchSnapshot>
  workspaceInit: (path: string) => Promise<WorkspaceScanResult>
  workspaceOpen: (path: string) => Promise<WorkspaceScanResult>
  workspaceClose: () => Promise<{ ok: boolean; error?: string; workspace: null }>
  workspaceScan: () => Promise<WorkspaceScanResult>
  workspaceSearch: (query: string) => Promise<WorkspaceSearchResult>
  trashWorkspaceProject: (path: string) => Promise<WorkspaceTrashResult>
  fetchPreview: (parameters: Record<string, unknown>, scripts?: Record<string, string>) => Promise<PreviewPayload>
  fetchPreview2D: (parameters: Record<string, unknown>, scripts?: Record<string, string>) => Promise<Preview2DPayload>
  loadProjectPath: (path: string) => Promise<WorkbenchSnapshot>
  newProject: () => Promise<WorkbenchSnapshot>
  importGdlFile: (path?: string) => Promise<WorkbenchSnapshot>
  importGsmFile: (path?: string) => Promise<WorkbenchSnapshot>
  importBlenderScript: (path?: string) => Promise<WorkbenchSnapshot>
  exportHsfProject: (parentDir?: string, name?: string) => Promise<HsfExportResult>
  saveProject: () => Promise<HsfExportResult>
  closeProject: () => Promise<WorkbenchSnapshot>
  chooseProjectDirectory: () => Promise<DirectoryChoiceResult>
  chooseCompilerFile: () => Promise<FileChoiceResult>
  chooseOutputDirectory: () => Promise<DirectoryChoiceResult>
  compileProject: (outputDir?: string) => Promise<CompileResult>
  createProjectFromPrompt: (
    message: string,
    assistantSettings?: string,
    image?: AssistantImageAttachment | null,
    signal?: AbortSignal,
  ) => Promise<CreateProjectResult>
  listProjectScripts: () => Promise<ProjectScriptsResponse>
  listRecentProjects: () => Promise<RecentProjectsResponse>
  listProjectRevisions: () => Promise<ProjectRevisionsResponse>
  getProjectScript: (scriptName: string) => Promise<ProjectScriptContentResponse | null>
  saveProjectScript: (scriptName: string, content: string) => Promise<SaveScriptResponse>
  saveProjectRevision: (message?: string) => Promise<SaveRevisionResponse>
  restoreProjectRevision: (revisionId: string) => Promise<RestoreRevisionResponse>
  fetchProjectGitStatus: () => Promise<ProjectGitResponse>
  initializeProjectGit: () => Promise<ProjectGitResponse>
  updateProjectGitSettings: (enabled: boolean) => Promise<ProjectGitResponse>
  commitProjectGit: (message?: string) => Promise<ProjectGitResponse>
  mockCompile: (outputDir?: string) => Promise<MockCompileResponse>
  revealArtifact: (path?: string) => Promise<RevealArtifactResult>
  updateCompilerSettings: (settings: CompilerSettings) => Promise<CompilerSettingsResult>
  fetchRuntimeSettings: () => Promise<RuntimeSettingsResult>
  fetchConfigRevision: () => Promise<ConfigRevisionResult>
  openConfig: () => Promise<{ ok: boolean; error?: string }>
  testLlmConnection: () => Promise<LlmConnectionTestResult>
  updateLlmModel: (model: string) => Promise<LlmSettingsResult>
  updateLlmApiKey: (model: string, apiKey: string) => Promise<LlmSettingsResult>
  fetchTapirStatus: () => Promise<TapirStatusResult>
  reloadTapirLibraries: () => Promise<TapirActionResult>
  syncTapirSelection: () => Promise<TapirActionResult>
  highlightTapirSelection: () => Promise<TapirActionResult>
  loadTapirParameters: () => Promise<TapirActionResult>
  applyTapirParameterEdits: (paramEdits?: Record<string, unknown>) => Promise<TapirActionResult>
  askAssistant: (message: string, signal?: AbortSignal) => Promise<AssistantResult>
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
  fetchKnowledgeStatus: () => Promise<KnowledgeStatus>
  reloadKnowledge: () => Promise<KnowledgeStatus>
  generateWithAssistant: (
    message: string,
    assistantSettings?: string,
    image?: AssistantImageAttachment | null,
    signal?: AbortSignal,
  ) => Promise<GenerateResult>
  generateWithAssistantStream: (
    message: string,
    assistantSettings?: string,
    image?: AssistantImageAttachment | null,
    onEvent?: (event: import('../api/types').AssistantStreamEvent) => void,
    signal?: AbortSignal,
  ) => Promise<GenerateResult>
  requestModifyPlan: (
    message: string,
    assistantSettings?: string,
    image?: AssistantImageAttachment | null,
    signal?: AbortSignal,
  ) => Promise<GenerateResult>
  confirmModifyPlan: (
    approve: boolean,
    stream?: boolean,
    onEvent?: (event: import('../api/types').AssistantStreamEvent) => void,
    signal?: AbortSignal,
  ) => Promise<GenerateResult>
  confirmSkillProposal: (approve: boolean, signal?: AbortSignal) => Promise<import('../api/types').SkillProposalConfirmResult>
  applyParameters: (parameters: Record<string, unknown>) => Promise<ApplyResult>
  addProjectParameter: (parameter: AddParameterRequest) => Promise<AddParameterResult>
  updateProjectParameter: (parameter: UpdateParameterRequest) => Promise<UpdateParameterResult>
  deleteProjectParameter: (name: string) => Promise<DeleteParameterResult>
  validateProjectParameters: () => Promise<ValidateParametersResult>
}

export type BackendErrorKind = 'down' | 'starting' | 'timeout'

export interface WorkbenchState {
  // sessionId 标识 backend 进程；projectEpoch 在每次换项目时变化，
  // 长操作（AI 生成/创建）用它丢弃跨项目的过期结果。
  sessionId: string | null
  projectEpoch: number
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
  /** 后端健康看门狗：非 null 表示后端处于故障态（详见 state/backendHealth.ts） */
  backendError: { kind: BackendErrorKind; at: number } | null
  /** 恢复后的一次性提示（"✅ 已恢复连接"），数秒后自动清除 */
  backendNotice: string | null
  compileLog: string[]
  compilerSettings: CompilerSettings
  llmSettings: LlmSettings
  configRevision: string | null
  chatAbortController: AbortController | null
  interruptedContext: { message: string; intent: string } | null
  activeRailPanel: '3d' | '2d' | 'inspect' | 'ai'
  assistantBusy: boolean
  assistantMessages: AssistantMessage[]
  /** 计划确认门（V3）：非 null = 有待用户确认的修改计划 */
  pendingPlan: import('../api/types').PendingPlan | null
  /** 模式级 skill 提案（P2-d）：非 null = 有待用户确认的 skill 提案 */
  pendingSkillProposal: import('../api/types').SkillProposal | null
  scripts: ProjectScript[]
  recentProjects: RecentProject[]
  revisions: ProjectRevision[]
  gitStatus: ProjectGitStatus | null
  gitBusy: boolean
  knowledgeStatus: KnowledgeStatus | null
  knowledgeBusy: boolean
  memoryStatus: ProjectMemoryStatus | null
  memoryLessons: ErrorLesson[]
  memorySkillPreview: string
  memoryBusy: boolean
  tapirStatus: TapirStatus | null
  tapirBusy: boolean
  latestRevisionId: string | null
  revisionLoading: boolean
  activeScriptName: string | null
  scriptContents: Record<string, string>
  dirtyScripts: Record<string, boolean>
  lastSavedAt: string | null
  scriptLoading: boolean
  scriptSaving: boolean
  mockCompileResult: MockCompileResponse | null
  workspace: WorkspaceInfo | null
  workspaceBusy: boolean
  /** open 失败且 code=not_a_workspace 时记录尝试路径，面板据此显示引导条（P3-d2b） */
  workspaceInitHint: string | null
  workspaceSearching: boolean
  workspaceSearchQuery: string | null
  workspaceSearchHits: WorkspaceSearchHit[]
  workspaceSearchResult: WorkspaceSearchResult | null
  load: () => Promise<void>
  loadProjectPath: (path: string) => Promise<void>
  newProject: () => Promise<void>
  importGdlFile: (path?: string) => Promise<void>
  importGsmFile: (path?: string) => Promise<void>
  importBlenderScript: (path?: string) => Promise<void>
  exportHsfProject: (parentDir?: string, name?: string) => Promise<void>
  saveProject: () => Promise<void>
  saveProjectAs: (parentDir?: string, name?: string) => Promise<void>
  closeProject: () => Promise<void>
  browseProjectDirectory: () => Promise<void>
  browseCompilerFile: () => Promise<CompilerSettings | null>
  browseOutputDirectory: () => Promise<CompilerSettings | null>
  setCompilerSettings: (settings: CompilerSettings) => Promise<CompilerSettings>
  openConfig: () => Promise<void>
  testLlmConnection: () => Promise<LlmConnectionTestResult>
  switchLlmModel: (model: string) => Promise<void>
  saveLlmApiKey: (model: string, apiKey: string) => Promise<LlmSettings>
  sendChat: (message: string, image?: AssistantImageAttachment | null) => Promise<void>
  stopChat: () => void
  confirmPendingPlan: (approve: boolean) => Promise<void>
  confirmPendingSkillProposal: (approve: boolean) => Promise<void>
  reloadRuntimeSettings: () => Promise<void>
  pollConfigRevision: () => Promise<void>
  refreshTapirStatus: () => Promise<void>
  reloadTapirLibraries: () => Promise<void>
  syncTapirSelection: () => Promise<void>
  highlightTapirSelection: () => Promise<void>
  loadTapirParameters: () => Promise<void>
  applyTapirParameters: () => Promise<void>
  compileCurrentProject: () => Promise<void>
  setActiveRailPanel: (panel: '3d' | '2d' | 'inspect' | 'ai') => void
  loadAssistantHistory: () => Promise<void>
  clearAssistantHistory: () => Promise<void>
  adoptAssistantMessageCode: (index: number) => Promise<void>
  sendAssistantMessage: (message: string) => Promise<void>
  createProjectFromPrompt: (message: string, image?: AssistantImageAttachment | null) => Promise<void>
  generateAssistantChanges: (message: string, image?: AssistantImageAttachment | null) => Promise<void>
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
  loadKnowledgeStatus: () => Promise<void>
  reloadKnowledge: () => Promise<void>
  loadMemoryStatus: () => Promise<void>
  loadMemoryLessons: () => Promise<void>
  summarizeProjectMemory: () => Promise<void>
  deleteMemoryLesson: (fingerprint: string) => Promise<void>
  ignoreMemoryLesson: (fingerprint: string) => Promise<void>
  updateMemoryLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => Promise<void>
  clearProjectMemory: () => Promise<void>
  saveRevision: (message?: string) => Promise<void>
  restoreRevision: (revisionId: string) => Promise<void>
  loadProjectGitStatus: () => Promise<void>
  initializeProjectGit: () => Promise<void>
  setProjectGitEnabled: (enabled: boolean) => Promise<void>
  commitProjectGit: (message?: string) => Promise<void>
  openScript: (name: string) => Promise<void>
  updateActiveScriptContent: (content: string) => void
  saveActiveScript: () => Promise<void>
  flushDirtyScripts: () => Promise<{ ok: boolean; didSave: boolean }>
  runMockCompile: () => Promise<void>
  revealCompileOutput: (path?: string) => Promise<void>
  openWorkspace: (path: string) => Promise<void>
  initWorkspace: (path: string) => Promise<void>
  closeWorkspace: () => Promise<void>
  refreshWorkspace: () => Promise<void>
  searchWorkspace: (query: string) => Promise<void>
  trashWorkspaceProject: (path: string) => Promise<void>
  browseWorkspaceDirectory: () => Promise<string | null>
  clearWorkspaceInitHint: () => void
  loadPreview3D: () => Promise<void>
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
