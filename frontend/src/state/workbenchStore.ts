import { createStore } from 'zustand/vanilla'
import {
  applyParameters,
  askAssistant,
  chooseCompilerFile,
  chooseProjectDirectory,
  closeProject,
  compileProject,
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
} from '../api/client'
import { createAssistantActions } from './actions/assistantActions'
import { createCompileActions } from './actions/compileActions'
import { createParameterActions } from './actions/parameterActions'
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
  loadProjectPath,
  importGdlFile,
  closeProject,
  chooseProjectDirectory,
  chooseCompilerFile,
  compileProject,
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
    draftParameters: {},
    preview: null,
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
