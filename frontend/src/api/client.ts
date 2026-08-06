import type {
  ApplyResult,
  AddParameterRequest,
  AddParameterResult,
  AssistantCodeBlocksResult,
  AssistantHistoryResult,
  AssistantImageAttachment,
  AssistantMessage,
  AssistantResult,
  AssistantStreamEvent,
  ClearProjectMemoryResult,
  CompileResult,
  CreateProjectResult,
  DeleteMemoryLessonResult,
  IgnoreMemoryLessonResult,
  MockCompileResponse,
  CompilerSettings,
  CompilerSettingsResult,
  ConfigRevisionResult,
  DirectoryChoiceResult,
  FileChoiceResult,
  GenerateResult,
  HsfExportResult,
  WorkspaceScanResult,
  WorkspaceSearchResult,
  LlmSettings,
  LlmSettingsResult,
  LlmConnectionTestResult,
  Preview2DPayload,
  PreviewPayload,
  ProjectGitResponse,
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
  KnowledgeStatus,
  UpdateMemoryLessonRequest,
  UpdateMemoryLessonResult,
  UpdateParameterRequest,
  UpdateParameterResult,
  ValidateParametersResult,
  WorkbenchSnapshot,
} from './types'

const API_BASE = import.meta.env.VITE_OPENBREP_API || ''

// ── 后端健康事件 ────────────────────────────────────────────
// 错误横幅的自愈机制：任何请求成功都清除"后端未运行"状态，
// 失败按类型分类（拒绝连接=未运行 / 502-504=启动中 / 超时）。
// store 侧通过 setApiHealthListener 挂载处理与 3s 恢复轮询。

export type ApiHealthEvent =
  | { kind: 'ok' }
  | { kind: 'down' }
  | { kind: 'starting'; status: number }
  | { kind: 'timeout' }

type ApiHealthListener = (event: ApiHealthEvent) => void
let apiHealthListener: ApiHealthListener | null = null

export function setApiHealthListener(listener: ApiHealthListener | null): void {
  apiHealthListener = listener
}

function emitApiHealth(event: ApiHealthEvent): void {
  apiHealthListener?.(event)
}

