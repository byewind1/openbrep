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

export interface CompilerSettings {
  mode: 'mock' | 'lp'
  converter_path: string
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
  project: WorkbenchProject
  parameters: WorkbenchParameter[]
  preview: PreviewPayload
  warnings: string[]
  compiler?: CompilerSettings
  llm?: LlmSettings
}

export interface ApplyResult extends WorkbenchSnapshot {
  ok: boolean
  changed: Record<string, unknown>
}

export interface CompileInfo {
  success: boolean
  mode: string
  output_path: string
  stdout: string
  stderr: string
  errors: string[]
  warnings: string[]
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

export interface ProjectScriptsResponse {
  scripts: ProjectScript[]
}

export interface ProjectScriptContentResponse {
  name: string
  path: string
  content: string
}

export interface SaveScriptResponse {
  success: boolean
  saved_at: string
}
