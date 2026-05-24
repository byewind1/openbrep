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
