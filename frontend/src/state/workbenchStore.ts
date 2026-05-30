import { createStore } from 'zustand/vanilla'
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
  exportHsfProject,
  extractAssistantCodeBlocks,
  fetchMemoryLessons,
  fetchMemoryStatus,
  fetchPreview2D,
  fetchPreview,
  fetchRuntimeSettings,
  fetchSnapshot,
  generateWithAssistant,
  getProjectScript,
  ignoreMemoryLesson,
  importGdlFile,
  importGsmFile,
  listAssistantHistory,
  listProjectRevisions,
  listProjectScripts,
  listRecentProjects,
  loadProjectPath,
  mockCompile,
  revealArtifact,
  restoreProjectRevision,
  saveProjectRevision,
  saveProjectScript,
  saveAssistantHistory,
  summarizeProjectMemory,
  updateCompilerSettings,
  updateLlmSettings,
  updateMemoryLesson,
  updateProjectParameter,
  validateProjectParameters,
} from '../api/client'
import { createAssistantActions } from './actions/assistantActions'
import { createCompileActions } from './actions/compileActions'
import { createMemoryActions } from './actions/memoryActions'
import { createParameterActions } from './actions/parameterActions'
import { createPreviewActions } from './actions/previewActions'
import { createProjectActions } from './actions/projectActions'
import { createRevisionActions } from './actions/revisionActions'
import { createScriptActions } from './actions/scriptActions'
import { createSettingsActions } from './actions/settingsActions'
import type { WorkbenchActionContext, WorkbenchApi, WorkbenchSet, WorkbenchState } from './workbenchStoreTypes'
import { defaultLlmSettings } from './workbenchStoreUtils'

export type { WorkbenchApi, WorkbenchState } from './workbenchStoreTypes'

const defaultWorkbenchApi: WorkbenchApi = {
  fetchSnapshot,
  fetchPreview,
  fetchPreview2D,
  loadProjectPath,
  importGdlFile,
  importGsmFile,
  exportHsfProject,
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
  mockCompile,
  revealArtifact,
  updateCompilerSettings,
  fetchRuntimeSettings,
  updateLlmSettings,
  askAssistant,
  listAssistantHistory,
  saveAssistantHistory,
  clearAssistantHistory,
  extractAssistantCodeBlocks,
  fetchMemoryStatus,
  fetchMemoryLessons,
  summarizeProjectMemory,
  deleteMemoryLesson,
  ignoreMemoryLesson,
  updateMemoryLesson,
  clearProjectMemory,
  generateWithAssistant,
  applyParameters,
  addProjectParameter,
  updateProjectParameter,
  deleteProjectParameter,
  validateProjectParameters,
}

export function createWorkbenchStore(api: WorkbenchApi = defaultWorkbenchApi) {
  return createStore<WorkbenchState>((set, get) => {
    const context: WorkbenchActionContext = {
      api,
      get,
      set: set as WorkbenchSet,
    }
    return {
      ...initialWorkbenchState(),
      ...createProjectActions(context),
      ...createSettingsActions(context),
      ...createParameterActions(context),
      ...createPreviewActions(context),
      ...createCompileActions(context),
      ...createMemoryActions(context),
      ...createScriptActions(context),
      ...createRevisionActions(context),
      ...createAssistantActions(context),
      clearLastError() {
        set({ lastError: null })
      },
    }
  })
}

export const workbenchStore = createWorkbenchStore()

function initialWorkbenchState() {
  return {
    project: null,
    parameters: [],
    parameterIssues: [],
    draftParameters: {},
    preview: null,
    preview2d: null,
    warnings: [],
    loading: false,
    applying: false,
    compiling: false,
    lastError: null,
    compileLog: [],
    compilerSettings: { mode: 'mock' as const, converter_path: '', output_dir: '' },
    llmSettings: defaultLlmSettings(),
    activeRailPanel: '3d' as const,
    assistantBusy: false,
    assistantMessages: [],
    scripts: [],
    recentProjects: [],
    revisions: [],
    memoryStatus: null,
    memoryLessons: [],
    memorySkillPreview: '',
    memoryBusy: false,
    latestRevisionId: null,
    revisionLoading: false,
    activeScriptName: null,
    scriptContents: {},
    dirtyScripts: {},
    scriptLoading: false,
    scriptSaving: false,
    mockCompileResult: null,
  }
}
