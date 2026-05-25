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

export interface WorkbenchSnapshot {
  project: WorkbenchProject
  parameters: WorkbenchParameter[]
  preview: PreviewPayload
  warnings: string[]
  compiler?: CompilerSettings
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

export interface CompilerSettingsResult {
  ok: boolean
  compiler?: CompilerSettings
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
