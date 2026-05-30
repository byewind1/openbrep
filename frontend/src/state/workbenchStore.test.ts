import type { WorkbenchApi } from './workbenchStore'
import { createWorkbenchStore } from './workbenchStore'

function makeApi(overrides: Partial<WorkbenchApi> = {}): WorkbenchApi {
  return {
    fetchSnapshot: async () => ({
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    fetchPreview: async () => ({ meshes: [], wires: [], warnings: [] }),
    fetchPreview2D: async () => ({
      lines: [{ from: [0, 0], to: [1, 1] }],
      polygons: [],
      circles: [],
      arcs: [],
      warnings: [],
    }),
    loadProjectPath: async (path: string) => ({
      project: { name: 'Chair', source: 'hsf', path },
      parameters: [{ name: 'B', type_tag: 'Length', description: 'Depth', value: '0.5', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: ['loaded'] },
      warnings: ['loaded'],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    importGdlFile: async () => ({
      ok: true,
      project: { name: 'spiral stair', source: 'hsf', path: '/workspace/spiral stair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: ['imported'] },
      warnings: ['imported'],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    closeProject: async () => ({
      ok: true,
      project: { name: 'Demo Bookshelf', source: 'demo' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    chooseProjectDirectory: async () => ({ ok: false, cancelled: true }),
    chooseCompilerFile: async () => ({ ok: false, cancelled: true }),
    chooseOutputDirectory: async () => ({ ok: false, cancelled: true }),
    compileProject: async () => ({ ok: false, error: 'not loaded' }),
    createProjectFromPrompt: async () => ({
      ok: true,
      assistant: {
        kind: 'create',
        reply: 'created bookshelf',
        changed_files: ['scripts/3d.gdl'],
        intent: 'CREATE',
      },
      project: { name: 'Bookshelf', source: 'hsf', path: '/workspace/Bookshelf' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: ['created'] },
      warnings: ['created'],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    listProjectScripts: async () => ({
      scripts: [
        { name: '3d.gdl', path: 'scripts/3d.gdl', exists: true, size: 128 },
        { name: '2d.gdl', path: 'scripts/2d.gdl', exists: true, size: 64 },
        { name: 'paramlist.xml', path: 'paramlist.xml', exists: true, size: 256 },
      ],
    }),
    listRecentProjects: async () => ({
      ok: true,
      projects: [{ path: '/workspace/Chair', exists: true }],
    }),
    listProjectRevisions: async () => ({
      ok: true,
      latest_revision_id: 'r0001',
      revisions: [
        {
          revision_id: 'r0001',
          project_name: 'Chair',
          gsm_name: 'Chair',
          created_at: '2026-05-27T09:00:00Z',
          message: 'stable',
          file_count: 3,
          trigger: 'manual',
          intent: '',
          user_instruction: '',
          changed_files: [],
          parent_revision_id: null,
          compile: {},
          explanation: '',
          is_latest: true,
        },
      ],
    }),
    getProjectScript: async (scriptName: string) => ({
      name: scriptName,
      path: `scripts/${scriptName}`,
      content: `content for ${scriptName}`,
    }),
    saveProjectScript: async () => ({ success: true, saved_at: '2026-05-27T09:00:00' }),
    saveProjectRevision: async () => ({
      ok: true,
      latest_revision_id: 'r0002',
      revision: {
        revision_id: 'r0002',
        project_name: 'Chair',
        gsm_name: 'Chair',
        created_at: '2026-05-27T09:01:00Z',
        message: 'saved from test',
        file_count: 3,
        trigger: 'manual',
        intent: '',
        user_instruction: '',
        changed_files: [],
        parent_revision_id: 'r0001',
        compile: {},
        explanation: '',
        is_latest: true,
      },
    }),
    restoreProjectRevision: async (revisionId: string) => ({
      ok: true,
      restored_revision_id: revisionId,
      latest_revision_id: 'r0003',
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: ['restored'] },
      warnings: ['restored'],
    }),
    mockCompile: async () => ({ success: true, mode: 'mock', issues: [], duration_ms: 12 }),
    revealArtifact: async (path = '') => ({ ok: true, path: path || '/workspace/output/Chair.gsm' }),
    updateCompilerSettings: async () => ({ ok: false, error: 'not loaded' }),
    fetchRuntimeSettings: async () => ({
      ok: true,
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
      llm: {
        model: 'glm-4-flash',
        models: ['glm-4-flash', 'deepseek-chat'],
        api_key: '',
        api_base: '',
        max_retries: 5,
        assistant_settings: '',
      },
    }),
    updateLlmSettings: async (settings) => ({ ok: true, llm: settings }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
    applyParameters: async (parameters: Record<string, unknown>) => ({
      ok: true,
      changed: parameters,
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '2.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    addProjectParameter: async (parameter) => ({
      ok: true,
      added: { name: parameter.name, type_tag: parameter.type_tag, description: parameter.description ?? '', value: String(parameter.value), is_fixed: false },
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [
        { name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true },
        { name: parameter.name, type_tag: parameter.type_tag, description: parameter.description ?? '', value: String(parameter.value), is_fixed: false },
      ],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    updateProjectParameter: async (parameter) => ({
      ok: true,
      updated: {
        name: parameter.new_name ?? parameter.name,
        type_tag: parameter.type_tag ?? 'Length',
        description: parameter.description ?? '',
        value: String(parameter.value ?? '1.0'),
        is_fixed: false,
      },
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [
        { name: parameter.new_name ?? parameter.name, type_tag: parameter.type_tag ?? 'Length', description: parameter.description ?? '', value: String(parameter.value ?? '1.0'), is_fixed: false },
      ],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    deleteProjectParameter: async (name) => ({
      ok: true,
      deleted: name,
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    validateProjectParameters: async () => ({ ok: true, issues: [] }),
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

test('applyDraftParameters applies changes and runs mock diagnostics', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)
  await store.getState().applyDraftParameters()

  expect(store.getState().draftParameters).toEqual({})
  expect(store.getState().parameters[0].value).toBe('2.0')
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().compileLog[0]).toContain('Mock compile passed')
  expect(store.getState().applying).toBe(false)
})

test('addProjectParameter refreshes parameters, paramlist cache, preview, and diagnostics', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  await store.getState().openScript('paramlist.xml')
  const ok = await store.getState().addProjectParameter({
    name: 'seat_height',
    type_tag: 'Length',
    value: 0.45,
    description: 'Seat height',
  })

  expect(ok).toBe(true)
  expect(store.getState().parameters.map((parameter) => parameter.name)).toContain('seat_height')
  expect(store.getState().scriptContents['paramlist.xml']).toBe('content for paramlist.xml')
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().compileLog[0]).toContain('Mock compile passed')
  expect(store.getState().applying).toBe(false)
})

test('addProjectParameter stores api errors in lastError', async () => {
  const store = createWorkbenchStore(makeApi({
    addProjectParameter: async () => ({
      ok: false,
      error: 'Parameter already exists',
      project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
  }))

  await store.getState().load()
  const ok = await store.getState().addProjectParameter({ name: 'A', type_tag: 'Length', value: 1 })

  expect(ok).toBe(false)
  expect(store.getState().lastError).toBe('Parameter already exists')
  expect(store.getState().applying).toBe(false)
})

test('validateProjectParameters stores parameter issues', async () => {
  const store = createWorkbenchStore(makeApi({
    validateProjectParameters: async () => ({
      ok: true,
      issues: ["Length parameter 'width_mm' should not include unit markers"],
    }),
  }))

  await store.getState().validateProjectParameters()

  expect(store.getState().parameterIssues).toEqual(["Length parameter 'width_mm' should not include unit markers"])
})

test('updateProjectParameter refreshes parameter metadata and diagnostics', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  const ok = await store.getState().updateProjectParameter({
    name: 'A',
    new_name: 'seat_width',
    type_tag: 'Length',
    value: 1.2,
    description: 'Seat width',
  })

  expect(ok).toBe(true)
  expect(store.getState().parameters[0].name).toBe('seat_width')
  expect(store.getState().parameters[0].description).toBe('Seat width')
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().applying).toBe(false)
})

test('deleteProjectParameter removes parameter and refreshes diagnostics', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  const ok = await store.getState().deleteProjectParameter('shelf_count')

  expect(ok).toBe(true)
  expect(store.getState().parameters).toEqual([])
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().applying).toBe(false)
})

test('resetDraftParameters discards unapplied parameter edits', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()
  await store.getState().setDraftParameter('A', 2)
  store.getState().resetDraftParameters()

  expect(store.getState().draftParameters).toEqual({})
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
  expect(store.getState().recentProjects).toEqual([{ path: '/workspace/Chair', exists: true }])
})

test('closes the current project and returns to demo state', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadProjectPath('/workspace/Chair')
  await store.getState().closeProject()

  expect(store.getState().project).toEqual({ name: 'Demo Bookshelf', source: 'demo' })
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().loading).toBe(false)
})

test('load fetches recent project list', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()

  expect(store.getState().recentProjects).toEqual([{ path: '/workspace/Chair', exists: true }])
})

test('load fetches revision history', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()

  expect(store.getState().latestRevisionId).toBe('r0001')
  expect(store.getState().revisions[0]?.message).toBe('stable')
})

test('saveRevision refreshes revision history and records a log entry', async () => {
  const store = createWorkbenchStore(
    makeApi({
      listProjectRevisions: async () => ({
        ok: true,
        latest_revision_id: 'r0002',
        revisions: [
          {
            revision_id: 'r0002',
            project_name: 'Chair',
            gsm_name: 'Chair',
            created_at: '2026-05-27T09:01:00Z',
            message: 'manual save',
            file_count: 3,
            trigger: 'manual',
            intent: '',
            user_instruction: '',
            changed_files: [],
            parent_revision_id: 'r0001',
            compile: {},
            explanation: '',
            is_latest: true,
          },
        ],
      }),
    }),
  )

  await store.getState().saveRevision('manual save')

  expect(store.getState().latestRevisionId).toBe('r0002')
  expect(store.getState().compileLog[0]).toBe('Saved revision r0002')
  expect(store.getState().revisionLoading).toBe(false)
})

test('restoreRevision refreshes project scripts and revision history', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().restoreRevision('r0001')

  expect(store.getState().warnings).toEqual(['restored'])
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().scriptContents['3d.gdl']).toBe('content for 3d.gdl')
  expect(store.getState().compileLog[0]).toBe('Restored revision r0001')
  expect(store.getState().revisionLoading).toBe(false)
})

test('failed project path load keeps current project and records an error', async () => {
  const store = createWorkbenchStore(
    makeApi({
      loadProjectPath: async () => ({
        ok: false,
        error: 'HSF directory not found',
        project: { name: 'Fallback' },
        parameters: [],
        preview: { meshes: [], wires: [] },
        warnings: [],
      }),
    }),
  )

  await store.getState().load()
  await store.getState().loadProjectPath('/missing/project')

  expect(store.getState().project?.name).toBe('Chair')
  expect(store.getState().lastError).toBe('HSF directory not found')
  expect(store.getState().loading).toBe(false)
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
        compiler: { mode: 'mock', converter_path: '', output_dir: '' },
      }),
    }),
  )

  await store.getState().browseProjectDirectory()

  expect(store.getState().project).toEqual({ name: 'Browsed', source: 'hsf', path: '/workspace/Browsed' })
  expect(store.getState().parameters[0].value).toBe('1.5')
  expect(store.getState().draftParameters).toEqual({})
})

test('imports a single GDL file as a project and opens its default script', async () => {
  const store = createWorkbenchStore(
    makeApi({
      importGdlFile: async () => ({
        ok: true,
        project: { name: 'spiral stair', source: 'hsf', path: '/workspace/spiral stair' },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: ['imported'] },
        warnings: ['imported'],
        compiler: { mode: 'mock', converter_path: '', output_dir: '' },
      }),
      listRecentProjects: async () => ({
        ok: true,
        projects: [{ path: '/workspace/spiral stair', exists: true }],
      }),
      getProjectScript: async (scriptName: string) => ({
        name: scriptName,
        path: `scripts/${scriptName}`,
        content: 'BLOCK A, B, ZZYZX\n',
      }),
    }),
  )

  await store.getState().importGdlFile('/input/spiral stair.gdl')

  expect(store.getState().project).toEqual({ name: 'spiral stair', source: 'hsf', path: '/workspace/spiral stair' })
  expect(store.getState().warnings).toEqual(['imported'])
  expect(store.getState().recentProjects).toEqual([{ path: '/workspace/spiral stair', exists: true }])
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK A, B, ZZYZX\n')
  expect(store.getState().loading).toBe(false)
})

test('failed GDL import keeps the current project and records an error', async () => {
  const store = createWorkbenchStore(
    makeApi({
      importGdlFile: async () => ({
        ok: false,
        error: 'Unsupported file type: .txt',
        project: { name: 'Fallback' },
        parameters: [],
        preview: { meshes: [], wires: [] },
        warnings: [],
      }),
    }),
  )

  await store.getState().load()
  await store.getState().importGdlFile('/input/notes.txt')

  expect(store.getState().project?.name).toBe('Chair')
  expect(store.getState().lastError).toBe('Unsupported file type: .txt')
  expect(store.getState().loading).toBe(false)
})

test('loads compiler settings from snapshot', async () => {
  const store = createWorkbenchStore(
    makeApi({
      fetchSnapshot: async () => ({
        project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
      }),
    }),
  )

  await store.getState().load()

  expect(store.getState().compilerSettings).toEqual({
    mode: 'lp',
    converter_path: '/Applications/LP_XMLConverter',
    output_dir: '',
  })
})

test('load fetches scripts and opens 3d.gdl by default', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().load()

  expect(store.getState().scripts.map((script) => script.name)).toEqual(['3d.gdl', '2d.gdl', 'paramlist.xml'])
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

test('openScript keeps current script active when content cannot be loaded', async () => {
  const store = createWorkbenchStore(
    makeApi({
      getProjectScript: async (scriptName: string) =>
        scriptName === '2d.gdl'
          ? null
          : {
              name: scriptName,
              path: `scripts/${scriptName}`,
              content: `content for ${scriptName}`,
            },
    }),
  )

  await store.getState().load()
  await store.getState().openScript('2d.gdl')

  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().lastError).toBe('Failed to open script: 2d.gdl')
  expect(store.getState().scriptLoading).toBe(false)
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
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().compileLog[0]).toContain('Mock compile passed')
  expect(store.getState().compileLog.some((entry) => entry.includes('Saved 3d.gdl'))).toBe(true)
})

