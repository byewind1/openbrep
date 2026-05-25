import type { WorkbenchApi } from './workbenchStore'
import { createWorkbenchStore } from './workbenchStore'

function makeApi(overrides: Partial<WorkbenchApi> = {}): WorkbenchApi {
  return {
    fetchSnapshot: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '' },
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    loadProjectPath: async (path: string) => ({
      project: { name: 'Chair', source: 'hsf', path },
      parameters: [{ name: 'B', type_tag: 'Length', description: 'Depth', value: '0.5', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: ['loaded'] },
      warnings: ['loaded'],
      compiler: { mode: 'mock', converter_path: '' },
    }),
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    updateCompilerSettings: async () => ({ ok: false, error: 'not loaded' }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
    applyParameters: async (parameters: Record<string, unknown>) => ({
      ok: true,
      changed: parameters,
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '2.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '' },
    }),
    ...overrides,
  }
}

test('updates draft parameter without changing saved parameter value', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)

  expect(store.getState().draftParameters.A).toBe(2)
  expect(store.getState().parameters[0].value).toBe('1.0')
})

test('loads a project path and clears stale draft parameters', async () => {
  const loadedPath = '/workspace/Chair'
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)
  await store.getState().loadProjectPath(loadedPath)

  expect(store.getState().project).toEqual({ name: 'Chair', source: 'hsf', path: loadedPath })
  expect(store.getState().parameters.map((parameter) => parameter.name)).toEqual(['B'])
  expect(store.getState().draftParameters).toEqual({})
  expect(store.getState().warnings).toEqual(['loaded'])
})

test('loads compiler settings from snapshot', async () => {
  const store = createWorkbenchStore(
    makeApi({
      fetchSnapshot: async () => ({
        project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter' },
      }),
    }),
  )

  await store.getState().load()

  expect(store.getState().compilerSettings).toEqual({
    mode: 'lp',
    converter_path: '/Applications/LP_XMLConverter',
  })
})

test('updates compiler settings through the API', async () => {
  const store = createWorkbenchStore(
    makeApi({
      updateCompilerSettings: async (settings) => ({ ok: true, compiler: settings }),
    }),
  )

  await store.getState().setCompilerSettings({ mode: 'lp', converter_path: '/converter' })

  expect(store.getState().compilerSettings).toEqual({ mode: 'lp', converter_path: '/converter' })
})

test('records compile results in the workbench log', async () => {
  const store = createWorkbenchStore(
    makeApi({
      compileProject: async () => ({
        ok: true,
        compile: {
          success: true,
          mode: 'lp',
          output_path: '/workspace/output/Chair.gsm',
          stdout: 'compiled',
          stderr: '',
          errors: [],
          warnings: [],
        },
      }),
    }),
  )

  await store.getState().compileCurrentProject()

  expect(store.getState().compileLog).toEqual(['LP compile passed: /workspace/output/Chair.gsm'])
  expect(store.getState().compiling).toBe(false)
})

test('adds user and assistant messages to the assistant thread', async () => {
  const store = createWorkbenchStore(
    makeApi({
      askAssistant: async (message: string) => ({
        ok: true,
        assistant: { kind: 'explain_project', reply: `reply to ${message}` },
      }),
    }),
  )

  await store.getState().sendAssistantMessage('解释这个构件')

  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '解释这个构件' },
    { role: 'assistant', content: 'reply to 解释这个构件' },
  ])
  expect(store.getState().assistantBusy).toBe(false)
})

test('generate assistant message refreshes preview and records changed files', async () => {
  const store = createWorkbenchStore(
    makeApi({
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
    }),
  )

  await store.getState().generateAssistantChanges('加一块层板')

  expect(store.getState().preview?.meshes[0]?.name).toBe('changed')
  expect(store.getState().warnings).toEqual(['preview refreshed'])
  expect(store.getState().assistantMessages.at(-1)).toEqual({
    role: 'assistant',
    content: 'changed 加一块层板\n\nChanged files: scripts/3d.gdl',
  })
})
