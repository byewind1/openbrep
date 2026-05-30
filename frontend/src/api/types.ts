export type ParameterType =
  | 'Length'
  | 'Angle'
  | 'RealNum'
  | 'Integer'
  | 'Boolean'
  | 'String'
  | 'Material'
  | 'PenColor'
  | 'FillPattern'
  | 'LineType'
  | string

export interface WorkbenchProject {
  name: string
  source?: string
  path?: string
}

export interface WorkbenchParameter {
  name: string
  type_tag: ParameterType
  description: string
  value: string
  is_fixed: boolean
}

export interface ProjectScript {
  name: string
  path: string
  exists: boolean
  size: number
}

export interface RecentProject {
  path: string
  exists: boolean
}

export interface ProjectRevision {
  revision_id: string
  project_name: string
  gsm_name: string
  created_at: string
  message: string
  file_count: number
  trigger: string
  intent: string
  user_instruction: string
  changed_files: string[]
  parent_revision_id: string | null
  compile: Record<string, unknown>
  explanation: string
  is_latest: boolean
}

export interface PreviewMesh {
  name: string
  vertices: number[][]
  faces: number[][]
}

export interface PreviewPayload {
  meshes: PreviewMesh[]
  wires: number[][][]
  warnings?: string[]
}

export interface Preview2DPayload {
  lines: Array<{ from: [number, number]; to: [number, number] }>
  polygons: Array<Array<[number, number]>>
  circles: Array<{ cx: number; cy: number; r: number }>
  arcs: Array<{ cx: number; cy: number; r: number; a0: number; a1: number }>
  warnings?: string[]
}

export interface CompilerSettings {
  mode: 'mock' | 'lp'
  converter_path: string
  output_dir: string
}

export interface LlmSettings {
  model: string
  models: string[]
  api_key: string
  api_base: string
  max_retries: number
  assistant_settings: string
}

export interface WorkbenchSnapshot {
  ok?: boolean
  project: WorkbenchProject
  parameters: WorkbenchParameter[]
  preview: PreviewPayload
  warnings: string[]
  compiler?: CompilerSettings
  llm?: LlmSettings
  error?: string
}

export interface HsfExportResult extends WorkbenchSnapshot {
  ok: boolean
  saved_to?: string
  cancelled?: boolean
}

export interface ApplyResult extends WorkbenchSnapshot {
  ok: boolean
  changed: Record<string, unknown>
}

export interface AddParameterRequest {
  name: string
  type_tag: 'Length' | 'RealNum' | 'Integer' | 'Boolean' | 'String'
  value: unknown
  description?: string
}

export interface AddParameterResult extends WorkbenchSnapshot {
  ok: boolean
  added?: WorkbenchParameter
}

export interface UpdateParameterRequest {
  name: string
  new_name?: string
  type_tag?: 'Length' | 'RealNum' | 'Integer' | 'Boolean' | 'String'
  value?: unknown
  description?: string
}

export interface UpdateParameterResult extends WorkbenchSnapshot {
  ok: boolean
  updated?: WorkbenchParameter
}

export interface DeleteParameterResult extends WorkbenchSnapshot {
  ok: boolean
  deleted?: string
}

export interface ValidateParametersResult {
  ok: boolean
  issues: string[]
  error?: string
}

export interface CompileInfo {
  success: boolean
  mode: string
  output_path: string
  stdout: string
  stderr: string
  errors: string[]
  warnings: string[]
  gsm_size_bytes?: number | null
  parameter_count?: number | null
}

export interface CompileResult {
  ok: boolean
  compile?: CompileInfo
  error?: string
}

export interface CompileIssue {
  severity: 'info' | 'warning' | 'error' | string
  script: string
  line: number | null
  message: string
}

export interface MockCompileResponse {
  ok?: boolean
  success: boolean
  mode: string
  issues: CompileIssue[]
  duration_ms: number
  output_path?: string
  gsm_size_bytes?: number | null
  parameter_count?: number | null
  error?: string
}

export interface RevealArtifactResult {
  ok: boolean
  path?: string
  error?: string
}

export interface CompilerSettingsResult {
  ok: boolean
  compiler?: CompilerSettings
  error?: string
}

export interface RuntimeSettingsResult {
  ok: boolean
  compiler?: CompilerSettings
  llm?: LlmSettings
  error?: string
}

export interface LlmSettingsResult {
  ok: boolean
  llm?: LlmSettings
  error?: string
}

export interface DirectoryChoiceResult extends Partial<WorkbenchSnapshot> {
  ok: boolean
  path?: string
  cancelled?: boolean
  error?: string
}

export interface FileChoiceResult {
  ok: boolean
  path?: string
  compiler?: CompilerSettings
  cancelled?: boolean
  error?: string
}

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AssistantResult {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
  }
  error?: string
}

export interface AssistantHistoryResult {
  ok: boolean
  messages: AssistantMessage[]
  error?: string
}

export interface SaveAssistantHistoryResult {
  ok: boolean
  count: number
  error?: string
}

export interface AssistantCodeBlock {
  path: string
  script_name: string
  content: string
}

export interface AssistantCodeBlocksResult {
  ok: boolean
  blocks: AssistantCodeBlock[]
  error?: string
}

export interface ProjectMemoryStatus {
  memory_root: string
  chat_count: number
  lesson_count: number
  has_learned_skill: boolean
  total_bytes: number
}

export interface ProjectMemoryStatusResult {
  ok: boolean
  memory?: ProjectMemoryStatus
  error?: string
}

export interface ClearProjectMemoryResult {
  ok: boolean
  before?: ProjectMemoryStatus
  error?: string
}

export interface ErrorLesson {
  fingerprint: string
  category: string
  summary: string
  guidance: string
  example: string
  count: number
  first_seen: string
  last_seen: string
  source: string
  project_name: string
  raw_excerpt: string
}

export interface ProjectLessonsResult {
  ok: boolean
  lessons: ErrorLesson[]
  error?: string
}

export interface LearningSummaryResult {
  ok: boolean
  lesson_count: number
  path: string
  message: string
}

export interface SummarizeMemoryResult {
  ok: boolean
  summary?: LearningSummaryResult
  skill?: string
  error?: string
}

export interface GenerateResult {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
    changed_files: string[]
    intent: string
  }
  preview?: PreviewPayload
  warnings?: string[]
  events?: Array<{ type: string; data: unknown }>
  error?: string
}

export interface CreateProjectResult extends WorkbenchSnapshot {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
    changed_files: string[]
    intent: string
  }
  events?: Array<{ type: string; data: unknown }>
  error?: string
}

export interface ProjectScriptsResponse {
  scripts: ProjectScript[]
}

export interface RecentProjectsResponse {
  ok: boolean
  projects: RecentProject[]
  error?: string
}

export interface ProjectRevisionsResponse {
  ok: boolean
  revisions: ProjectRevision[]
  latest_revision_id?: string | null
  error?: string
}

export interface SaveRevisionResponse {
  ok: boolean
  revision?: ProjectRevision
  latest_revision_id?: string | null
  error?: string
}

export interface RestoreRevisionResponse extends Partial<WorkbenchSnapshot> {
  ok: boolean
  restored_revision_id?: string
  revision?: ProjectRevision
  latest_revision_id?: string | null
  error?: string
}

export interface ProjectScriptContentResponse {
  ok?: boolean
  name: string
  path: string
  content: string
  error?: string
}

export interface SaveScriptResponse {
  ok?: boolean
  success: boolean
  saved_at: string
  error?: string
}