test('saveActiveScript records save failures without clearing dirty state', async () => {
  const store = createWorkbenchStore(
    makeApi({
      saveProjectScript: async () => ({ ok: false, success: false, saved_at: '', error: 'Disk is read-only' }),
    }),
  )

  await store.getState().load()
  store.getState().updateActiveScriptContent('changed 3d content')
  await store.getState().saveActiveScript()

  expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
  expect(store.getState().lastError).toBe('Disk is read-only')
  expect(store.getState().scriptSaving).toBe(false)
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

test('loadPreview2D stores plan preview geometry', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadPreview2D()

  expect(store.getState().preview2d?.lines).toEqual([{ from: [0, 0], to: [1, 1] }])
})

test('updates compiler settings through the API', async () => {
  const store = createWorkbenchStore(
    makeApi({
      updateCompilerSettings: async (settings) => ({ ok: true, compiler: settings }),
    }),
  )

  await store.getState().setCompilerSettings({ mode: 'lp', converter_path: '/converter', output_dir: '' })

  expect(store.getState().compilerSettings).toEqual({ mode: 'lp', converter_path: '/converter', output_dir: '' })
})

test('updates llm settings through the API', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().setLlmSettings({
    model: 'deepseek-chat',
    models: ['glm-4-flash', 'deepseek-chat'],
    api_key: 'deepseek-key',
    api_base: 'https://api.deepseek.com/v1',
    max_retries: 6,
    assistant_settings: '先解释再改',
  })

  expect(store.getState().llmSettings).toEqual({
    model: 'deepseek-chat',
    models: ['glm-4-flash', 'deepseek-chat'],
    api_key: 'deepseek-key',
    api_base: 'https://api.deepseek.com/v1',
    max_retries: 6,
    assistant_settings: '先解释再改',
  })
})

