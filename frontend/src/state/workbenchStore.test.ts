import { createWorkbenchStore } from './workbenchStore'

test('updates draft parameter without changing saved parameter value', async () => {
  const store = createWorkbenchStore({
    fetchSnapshot: async () => ({
      project: { name: 'Demo', source: 'test' },
      parameters: [
        { name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true },
      ],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async () => ({
      project: { name: 'Loaded', source: 'hsf', path: '/tmp/Loaded' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    applyParameters: async () => ({
      ok: true,
      changed: { A: 2 },
      project: { name: 'Demo', source: 'test' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '2.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  })

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)

  expect(store.getState().draftParameters.A).toBe(2)
  expect(store.getState().parameters[0].value).toBe('1.0')
})

test('loads a project path and clears stale draft parameters', async () => {
  const loadedPath = '/workspace/Chair'
  const store = createWorkbenchStore({
    fetchSnapshot: async () => ({
      project: { name: 'Demo', source: 'test' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async (path: string) => ({
      project: { name: 'Chair', source: 'hsf', path },
      parameters: [{ name: 'B', type_tag: 'Length', description: 'Depth', value: '0.5', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: ['loaded'] },
      warnings: ['loaded'],
    }),
    applyParameters: async () => ({
      ok: true,
      changed: {},
      project: { name: 'Demo', source: 'test' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  })

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)
  await store.getState().loadProjectPath(loadedPath)

  expect(store.getState().project).toEqual({ name: 'Chair', source: 'hsf', path: loadedPath })
  expect(store.getState().parameters.map((parameter) => parameter.name)).toEqual(['B'])
  expect(store.getState().draftParameters).toEqual({})
  expect(store.getState().warnings).toEqual(['loaded'])
})
