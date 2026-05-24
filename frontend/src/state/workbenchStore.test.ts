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
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
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
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
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

test('records compile results in the workbench log', async () => {
  const store = createWorkbenchStore({
    fetchSnapshot: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    compileProject: async () => ({
      ok: true,
      compile: {
        success: true,
        mode: 'mock',
        output_path: '/workspace/output/Chair.gsm',
        stdout: 'compiled',
        stderr: '',
        errors: [],
        warnings: [],
      },
    }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
    applyParameters: async () => ({
      ok: true,
      changed: {},
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  })

  await store.getState().compileCurrentProject()

  expect(store.getState().compileLog).toEqual([
    'Mock compile passed: /workspace/output/Chair.gsm',
  ])
  expect(store.getState().compiling).toBe(false)
})

test('adds user and assistant messages to the assistant thread', async () => {
  const store = createWorkbenchStore({
    fetchSnapshot: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    askAssistant: async (message: string) => ({
      ok: true,
      assistant: { kind: 'explain_project', reply: `reply to ${message}` },
    }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
    applyParameters: async () => ({
      ok: true,
      changed: {},
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  })

  await store.getState().sendAssistantMessage('解释这个构件')

  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '解释这个构件' },
    { role: 'assistant', content: 'reply to 解释这个构件' },
  ])
  expect(store.getState().assistantBusy).toBe(false)
})

test('generate assistant message refreshes preview and records changed files', async () => {
  const store = createWorkbenchStore({
    fetchSnapshot: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    askAssistant: async () => ({ ok: false, error: 'use generate' }),
    generateWithAssistant: async (message: string) => ({
      ok: true,
      assistant: {
        kind: 'generate',
        reply: `changed ${message}`,
        changed_files: ['scripts/3d.gdl'],
        intent: 'MODIFY',
      },
      preview: {
        meshes: [{ name: 'changed', vertices: [], faces: [] }],
        wires: [],
        warnings: ['preview refreshed'],
      },
      warnings: ['preview refreshed'],
    }),
    applyParameters: async () => ({
      ok: true,
      changed: {},
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  })

  await store.getState().generateAssistantChanges('加一块层板')

  expect(store.getState().preview?.meshes[0]?.name).toBe('changed')
  expect(store.getState().warnings).toEqual(['preview refreshed'])
  expect(store.getState().assistantMessages.at(-1)).toEqual({
    role: 'assistant',
    content: 'changed 加一块层板\n\nChanged files: scripts/3d.gdl',
  })
})
