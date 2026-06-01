import type {
  ApplyResult,
  AddParameterRequest,
  AddParameterResult,
  AssistantCodeBlocksResult,
  AssistantHistoryResult,
  AssistantImageAttachment,
  AssistantMessage,
  AssistantResult,
  ClearProjectMemoryResult,
  CompileResult,
  CreateProjectResult,
  DeleteMemoryLessonResult,
  IgnoreMemoryLessonResult,
  MockCompileResponse,
  CompilerSettings,
  CompilerSettingsResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  HsfExportResult,
  LlmSettings,
  LlmSettingsResult,
  LlmConnectionTestResult,
  Preview2DPayload,
  PreviewPayload,
  ProjectLessonsResult,
  ProjectScriptContentResponse,
  ProjectScriptsResponse,
  ProjectMemoryStatusResult,
  ProjectRevisionsResponse,
  RecentProjectsResponse,
  RevealArtifactResult,
  RestoreRevisionResponse,
  RuntimeSettingsResult,
  SaveAssistantHistoryResult,
  SaveScriptResponse,
  SaveRevisionResponse,
  SummarizeMemoryResult,
  TapirActionResult,
  TapirStatusResult,
  DeleteParameterResult,
  UpdateMemoryLessonRequest,
  UpdateMemoryLessonResult,
  UpdateParameterRequest,
  UpdateParameterResult,
  ValidateParametersResult,
  WorkbenchSnapshot,
} from './types'

const API_BASE = import.meta.env.VITE_OPENBREP_API || ''

export async function fetchSnapshot(): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>('/api/snapshot', { method: 'GET' }, fallbackSnapshot)
}

export async function fetchPreview(
  parameters: Record<string, unknown>,
  scripts?: Record<string, string>,
): Promise<PreviewPayload> {
  const response = await requestJson<{ preview: PreviewPayload }>(
    '/api/preview',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters, scripts }),
    },
    { preview: fallbackSnapshot.preview },
  )
  return response.preview
}

export async function fetchPreview2D(
  parameters: Record<string, unknown>,
  scripts?: Record<string, string>,
): Promise<Preview2DPayload> {
  const response = await requestJson<{ preview: Preview2DPayload }>(
    '/api/preview/2d',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters, scripts }),
    },
    { preview: fallbackPreview2D },
  )
  return response.preview
}