test('reloadRuntimeSettings refreshes compiler and llm settings', async () => {
  const store = createWorkbenchStore(
    makeApi({
      fetchRuntimeSettings: async () => ({
        ok: true,
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
        llm: {
          model: 'gpt-4.1-mini',
          models: ['gpt-4.1-mini'],
          api_key: 'openai-key',
          api_base: 'https://api.openai.com/v1',
          max_retries: 4,
          assistant_settings: 'short answers',
        },
      }),
    }),
  )

  await store.getState().reloadRuntimeSettings()

  expect(store.getState().compilerSettings.mode).toBe('lp')
  expect(store.getState().llmSettings.model).toBe('gpt-4.1-mini')
  expect(store.getState().llmSettings.assistant_settings).toBe('short answers')
})

test('browses for LP_XMLConverter and stores compiler settings', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseCompilerFile: async () => ({
        ok: true,
        path: '/Applications/LP_XMLConverter',
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
      }),
    }),
  )

  await store.getState().browseCompilerFile()

  expect(store.getState().compilerSettings).toEqual({
    mode: 'lp',
    converter_path: '/Applications/LP_XMLConverter',
    output_dir: '',
  })
})

test('browses for compile output directory and stores compiler settings', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseOutputDirectory: async () => ({
        ok: true,
        path: '/workspace/output',
        compiler: { mode: 'mock', converter_path: '', output_dir: '/workspace/output' },
      }),
    }),
  )

  await store.getState().browseOutputDirectory()

  expect(store.getState().compilerSettings).toEqual({
    mode: 'mock',
    converter_path: '',
    output_dir: '/workspace/output',
  })
})

