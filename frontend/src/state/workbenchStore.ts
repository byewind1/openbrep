import { createStore } from 'zustand/vanilla'
import { createBackendHealth } from './backendHealth'
import {
  addProjectParameter,
  applyParameters,
  askAssistant,
  chooseCompilerFile,
  chooseOutputDirectory,
  chooseProjectDirectory,
  clearAssistantHistory,
  clearProjectMemory,
  closeProject,
  compileProject,
  createProjectFromPrompt,
  deleteMemoryLesson,
  deleteProjectParameter,
  distillDistilledLessons,
  exportHsfProject,
  extractAssistantCodeBlocks,
  fetchConfigRevision,
  fetchDistilledLessons,
  fetchKnowledgeStatus,
  fetchMemoryLessons,
  fetchMemoryStatus,
  fetchProjectGitStatus,
  fetchPreview2D,
  fetchPreview,
  fetchEffectiveParameters,
  fetchAuthoritativePreview,
  fetchHostVerification,
  fetchRuntimeSettings,
  fetchTapirStatus,
  fetchSnapshot,
  generateWithAssistant,
  generateWithAssistantStream,
  requestModifyPlan,
  confirmModifyPlan,
  confirmSkillProposal,
  listSkillProposals,
  getProjectScript,
  initializeProjectGit,
  ignoreMemoryLesson,
  importGdlFile,
  importGsmFile,
  importBlenderScript,
  reloadKnowledge,
  reloadTapirLibraries,
  importAssistantHistory,
  distillAssistantHistory,
  listAssistantHistory,
  listProjectRevisions,
  listProjectScripts,
  listRecentProjects,
  loadProjectPath,
  mockCompile,
  newProject,
  revealArtifact,
  restoreProjectRevision,
  getProjectRevisionDiff,
  saveProjectRevision,
  saveProject,
  saveProjectScript,
  saveAssistantHistory,
  setDistilledLessonStatus,
  summarizeProjectMemory,
  syncTapirSelection,
  highlightTapirSelection,
  loadTapirParameters,
  applyTapirParameterEdits,
  updateCompilerSettings,
  updateLlmModel,
  updateLlmApiKey,
  updateSessionLlmModel,
  fetchCodexStatus,
  fetchCodexModels,
  openConfig,
  testLlmConnection,
  updateProjectGitSettings,
  updateMemoryLesson,
  updateProjectParameter,
  validateProjectParameters,
  commitProjectGit,
  workspaceInit,
  workspaceOpen,
  workspaceClose,
  workspaceScan,
  workspaceSearch,
  trashWorkspaceProject,
  runHostVerification,
} from '../api/client'
import { createAssistantActions } from './actions/assistantActions'
import { createCompileActions } from './actions/compileActions'
import { createKnowledgeActions } from './actions/knowledgeActions'
import { createMemoryActions } from './actions/memoryActions'
import { createParameterActions } from './actions/parameterActions'
import { createPreviewActions } from './actions/previewActions'
import { createProjectActions } from './actions/projectActions'
import { createRevisionActions } from './actions/revisionActions'
import { createScriptActions } from './actions/scriptActions'
import { createSettingsActions } from './actions/settingsActions'
import { createTapirActions } from './actions/tapirActions'
import { createWorkspaceActions } from './actions/workspaceActions'
import type { WorkbenchActionContext, WorkbenchApi, WorkbenchSet, WorkbenchState } from './workbenchStoreTypes'
import { defaultLlmSettings } from './workbenchStoreUtils'

export type { WorkbenchApi, WorkbenchState } from './workbenchStoreTypes'

