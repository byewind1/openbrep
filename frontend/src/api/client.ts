import type {
  ApplyResult,
  AssistantResult,
  CompileResult,
  MockCompileResponse,
  CompilerSettings,
  CompilerSettingsResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  LlmSettings,
  LlmSettingsResult,
  PreviewPayload,
  ProjectScriptContentResponse,
  ProjectScriptsResponse,
  RecentProjectsResponse,
  RuntimeSettingsResult,
  SaveScriptResponse,
  WorkbenchSnapshot,
} from './types'

const API_BASE = import.meta.env.VITE_OPENBREP_API || ''

export async function fetchSnapshot(): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>('/api/snapshot', { method: 'GET' }, fallbackSnapshot)
}

export async function fetchPreview(parameters: Record<string, unknown>): Promise<PreviewPayload> {
  const response = await requestJson<{ preview: PreviewPayload }>(
    '/api/preview',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters }),
    },
    { preview: fallbackSnapshot.preview },
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

export async function listRecentProjects(): Promise<RecentProjectsResponse> {
  return requestJson<RecentProjectsResponse>(
    '/api/project/recent',
    { method: 'GET' },
    { ok: false, projects: [], error: 'OpenBrep local API is not available.' },
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

export async function compileProject(): Promise<CompileResult> {
  return requestJson<CompileResult>(
    '/api/compile',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
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

export async function generateWithAssistant(message: string, assistantSettings = ''): Promise<GenerateResult> {
  return requestJson<GenerateResult>(
    '/api/assistant/generate',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, assistant_settings: assistantSettings }),
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

export async function mockCompile(): Promise<MockCompileResponse> {
  return requestJson<MockCompileResponse>(
    '/api/compile/mock',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { success: false, mode: 'mock', issues: [], duration_ms: 0, error: 'OpenBrep local API is not available.' },
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
  compiler: { mode: 'mock', converter_path: '' },
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
