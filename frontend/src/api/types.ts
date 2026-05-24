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

export interface WorkbenchSnapshot {
  project: WorkbenchProject
  parameters: WorkbenchParameter[]
  preview: PreviewPayload
  warnings: string[]
}

export interface ApplyResult extends WorkbenchSnapshot {
  ok: boolean
  changed: Record<string, unknown>
}