/** 恢复轮询专用探针：短超时直连 snapshot，成功/失败都会发健康事件。 */
export async function probeBackend(timeoutMs: number): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/api/snapshot`, {
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      emitApiHealth({ kind: 'starting', status: response.status })
      return false
    }
    if (!response.ok) {
      emitApiHealth({ kind: 'down' })
      return false
    }
    emitApiHealth({ kind: 'ok' })
    return true
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      emitApiHealth({ kind: 'timeout' })
    } else {
      emitApiHealth({ kind: 'down' })
    }
    return false
  }
}

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

export async function workspaceInit(path: string): Promise<WorkspaceScanResult> {
  return requestJson<WorkspaceScanResult>(
    '/api/workspace/init',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', workspace: null },
  )
}

export async function workspaceOpen(path: string): Promise<WorkspaceScanResult> {
  return requestJson<WorkspaceScanResult>(
    '/api/workspace/open',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', workspace: null },
  )
}

export async function workspaceClose(): Promise<{ ok: boolean; error?: string; workspace: null }> {
  return requestJson<{ ok: boolean; error?: string; workspace: null }>(
    '/api/workspace/close',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.', workspace: null },
  )
}

export async function workspaceScan(): Promise<WorkspaceScanResult> {
  return requestJson<WorkspaceScanResult>(
    '/api/workspace/scan',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.', workspace: null },
  )
}

export async function workspaceSearch(query: string): Promise<WorkspaceSearchResult> {
  const q = encodeURIComponent(query)
  return requestJson<WorkspaceSearchResult>(
    `/api/workspace/search?q=${q}`,
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.', query, hits: [], hit_count: 0 },
  )
}

export async function newProject(): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/new',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.', ...fallbackSnapshot },
  )
}

export async function saveProject(): Promise<HsfExportResult> {
  return requestJson<HsfExportResult>(
    '/api/project/save',
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

export async function importBlenderScript(path = ''): Promise<WorkbenchSnapshot> {
  return requestJson<WorkbenchSnapshot>(
    '/api/project/import-blender',
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
  signal?: AbortSignal,
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
    signal,
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

export async function fetchProjectGitStatus(): Promise<ProjectGitResponse> {
  return requestJson<ProjectGitResponse>(
    '/api/project/git',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.', git: fallbackProjectGitStatus },
  )
}

export async function initializeProjectGit(): Promise<ProjectGitResponse> {
  return requestJson<ProjectGitResponse>(
    '/api/project/git/init',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
    { ok: false, error: 'OpenBrep local API is not available.', git: fallbackProjectGitStatus },
  )
}

export async function updateProjectGitSettings(enabled: boolean): Promise<ProjectGitResponse> {
  return requestJson<ProjectGitResponse>(
    '/api/project/git/settings',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', git: fallbackProjectGitStatus },
  )
}

export async function commitProjectGit(message = ''): Promise<ProjectGitResponse> {
  return requestJson<ProjectGitResponse>(
    '/api/project/git/commit',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
    { ok: false, error: 'OpenBrep local API is not available.', git: fallbackProjectGitStatus },
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

export async function fetchConfigRevision(): Promise<ConfigRevisionResult> {
  return requestJson<ConfigRevisionResult>(
    '/api/settings/config-revision',
    { method: 'GET' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function openConfig(): Promise<{ ok: boolean; error?: string }> {
  return requestJson(
    '/api/settings/open-config',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function testLlmConnection(): Promise<LlmConnectionTestResult> {
  return requestJson<LlmConnectionTestResult>(
    '/api/settings/llm/test',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    { ok: false, error: 'OpenBrep local API is not available.', category: 'llm_configuration' },
  )
}

export async function updateLlmModel(model: string): Promise<LlmSettingsResult> {
  return requestJson<LlmSettingsResult>(
    '/api/settings/llm/model',
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function updateLlmApiKey(model: string, apiKey: string): Promise<LlmSettingsResult> {
  return requestJson<LlmSettingsResult>(
    '/api/settings/llm/api-key',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, api_key: apiKey }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
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

export async function askAssistant(message: string, signal?: AbortSignal): Promise<AssistantResult> {
  return requestJson<AssistantResult>(
    '/api/assistant',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
    { ok: false, error: 'OpenBrep local API is not available.' },
    signal,
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

export async function fetchKnowledgeStatus(): Promise<KnowledgeStatus> {
  return requestJson<KnowledgeStatus>(
    '/api/knowledge/status',
    { method: 'GET' },
    { ok: false, has_pro: false, free_doc_count: 0, pro_doc_count: 0, pro_doc_names: [], pro_dir: '', pro_dir_exists: false, error: 'OpenBrep local API is not available.' },
  )
}

export async function reloadKnowledge(): Promise<KnowledgeStatus> {
  return requestJson<KnowledgeStatus>(
    '/api/knowledge/reload',
    { method: 'POST' },
    { ok: false, has_pro: false, free_doc_count: 0, pro_doc_count: 0, pro_doc_names: [], pro_dir: '', pro_dir_exists: false, error: 'OpenBrep local API is not available.' },
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
  signal?: AbortSignal,
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
    signal,
  )
}

export async function generateWithAssistantStream(
  message: string,
  assistantSettings = '',
  image?: AssistantImageAttachment | null,
  onEvent?: (event: AssistantStreamEvent) => void,
  signal?: AbortSignal,
): Promise<GenerateResult> {
  const response = await fetch(`${API_BASE}/api/assistant/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      assistant_settings: assistantSettings,
      stream: true,
      ...(image ? { image_b64: image.b64, image_mime: image.mime } : {}),
    }),
    signal,
  })

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => 'Stream request failed.')
    return { ok: false, error: text, assistant: null, preview: null, warnings: [], events: [] } as GenerateResult
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalResult: GenerateResult | null = null

  try {
    while (true) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      let currentEvent: AssistantStreamEvent | null = null
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = { type: line.slice(7).trim() as AssistantStreamEvent['type'], data: {} }
        } else if (line.startsWith('data: ') && currentEvent) {
          try {
            currentEvent.data = JSON.parse(line.slice(6))
          } catch {
            currentEvent.data = { raw: line.slice(6) }
          }
        } else if (line === '' && currentEvent) {
          if (currentEvent.type === 'done') {
            finalResult = (currentEvent.data as unknown as GenerateResult) ?? null
          } else if (currentEvent.type === 'error') {
            finalResult = {
              ok: false,
              error: String((currentEvent.data as { error?: string }).error ?? 'Stream error'),
              assistant: null,
              preview: null,
              warnings: [],
              events: [],
            } as GenerateResult
          } else {
            onEvent?.(currentEvent)
          }
          currentEvent = null
        }
      }
    }
  } finally {
    reader.releaseLock()
  }

  if (signal?.aborted) {
    return { ok: false, error: 'Aborted.', assistant: null, preview: null, warnings: [], events: [] } as GenerateResult
  }

  return (
    finalResult ?? ({
      ok: false,
      error: 'Stream ended without final result.',
      assistant: null,
      preview: null,
      warnings: [],
      events: [],
    } as GenerateResult)
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

export async function shutdownServer(): Promise<{ ok: boolean }> {
  try {
    const response = await fetch(`${API_BASE}/api/shutdown`, { method: 'POST' })
    return (await response.json()) as { ok: boolean }
  } catch {
    return { ok: false }
  }
}

async function requestJson<T>(path: string, init: RequestInit, fallback: T, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, signal ? { ...init, signal } : init)
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      emitApiHealth({ kind: 'starting', status: response.status })
      return fallback
    }
    const payload = (await response.json()) as T
    emitApiHealth({ kind: 'ok' })
    if (!response.ok) return payload ?? fallback
    return payload
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    emitApiHealth({ kind: 'down' })
    return fallback
  }
}

export const fallbackSnapshot: WorkbenchSnapshot = {
  project: null,
  compiler: { mode: 'mock', converter_path: '', output_dir: '' },
  llm: {
    model: 'glm-4-flash',
    models: ['glm-4-flash'],
    api_key: '',
    api_base: '',
    max_retries: 5,
    assistant_settings: '',
  },
  parameters: [],
  preview: {
    meshes: [],
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

export const fallbackProjectGitStatus = {
  enabled: false,
  initialized: false,
  dirty: false,
  changes: [],
  last_commit: '',
}
