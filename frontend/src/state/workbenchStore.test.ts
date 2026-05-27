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
    chooseProjectDirectory: async () => ({ ok: false, cancelled: true }),
    chooseCompilerFile: async () => ({ ok: false, cancelled: true }),
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    listProjectScripts: async () => ({
      scripts: [
        { name: '3d.gdl', path: 'scripts/3d.gdl', exists: true, size: 128 },
        { name: '2d.gdl', path: 'scripts/2d.gdl', exists: true, size: 64 },
      ],
    }),
    getProjectScript: async (scriptName: string) => ({
      name: scriptName,
      path: `scripts/${scriptName}`,
      content: `content for ${scriptName}`,
    }),
    saveProjectScript: async () => ({ success: true, saved_at: '2026-05-27T09:00:00' }),
    mockCompile: async () => ({ success: true, mode: 'mock', issues: [], duration_ms: 12 }),
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

test('browses for a project directory and loads the selected HSF snapshot', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseProjectDirectory: async () => ({
        ok: true,
        path: '/workspace/Browsed',
        project: { name: 'Browsed', source: 'hsf', path: '/workspace/Browsed' },
        parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.5', is_fixed: true }],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        compiler: { mode: 'mock', converter_path: '' },
      }),
    }),
  )

  await store.getState().browseProjectDirectory()

  expect(store.getState().project).toEqual({ name: 'Browsed', source: 'hsf', path: '/workspace/Browsed' })
  expect(store.getState().parameters[0].value).toBe('1.5')
  expect(store.getState().draftParameters).toEqual({})
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

test('load fetches scripts and opens 3d.gdl by default', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()

  expect(store.getState().scripts.map((script) => script.name)).toEqual(['3d.gdl', '2d.gdl'])
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().scriptContents['3d.gdl']).toBe('content for 3d.gdl')
})

test('openScript loads content and marks the script active', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadScripts()
  await store.getState().openScript('2d.gdl')

  expect(store.getState().activeScriptName).toBe('2d.gdl')
  expect(store.getState().scriptContents['2d.gdl']).toBe('content for 2d.gdl')
})

test('updateActiveScriptContent marks active script dirty', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  store.getState().updateActiveScriptContent('changed 3d content')

  expect(store.getState().scriptContents['3d.gdl']).toBe('changed 3d content')
  expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
})

test('saveActiveScript clears dirty state after successful save', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  store.getState().updateActiveScriptContent('changed 3d content')
  await store.getState().saveActiveScript()

  expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
  expect(store.getState().scriptSaving).toBe(false)
  expect(store.getState().compileLog[0]).toContain('Saved 3d.gdl')
})

test('runMockCompile stores diagnostics result', async () => {
  const store = createWorkbenchStore(
    makeApi({
      mockCompile: async () => ({
        success: false,
        mode: 'mock',
        issues: [{ severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' }],
        duration_ms: 23,
      }),
    }),
  )

  await store.getState().runMockCompile()

  expect(store.getState().mockCompileResult?.issues).toHaveLength(1)
  expect(store.getState().mockCompileResult?.issues[0]?.message).toBe('FOR/NEXT mismatch')
  expect(store.getState().compileLog[0]).toContain('1 errors')
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

test('browses for LP_XMLConverter and stores compiler settings', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseCompilerFile: async () => ({
        ok: true,
        path: '/Applications/LP_XMLConverter',
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter' },
      }),
    }),
  )

  await store.getState().browseCompilerFile()

  expect(store.getState().compilerSettings).toEqual({
    mode: 'lp',
    converter_path: '/Applications/LP_XMLConverter',
  })
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