export async function loadProjectPath(path: string): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/load',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function closeProject(): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/close',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function importGdlFile(path = ''): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/import-gdl',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function importGsmFile(path = ''): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/import-gsm',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function exportHsfProject(parentDir = '', name = ''): Promise<HsfExportResult> {
  return requestJson<HsfExportResult>(
    '/api/project/export-hsf',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_dir: parentDir, name }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function createProjectFromPrompt(
  message: string,
  assistantSettings = '',
  image?: AssistantImageAttachment | null,
): Promise<CreateProjectResult> {
  return requestJson<CreateProjectResult>(
    '/api/project/create',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: message,
        assistant_settings: assistantSettings,
        ...(image ? { image_b64: image.b64, image_mime: image.mime } : {}),
      }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function listRecentProjects(): Promise<RecentProjectsResponse> {
  return requestJson<RecentProjectsResponse>(
    '/api/project/recent',
    { method: 'GET' },
    { ok: false, projects: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function listProjectRevisions(): Promise<ProjectRevisionsResponse> {
  return requestJson<ProjectRevisionsResponse>(
    '/api/project/revisions',
    { method: 'GET' },
    { ok: false, revisions: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function saveProjectRevision(message = ''): Promise<SaveRevisionResponse> {
  return requestJson<SaveRevisionResponse>(
    '/api/project/revision/save',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function restoreProjectRevision(revisionId: string): Promise<RestoreRevisionResponse> {
  return requestJson<RestoreRevisionResponse>(
    '/api/project/revision/restore',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision_id: revisionId }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function chooseProjectDirectory(): Promise<DirectoryChoiceResult> {
  return requestJson<DirectoryChoiceResult>(
    '/api/dialog/open-directory',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function chooseCompilerFile(): Promise<FileChoiceResult> {
  return requestJson<FileChoiceResult>(
    '/api/dialog/open-file',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ purpose: 'compiler' }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function chooseOutputDirectory(): Promise<DirectoryChoiceResult> {
  return requestJson<DirectoryChoiceResult>(
    '/api/dialog/output-directory',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function applyParameters(parameters: Record<string, unknown>): Promise<ApplyResult> {
  return requestJson<ApplyResult>(
    '/api/apply',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters }),
    },
    { ok: true, changed: parameters, ...fallbackSnapshot },
  )
}

export async function addProjectParameter(parameter: AddParameterRequest): Promise<AddParameterResult> {
  return requestJson<AddParameterResult>(
    '/api/project/parameters',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parameter),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function validateProjectParameters(): Promise<ValidateParametersResult> {
  return requestJson<ValidateParametersResult>(
    '/api/project/parameters/validate',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, issues: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function updateProjectParameter(parameter: UpdateParameterRequest): Promise<UpdateParameterResult> {
  return requestJson<UpdateParameterResult>(
    '/api/project/parameters/update',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parameter),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function deleteProjectParameter(name: string): Promise<DeleteParameterResult> {
  return requestJson<DeleteParameterResult>(
    '/api/project/parameters/delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function compileProject(outputDir = ''): Promise<CompileResult> {
  return requestJson<CompileResult>(
    '/api/compile',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_dir: outputDir }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function updateCompilerSettings(settings: CompilerSettings): Promise<CompilerSettingsResult> {
  return requestJson<CompilerSettingsResult>(
    '/api/settings/compiler',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function fetchRuntimeSettings(): Promise<RuntimeSettingsResult> {
  return requestJson<RuntimeSettingsResult>(
    '/api/settings/runtime',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function updateLlmSettings(settings: LlmSettings): Promise<LlmSettingsResult> {
  return requestJson<LlmSettingsResult>(
    '/api/settings/llm',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function testLlmConnection(settings: LlmSettings): Promise<LlmConnectionTestResult> {
  return requestJson<LlmConnectionTestResult>(
    '/api/settings/llm/test',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    },
    { ok: false, error: 'OpenBrep local API is not available.', category: 'llm_configuration' },
  )
}

export async function fetchTapirStatus(): Promise<TapirStatusResult> {
  return requestJson<TapirStatusResult>(
    '/api/tapir/status',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function reloadTapirLibraries(): Promise<TapirActionResult> {
  return requestJson<TapirActionResult>(
    '/api/tapir/reload-libraries',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, message: 'OpenBrep local API is not available.' },
  )
}

export async function syncTapirSelection(): Promise<TapirActionResult> {
  return requestJson<TapirActionResult>(
    '/api/tapir/selection/sync',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, message: 'OpenBrep local API is not available.' },
  )
}

export async function highlightTapirSelection(): Promise<TapirActionResult> {
  return requestJson<TapirActionResult>(
    '/api/tapir/selection/highlight',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, message: 'OpenBrep local API is not available.' },
  )
}

export async function loadTapirParameters(): Promise<TapirActionResult> {
  return requestJson<TapirActionResult>(
    '/api/tapir/parameters/load',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, message: 'OpenBrep local API is not available.' },
  )
}

export async function applyTapirParameterEdits(paramEdits?: Record<string, unknown>): Promise<TapirActionResult> {
  return requestJson<TapirActionResult>(
    '/api/tapir/parameters/apply',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param_edits: paramEdits ?? {} }),
    },
    { ok: false, message: 'OpenBrep local API is not available.' },
  )
}

export async function askAssistant(message: string): Promise<AssistantResult> {
  return requestJson<AssistantResult>(
    '/api/assistant',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function listAssistantHistory(): Promise<AssistantHistoryResult> {
  return requestJson<AssistantHistoryResult>(
    '/api/assistant/history',
    { method: 'GET' },
    { ok: false, messages: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function saveAssistantHistory(messages: AssistantMessage[]): Promise<SaveAssistantHistoryResult> {
  return requestJson<SaveAssistantHistoryResult>(
    '/api/assistant/history',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
    },
    { ok: false, count: 0, error: 'OpenBrep local API is not available.' },
  )
}

export async function clearAssistantHistory(): Promise<SaveAssistantHistoryResult> {
  return requestJson<SaveAssistantHistoryResult>(
    '/api/assistant/history',
    { method: 'DELETE' },
    { ok: false, count: 0, error: 'OpenBrep local API is not available.' },
  )
}

export async function extractAssistantCodeBlocks(content: string): Promise<AssistantCodeBlocksResult> {
  return requestJson<AssistantCodeBlocksResult>(
    '/api/assistant/code-blocks',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
    { ok: false, blocks: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function fetchMemoryStatus(): Promise<ProjectMemoryStatusResult> {
  return requestJson<ProjectMemoryStatusResult>(
    '/api/memory/status',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function fetchMemoryLessons(): Promise<ProjectLessonsResult> {
  return requestJson<ProjectLessonsResult>(
    '/api/memory/lessons',
    { method: 'GET' },
    { ok: false, lessons: [], error: 'OpenBrep local API is not available.' },
  )
}

export async function summarizeProjectMemory(): Promise<SummarizeMemoryResult> {
  return requestJson<SummarizeMemoryResult>(
    '/api/memory/summarize',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function deleteMemoryLesson(fingerprint: string): Promise<DeleteMemoryLessonResult> {
  return requestJson<DeleteMemoryLessonResult>(
    `/api/memory/lessons/${encodeURIComponent(fingerprint)}`,
    { method: 'DELETE' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function ignoreMemoryLesson(fingerprint: string): Promise<IgnoreMemoryLessonResult> {
  return requestJson<IgnoreMemoryLessonResult>(
    `/api/memory/lessons/${encodeURIComponent(fingerprint)}/ignore`,
    { method: 'POST' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function updateMemoryLesson(
  fingerprint: string,
  updates: UpdateMemoryLessonRequest,
): Promise<UpdateMemoryLessonResult> {
  return requestJson<UpdateMemoryLessonResult>(
    `/api/memory/lessons/${encodeURIComponent(fingerprint)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function clearProjectMemory(): Promise<ClearProjectMemoryResult> {
  return requestJson<ClearProjectMemoryResult>(
    '/api/memory',
    { method: 'DELETE' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function generateWithAssistant(
  message: string,
  assistantSettings = '',
  image?: AssistantImageAttachment | null,
): Promise<GenerateResult> {
  return requestJson<GenerateResult>(
    '/api/assistant/generate',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        assistant_settings: assistantSettings,
        ...(image ? { image_b64: image.b64, image_mime: image.mime } : {}),
      }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function listProjectScripts(): Promise<ProjectScriptsResponse> {
  return requestJson<ProjectScriptsResponse>('/api/project/scripts', { method: 'GET' }, { scripts: [] })
}

export async function getProjectScript(scriptName: string): Promise<ProjectScriptContentResponse | null> {
  return requestJson<ProjectScriptContentResponse | null>(
    `/api/project/script/${encodeURIComponent(scriptName)}`,
    { method: 'GET' },
    null,
  )
}

export async function saveProjectScript(scriptName: string, content: string): Promise<SaveScriptResponse> {
  return requestJson<SaveScriptResponse>(
    `/api/project/script/${encodeURIComponent(scriptName)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
    { success: false, saved_at: '' },
  )
}

export async function mockCompile(outputDir = ''): Promise<MockCompileResponse> {
  return requestJson<MockCompileResponse>(
    '/api/compile/mock',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_dir: outputDir }),
    },
    { success: false, mode: 'mock', issues: [], duration_ms: 0, error: 'OpenBrep local API is not available.' },
  )
}

export async function revealArtifact(path = ''): Promise<RevealArtifactResult> {
  return requestJson<RevealArtifactResult>(
    '/api/artifact/reveal',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

async function requestJson<T>(path: string, init: RequestInit, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, init)
    const payload = (await response.json()) as T
    if (!response.ok) return payload ?? fallback
    return payload
  } catch {
    return fallback
  }
}

export const fallbackSnapshot: WorkbenchSnapshot = {
  project: { name: 'Demo Bookshelf', source: 'fallback' },
  compiler: { mode: 'mock', converter_path: '', output_dir: '' },
  llm: {
    model: 'glm-4-flash',
    models: ['glm-4-flash'],
    api_key: '',
    api_base: '',
    max_retries: 5,
    assistant_settings: '',
  },
  parameters: [
    { name: 'A', type_tag: 'Length', description: '总宽', value: '1.2', is_fixed: true },
    { name: 'B', type_tag: 'Length', description: '总深', value: '0.36', is_fixed: true },
    { name: 'ZZYZX', type_tag: 'Length', description: '总高', value: '1.8', is_fixed: true },
    { name: 'shelf_count', type_tag: 'Integer', description: '层板数', value: '5', is_fixed: false },
    { name: 'has_back_panel', type_tag: 'Boolean', description: '背板', value: '1', is_fixed: false },
  ],
  preview: {
    meshes: [
      {
        name: 'fallback-block',
        vertices: [
          [0, 0, 0],
          [1, 0, 0],
          [1, 1, 0],
          [0, 1, 0],
          [0, 0, 1],
          [1, 0, 1],
          [1, 1, 1],
          [0, 1, 1],
        ],
        faces: [
          [0, 1, 2],
          [0, 2, 3],
          [4, 6, 5],
          [4, 7, 6],
        ],
      },
    ],
    wires: [],
    warnings: [],
  },
  warnings: [],
}

export const fallbackPreview2D: Preview2DPayload = {
  lines: [],
  polygons: [],
  circles: [],
  arcs: [],
  warnings: [],
}