const defaultWorkbenchApi: WorkbenchApi = {
  fetchSnapshot,
  fetchPreview,
  fetchEffectiveParameters,
  fetchAuthoritativePreview,
  fetchHostVerification,
  runHostVerification,
  listSkillProposals,
  workspaceInit,
  workspaceOpen,
  workspaceClose,
  workspaceScan,
  workspaceSearch,
  trashWorkspaceProject,
  fetchPreview2D,
  loadProjectPath,
  newProject,
  importGdlFile,
  importGsmFile,
  importBlenderScript,
  exportHsfProject,
  saveProject,
  closeProject,
  chooseProjectDirectory,
  chooseCompilerFile,
  chooseOutputDirectory,
  compileProject,
  createProjectFromPrompt,
  listProjectScripts,
  listRecentProjects,
  listProjectRevisions,
  getProjectScript,
  saveProjectScript,
  saveProjectRevision,
  restoreProjectRevision,
  getProjectRevisionDiff,
  fetchProjectGitStatus,
  initializeProjectGit,
  updateProjectGitSettings,
  commitProjectGit,
  mockCompile,
  revealArtifact,
  updateCompilerSettings,
  fetchRuntimeSettings,
  fetchConfigRevision,
  fetchTapirStatus,
  reloadTapirLibraries,
  syncTapirSelection,
  highlightTapirSelection,
  loadTapirParameters,
  applyTapirParameterEdits,
  openConfig,
  testLlmConnection,
  updateLlmModel,
  updateLlmApiKey,
  updateSessionLlmModel,
  fetchCodexStatus,
  fetchCodexModels,
  askAssistant,
  listAssistantHistory,
  saveAssistantHistory,
  clearAssistantHistory,
  importAssistantHistory,
  distillAssistantHistory,
  extractAssistantCodeBlocks,
  fetchKnowledgeStatus,
  reloadKnowledge,
  fetchMemoryStatus,
  fetchMemoryLessons,
  summarizeProjectMemory,
  deleteMemoryLesson,
  ignoreMemoryLesson,
  updateMemoryLesson,
  clearProjectMemory,
  fetchDistilledLessons,
  distillDistilledLessons,
  setDistilledLessonStatus,
  generateWithAssistant,
  generateWithAssistantStream,
  requestModifyPlan,
  confirmModifyPlan,
  confirmSkillProposal,
  applyParameters,
  addProjectParameter,
  updateProjectParameter,
  deleteProjectParameter,
  validateProjectParameters,
}

export function createWorkbenchStore(api: WorkbenchApi = defaultWorkbenchApi) {
  const store = createStore<WorkbenchState>((set, get) => {
    const context: WorkbenchActionContext = {
      api,
      get,
      set: set as WorkbenchSet,
    }
    // 后端健康看门狗：失败分型 + 3s 恢复轮询 + 成功自动清横幅
    createBackendHealth({ get, set: set as WorkbenchSet }).install()
    return {
      ...initialWorkbenchState(),
      ...createProjectActions(context),
      ...createSettingsActions(context),
      ...createTapirActions(context),
      ...createWorkspaceActions(context),
      ...createParameterActions(context),
      ...createPreviewActions(context),
      ...createCompileActions(context),
      ...createKnowledgeActions(context),
      ...createMemoryActions(context),
      ...createScriptActions(context),
      ...createRevisionActions(context),
      ...createAssistantActions(context),
      clearLastError() {
        set({ lastError: null })
      },
    }
  })
  installPreviewQualityReconciler(store)
  return store
}

/**
 * 预览质量对账：快照/加载响应里内嵌的 preview 可能与用户所选质量档不一致
 * （后端快照按固定档生成；旧后端 payload 无 quality 字段则跳过）。preview 对象
 * 身份变化时比对 payload 自描述的 quality，不一致即按当前档重取。
 * previewQuality 自身变化不在此处理——setPreviewQuality 已显式重取，
 * 这里再监听会造成双拉。
 */