test('records compile results in the workbench log', async () => {
  let receivedOutputDir = ''
  const store = createWorkbenchStore(
    makeApi({
      updateCompilerSettings: async (settings) => ({ ok: true, compiler: settings }),
      compileProject: async (outputDir = '') => {
        receivedOutputDir = outputDir
        return {
          ok: true,
          compile: {
            success: true,
            mode: 'lp',
            output_path: '/workspace/output/Chair.gsm',
            stdout: 'compiled',
            stderr: '',
            errors: [],
            warnings: [],
            gsm_size_bytes: 4096,
            parameter_count: 3,
          },
        }
      },
    }),
  )

  await store.getState().setCompilerSettings({ mode: 'mock', converter_path: '', output_dir: '/workspace/output' })
  await store.getState().compileCurrentProject()

  expect(receivedOutputDir).toBe('/workspace/output')
  expect(store.getState().compileLog).toEqual(['LP compile passed: /workspace/output/Chair.gsm'])
  expect(store.getState().mockCompileResult).toEqual({
    success: true,
    mode: 'lp',
    issues: [],
    duration_ms: 0,
    output_path: '/workspace/output/Chair.gsm',
    gsm_size_bytes: 4096,
    parameter_count: 3,
    error: undefined,
  })
  expect(store.getState().compiling).toBe(false)
})

