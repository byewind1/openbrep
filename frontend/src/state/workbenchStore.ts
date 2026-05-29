import { createStore } from 'zustand/vanilla'
import {
  addProjectParameter,
  applyParameters,
  askAssistant,
  chooseCompilerFile,
  chooseProjectDirectory,
  closeProject,
  compileProject,
  createProjectFromPrompt,
  fetchPreview2D,
  fetchPreview,
  fetchRuntimeSettings,
  fetchSnapshot,
  generateWithAssistant,
  getProjectScript,
  importGdlFile,
  listProjectRevisions,
  listProjectScripts,
  listRecentProjects,
  loadProjectPath,
  mockCompile,
  restoreProjectRevision,
  saveProjectRevision,
  saveProjectScript,
  updateCompilerSettings,
  updateLlmSettings,
  validateProjectParameters,
} from '../api/client'
import { createAssistantActions } from './actions/assistantActions'
import { createCompileActions } from './actions/compileActions'
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
  closeProject,
  chooseProjectDirectory,
  chooseCompilerFile,
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
  updateCompilerSettings,
  fetchRuntimeSettings,
  updateLlmSettings,
  askAssistant,
  generateWithAssistant,
  applyParameters,
  addProjectParameter,
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
    compilerSettings: { mode: 'mock' as const, converter_path: '' },
    llmSettings: defaultLlmSettings(),
    activeRailPanel: '3d' as const,
    assistantBusy: false,
    assistantMessages: [],
    scripts: [],
    recentProjects: [],
    revisions: [],
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