function installPreviewQualityReconciler(store: {
  getState: () => WorkbenchState
  subscribe: (listener: (state: WorkbenchState, prev: WorkbenchState) => void) => void
}) {
  let syncing = false
  // 拉取失败（或后端未按请求档返回）时记下该 mismatch 对，避免无条件重试死循环；
  // 真正的状态变化（新 preview / 切档）会在 finally 的再审里重新武装
  let blockedKey: string | null = null
  function reconcile() {
    if (syncing) return
    const state = store.getState()
    const preview = state.preview
    if (!preview || preview.meshes.length === 0) return
    const payloadQuality = preview.quality
    const targetQuality = state.previewQuality
    if (!payloadQuality || payloadQuality === targetQuality) return
    const key = `${payloadQuality}->${targetQuality}`
    if (key === blockedKey) return
    syncing = true
    void state
      .loadPreview3D()
      .then(() => {
        const settled = store.getState()
        const settledQuality = settled.preview?.quality
        blockedKey = settledQuality && settledQuality !== settled.previewQuality ? key : null
      })
      .catch(() => {
        blockedKey = key
      })
      .finally(() => {
        syncing = false
        // 拉取期间 preview/质量档可能又变了：收尾再审一次，保证最终一致
        const current = store.getState()
        if (current.preview !== preview || current.previewQuality !== targetQuality) reconcile()
      })
  }
  store.subscribe((state, prev) => {
    if (state.preview !== prev.preview) reconcile()
  })
}

export const workbenchStore = createWorkbenchStore()

function initialWorkbenchState() {
  return {
    sessionId: null,
    projectEpoch: 0,
    project: null,
    parameters: [],
    parameterIssues: [],
    draftParameters: {},
    sourceFingerprint: null,
    effectiveParameters: {},
    effectiveParameterDiagnostics: [],
    effectiveParametersBusy: false,
    effectiveParametersError: null,
    preview: null,
    preview2d: null,
    previewQuality: 'accurate' as const,
    previewGhost: null,
    previewGhostLabel: null,
    previewSourceMode: 'local' as const,
    previewAuthoritative: null,
    previewAuthoritative2d: null,
    previewAuthoritativeLoading: false,
    previewAuthoritativeError: null,
    previewAuthoritativeParamsKey: null,
    hostVerification: null,
    hostVerificationLoading: false,
    hostVerificationError: null,
    hostVerificationParamsKey: null,
    warnings: [],
    loading: false,
    applying: false,
    compiling: false,
    lastError: null,
    backendError: null,
    backendNotice: null,
    compileLog: [],
    compilerSettings: { mode: 'mock' as const, converter_path: '', output_dir: '' },
    llmSettings: defaultLlmSettings(),
    codexCatalog: { connected: false, models: [], loaded: false },
    configRevision: null,
    chatAbortController: null,
    interruptedContext: null,
    pendingDeliveryContinue: null,
    activeRailPanel: 'ai' as const,
    assistantBusy: false,
    assistantMessages: [],
    assistantDraftSeed: null,
    pendingPlan: null,
    pendingExtraction: null,
    pendingSkillProposal: null,
    scripts: [],
    recentProjects: [],
    revisions: [],
    gitStatus: null,
    gitBusy: false,
    knowledgeStatus: null,
    knowledgeBusy: false,
    memoryStatus: null,
    memoryLessons: [],
    memorySkillPreview: '',
    memoryBusy: false,
    distilledLessons: [],
    distilledLessonsBusy: false,
    distilledLessonsMessage: null,
    tapirStatus: null,
    tapirBusy: false,
    latestRevisionId: null,
    revisionLoading: false,
    activeScriptName: null,
    scriptContents: {},
    dirtyScripts: {},
    lastSavedAt: null,
    needsSaveAs: false,
    scriptLoading: false,
    scriptSaving: false,
    mockCompileResult: null,
    workspace: null,
    workspaceBusy: false,
    workspaceInitHint: null,
    workspaceSearching: false,
    workspaceSearchQuery: null,
    workspaceSearchHits: [],
    workspaceSearchResult: null,
  }
}