test('mock compile uses configured output directory', async () => {
  let receivedOutputDir = ''
  const store = createWorkbenchStore(
    makeApi({
      updateCompilerSettings: async (settings) => ({ ok: true, compiler: settings }),
      mockCompile: async (outputDir = '') => {
        receivedOutputDir = outputDir
        return { success: true, mode: 'mock', issues: [], duration_ms: 12 }
      },
    }),
  )

  await store.getState().setCompilerSettings({ mode: 'mock', converter_path: '', output_dir: '/workspace/output' })
  await store.getState().runMockCompile()

  expect(receivedOutputDir).toBe('/workspace/output')
})

test('revealCompileOutput records revealed artifact path', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().revealCompileOutput('/workspace/output/Chair.gsm')

  expect(store.getState().compileLog[0]).toBe('Revealed /workspace/output/Chair.gsm')
})

test('revealCompileOutput stores api errors in lastError', async () => {
  const store = createWorkbenchStore(
    makeApi({
      revealArtifact: async () => ({ ok: false, error: 'Artifact not found' }),
    }),
  )

  await store.getState().revealCompileOutput('/missing.gsm')

  expect(store.getState().lastError).toBe('Artifact not found')
})

test('records real compile errors in diagnostics', async () => {
  const store = createWorkbenchStore(
    makeApi({
      compileProject: async () => ({
        ok: false,
        error: 'LP_XMLConverter not found',
        compile: {
          success: false,
          mode: 'lp',
          output_path: '',
          stdout: '',
          stderr: 'LP_XMLConverter not found',
          errors: ['LP_XMLConverter not found'],
          warnings: ['no gsm written'],
        },
      }),
    }),
  )

  await store.getState().compileCurrentProject()

  expect(store.getState().mockCompileResult?.success).toBe(false)
  expect(store.getState().mockCompileResult?.mode).toBe('lp')
  expect(store.getState().mockCompileResult?.issues).toEqual([
    { severity: 'error', script: '', line: null, message: 'LP_XMLConverter not found' },
    { severity: 'warning', script: '', line: null, message: 'no gsm written' },
  ])
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

test('sets active rail panel', () => {
  const store = createWorkbenchStore(makeApi())

  store.getState().setActiveRailPanel('ai')

  expect(store.getState().activeRailPanel).toBe('ai')
})

test('createProjectFromPrompt loads the created project and records assistant reply', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().createProjectFromPrompt('做一个书架')

  expect(store.getState().project).toEqual({ name: 'Bookshelf', source: 'hsf', path: '/workspace/Bookshelf' })
  expect(store.getState().warnings).toEqual(['created'])
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '做一个书架' },
    { role: 'assistant', content: 'created bookshelf' },
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
      getProjectScript: async (scriptName: string) => ({
        name: scriptName,
        path: `scripts/${scriptName}`,
        content: `updated content for ${scriptName}`,
      }),
    }),
  )

  await store.getState().load()
  await store.getState().generateAssistantChanges('加一块层板')

  expect(store.getState().preview?.meshes[0]?.name).toBe('changed')
  expect(store.getState().warnings).toEqual(['preview refreshed'])
  expect(store.getState().scriptContents['3d.gdl']).toBe('updated content for 3d.gdl')
  expect(store.getState().scriptContents['2d.gdl']).toBe('updated content for 2d.gdl')
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().mockCompileResult?.success).toBe(true)
  expect(store.getState().compileLog[0]).toContain('Mock compile passed')
  expect(store.getState().assistantMessages.at(-1)).toEqual({
    role: 'assistant',
    content: 'changed 加一块层板\n\nChanged files: scripts/3d.gdl',
  })
})
