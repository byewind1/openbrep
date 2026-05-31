import type { WorkbenchParameter } from '../api/types'

const DIMENSION_TYPES = new Set(['Length', 'Angle', 'RealNum'])

export interface ParameterGroups {
  dimensions: WorkbenchParameter[]
  properties: WorkbenchParameter[]
}

export function groupParameters(parameters: WorkbenchParameter[]): ParameterGroups {
  return {
    dimensions: parameters.filter((param) => DIMENSION_TYPES.has(param.type_tag)),
    properties: parameters.filter((param) => !DIMENSION_TYPES.has(param.type_tag)),
  }
}
