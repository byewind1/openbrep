import type { ApplyResult, PreviewPayload, WorkbenchSnapshot } from './types'

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

async function requestJson<T>(path: string, init: RequestInit, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, init)
    if (!response.ok) return fallback
    return (await response.json()) as T
  } catch {
    return fallback
  }
}

export const fallbackSnapshot: WorkbenchSnapshot = {
  project: { name: 'Demo Bookshelf', source: 'fallback' },
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
