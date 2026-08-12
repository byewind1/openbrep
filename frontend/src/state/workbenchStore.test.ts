import type { AssistantStreamEvent } from '../api/types'
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
    workspaceInit: async (path: string) => ({
      ok: true,
      workspace: path,
      project_count: 0,
      projects: [],
    }),
    workspaceOpen: async (path: string) => ({
      ok: true,
      workspace: path,
      project_count: 1,
      projects: [
        {
          name: 'Chair',
          path: `${path}/hsf/Chair`,
          parameter_count: 3,
          scripts_present: ['SCRIPT_3D'],
          latest_revision_id: null,
          origin: null,
          artifact_count: 0,
          active: false,
        },
      ],
    }),
    workspaceClose: async () => ({ ok: true, workspace: null }),
    workspaceScan: async () => ({
      ok: true,
      workspace: '/workspace',
      project_count: 1,
      projects: [
        {
          name: 'Chair',
          path: '/workspace/hsf/Chair',
          parameter_count: 3,
          scripts_present: ['SCRIPT_3D'],
          latest_revision_id: 'r0001',
          origin: { imported_from: '/workspace/sources/chair.gdl', imported_kind: 'gdl', imported_at: '2026-08-06T00:00:00' },
          artifact_count: 2,
          active: true,
        },
      ],
    }),
    trashWorkspaceProject: async (path: string) => ({ ok: true, trashed_to: `/workspace/.openbrep/trash/${path.split('/').pop()}`, workspace: '/workspace' }),
    workspaceSearch: async (query: string) => ({
      ok: true,
      query,
      hits: [
        { project: 'Chair', location: 'scripts/3d.gdl', line: 2, snippet: 'CYLIND 1, 1' },
      ],
      hit_count: 1,
      workspace: '/workspace',
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
      workspace: {
        path: '/workspace',
        project_count: 1,
        projects: [
          {
            name: 'Chair',
            path: '/workspace/hsf/Chair',
            parameter_count: 3,
            scripts_present: ['SCRIPT_3D'],
            latest_revision_id: null,
            origin: null,
            artifact_count: 0,
            active: true,
          },
        ],
      },
    }),
    importGdlFile: async () => ({
      ok: true,
      project: { name: 'spiral stair', source: 'hsf', path: '/workspace/spiral stair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: ['imported'] },
      warnings: ['imported'],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    importGsmFile: async () => ({
      ok: true,
      project: { name: 'imported shelf', source: 'hsf', path: '/workspace/imported shelf' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: ['decompiled'] },
      warnings: ['decompiled'],
      compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
    }),
    importBlenderScript: async () => ({
      ok: true,
      project: { name: 'blender box', source: 'blender_import', path: '/workspace/blender box' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    exportHsfProject: async () => ({
      ok: true,
      saved_to: '/exports/Chair',
      project: { name: 'Chair', source: 'hsf', path: '/exports/Chair' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: ['saved'] },
      warnings: ['saved'],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    newProject: async () => ({
      ok: true,
      project: { name: 'Untitled GDL Object', source: 'untitled' },
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
      compiler: { mode: 'mock', converter_path: '', output_dir: '' },
    }),
    saveProject: async () => ({
      ok: false,
      needs_save_as: true,
      error: 'Project has no HSF path. Use Save As HSF.',
      project: { name: 'Untitled GDL Object', source: 'untitled' },
      parameters: [],
      preview: { meshes: [], wires: [], warnings: [] },
      warnings: [],
    }),
    closeProject: async () => ({
      ok: true,
      project: null,
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
    fetchProjectGitStatus: async () => ({
      ok: true,
      git: { enabled: false, initialized: false, dirty: false, changes: [], last_commit: '' },
    }),
    initializeProjectGit: async () => ({
      ok: true,
      git: { enabled: true, initialized: true, dirty: true, changes: ['A  scripts/3d.gdl'], last_commit: '' },
    }),
    updateProjectGitSettings: async (enabled) => ({
      ok: true,
      git: { enabled, initialized: true, dirty: false, changes: [], last_commit: 'abc1234' },
    }),
    commitProjectGit: async () => ({
      ok: true,
      git: { enabled: true, initialized: true, dirty: false, changes: [], last_commit: 'def5678' },
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
    fetchConfigRevision: async () => ({ ok: true, revision: 'rev-0' }),
    fetchKnowledgeStatus: async () => ({
      ok: true,
      has_pro: false,
      free_doc_count: 0,
      pro_doc_count: 0,
      pro_doc_names: [],
      pro_dir: '',
      pro_dir_exists: false,
    }),
    reloadKnowledge: async () => ({
      ok: true,
      has_pro: false,
      free_doc_count: 0,
      pro_doc_count: 0,
      pro_doc_names: [],
      pro_dir: '',
      pro_dir_exists: false,
    }),
    openConfig: async () => ({ ok: true }),
    testLlmConnection: async () => ({ ok: true, message: 'LLM connection OK', model: 'glm-4-flash', duration_ms: 12 }),
    askAssistant: async () => ({ ok: false, error: 'not loaded' }),
    listAssistantHistory: async () => ({ ok: true, messages: [] }),
    saveAssistantHistory: async (messages) => ({ ok: true, count: messages.length }),
    clearAssistantHistory: async () => ({ ok: true, count: 0 }),
    importAssistantHistory: async (sourcePath) => ({ ok: true, imported: 0, source_name: sourcePath.split('/').pop() ?? 'source' }),
    distillAssistantHistory: async () => ({ ok: false, error: 'not loaded' }),
    extractAssistantCodeBlocks: async () => ({ ok: true, blocks: [] }),
    fetchMemoryStatus: async () => ({
      ok: true,
      memory: {
        memory_root: '/workspace/Chair/.openbrep/memory',
        chat_count: 0,
        lesson_count: 0,
        has_learned_skill: false,
        total_bytes: 0,
      },
    }),
    clearProjectMemory: async () => ({
      ok: true,
      before: {
        memory_root: '/workspace/Chair/.openbrep/memory',
        chat_count: 0,
        lesson_count: 0,
        has_learned_skill: false,
        total_bytes: 0,
      },
    }),
    fetchMemoryLessons: async () => ({ ok: true, lessons: [] }),
    summarizeProjectMemory: async () => ({
      ok: true,
      summary: {
        ok: true,
        lesson_count: 0,
        path: '/workspace/Chair/.openbrep/memory/skills/learned_skill.md',
        message: '已整理 0 条错题约束，方式：规则整理',
      },
      skill: '',
    }),
    deleteMemoryLesson: async (fingerprint: string) => ({ ok: true, deleted: fingerprint, remaining_count: 0 }),
    ignoreMemoryLesson: async (fingerprint: string) => ({ ok: true, ignored: fingerprint, remaining_count: 0 }),
    updateMemoryLesson: async (fingerprint: string, updates) => ({
      ok: true,
      lesson: {
        fingerprint,
        category: updates.category ?? 'general_compile_error',
        summary: updates.summary ?? '',
        guidance: updates.guidance ?? '',
        example: updates.example ?? '',
        count: 1,
        first_seen: '2026-05-30T10:00:00Z',
        last_seen: '2026-05-30T10:00:00Z',
        source: 'test',
        project_name: 'Chair',
        raw_excerpt: '',
      },
    }),
    generateWithAssistant: async () => ({ ok: false, error: 'not loaded' }),
    generateWithAssistantStream: async () => ({ ok: false, error: 'not loaded' }),
    requestModifyPlan: async () => ({ ok: false, error: 'not loaded' }),
    confirmModifyPlan: async () => ({ ok: false, error: 'not loaded' }),
    confirmSkillProposal: async () => ({ ok: false, error: 'not loaded' }),
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
    fetchTapirStatus: async () => ({
      ok: true,
      tapir: {
        import_ok: false,
        available: false,
        archicad_connected: false,
        tapir_available: false,
        version: '',
        message: 'Tapir bridge 未导入',
        selected_guids: [],
        selected_details: [],
        selected_params: [],
        param_edits: {},
        last_error: '',
        last_sync_at: '',
      },
    }),
    syncTapirSelection: async () => ({
      ok: true,
      message: '已同步 1 个对象',
      tapir: {
        import_ok: true,
        available: true,
        archicad_connected: true,
        tapir_available: true,
        version: '/Applications/GRAPHISOFT/Archicad',
        message: 'Archicad + Tapir 已连接',
        selected_guids: ['GUID-1'],
        selected_details: [{ guid: 'GUID-1', type: 'Object', name: 'Chair' }],
        selected_params: [],
        param_edits: {},
        last_error: '',
        last_sync_at: '2026-06-01 10:00',
      },
    }),
    highlightTapirSelection: async () => ({ ok: false, message: '请先同步选中对象' }),
    loadTapirParameters: async () => ({
      ok: true,
      message: '已读取 1 个对象参数',
      tapir: {
        import_ok: true,
        available: true,
        archicad_connected: true,
        tapir_available: true,
        version: '/Applications/GRAPHISOFT/Archicad',
        message: 'Archicad + Tapir 已连接',
        selected_guids: ['GUID-1'],
        selected_details: [{ guid: 'GUID-1', type: 'Object', name: 'Chair' }],
        selected_params: [{ guid: 'GUID-1', gdlParameters: [{ name: 'A', value: 1 }] }],
        param_edits: { 'GUID-1::A': '1' },
        last_error: '',
        last_sync_at: '2026-06-01 10:00',
      },
    }),
    applyTapirParameterEdits: async () => ({ ok: true, message: '参数已应用到 1 个对象' }),
    reloadTapirLibraries: async () => ({ ok: false, message: 'Archicad 未运行或 Tapir 未安装' }),
    updateLlmModel: async () => ({ ok: true }),
    updateLlmApiKey: async () => ({ ok: true }),
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

test('tapir actions refresh status and store selected Archicad elements', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().refreshTapirStatus()
  expect(store.getState().tapirStatus?.message).toBe('Tapir bridge 未导入')

  await store.getState().syncTapirSelection()
  expect(store.getState().tapirStatus?.selected_guids).toEqual(['GUID-1'])
  expect(store.getState().tapirStatus?.selected_details[0]?.name).toBe('Chair')
  expect(store.getState().compileLog[0]).toBe('已同步 1 个对象')
})

test('tapir parameter actions keep edit state and record writeback result', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadTapirParameters()
  expect(store.getState().tapirStatus?.param_edits).toEqual({ 'GUID-1::A': '1' })

  await store.getState().applyTapirParameters()
  expect(store.getState().compileLog[0]).toBe('参数已应用到 1 个对象')
  expect(store.getState().tapirBusy).toBe(false)
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

test('closes the current project and returns to empty workbench state', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadProjectPath('/workspace/Chair')
  await store.getState().closeProject()

  expect(store.getState().project).toBeNull()
  expect(store.getState().activeScriptName).toBeNull()
  expect(store.getState().loading).toBe(false)
})

test('newProject loads an untitled project and refreshes project resources', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().newProject()

  expect(store.getState().project?.name).toBe('Untitled GDL Object')
  expect(store.getState().project?.source).toBe('untitled')
  expect(store.getState().scripts.length).toBeGreaterThan(0)
})

test('saveProject reports save-as requirement for unsaved projects', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().saveProject()

  expect(store.getState().lastError).toContain('Save As HSF')
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

  await store.getState().load()  // 先加载项目：无项目时 revisions 刷新按修复 3 会被跳过
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

test('imports a GSM file as a decompiled HSF project and opens its default script', async () => {
  const store = createWorkbenchStore(
    makeApi({
      importGsmFile: async () => ({
        ok: true,
        project: { name: 'imported shelf', source: 'hsf', path: '/workspace/imported shelf' },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: ['decompiled'] },
        warnings: ['decompiled'],
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
      }),
      listRecentProjects: async () => ({
        ok: true,
        projects: [{ path: '/workspace/imported shelf', exists: true }],
      }),
      getProjectScript: async (scriptName: string) => ({
        name: scriptName,
        path: `scripts/${scriptName}`,
        content: 'BLOCK A, B, ZZYZX\n',
      }),
    }),
  )

  await store.getState().importGsmFile('/input/imported shelf.gsm')

  expect(store.getState().project).toEqual({ name: 'imported shelf', source: 'hsf', path: '/workspace/imported shelf' })
  expect(store.getState().warnings).toEqual(['decompiled'])
  expect(store.getState().compilerSettings.mode).toBe('lp')
  expect(store.getState().recentProjects).toEqual([{ path: '/workspace/imported shelf', exists: true }])
  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK A, B, ZZYZX\n')
})

test('exports current HSF project and refreshes workspace state', async () => {
  const store = createWorkbenchStore(
    makeApi({
      exportHsfProject: async (parentDir = '', name = '') => ({
        ok: true,
        saved_to: `${parentDir}/${name}`,
        project: { name, source: 'hsf', path: `${parentDir}/${name}` },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: ['saved'] },
        warnings: ['saved'],
        compiler: { mode: 'mock', converter_path: '', output_dir: '' },
      }),
      listRecentProjects: async () => ({
        ok: true,
        projects: [{ path: '/exports/ExportedShelf', exists: true }],
      }),
    }),
  )

  await store.getState().exportHsfProject('/exports', 'ExportedShelf')

  expect(store.getState().project).toEqual({ name: 'ExportedShelf', source: 'hsf', path: '/exports/ExportedShelf' })
  expect(store.getState().warnings).toEqual(['saved'])
  expect(store.getState().recentProjects).toEqual([{ path: '/exports/ExportedShelf', exists: true }])
  expect(store.getState().compileLog[0]).toBe('Saved HSF source: /exports/ExportedShelf')
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

test('setPreviewQuality updates state and refetches 3D preview with the tier', async () => {
  const calls: unknown[] = []
  const store = createWorkbenchStore(
    makeApi({
      fetchPreview: async (_parameters, _scripts, quality) => {
        calls.push(quality)
        return { meshes: [{ name: 'quality-block', vertices: [], faces: [] }], wires: [], warnings: [] }
      },
    }),
  )

  expect(store.getState().previewQuality).toBe('fast')
  await store.getState().setPreviewQuality('accurate')

  expect(store.getState().previewQuality).toBe('accurate')
  expect(calls).toEqual(['accurate'])
  expect(store.getState().preview?.meshes[0]?.name).toBe('quality-block')
})

test('setPreviewQuality refreshes 2D preview too when the 2D tab is active', async () => {
  const preview3d: unknown[] = []
  const preview2d: unknown[] = []
  const store = createWorkbenchStore(
    makeApi({
      fetchPreview: async (_p, _s, quality) => {
        preview3d.push(quality)
        return { meshes: [], wires: [], warnings: [] }
      },
      fetchPreview2D: async (_p, _s, quality) => {
        preview2d.push(quality)
        return { lines: [], polygons: [], circles: [], arcs: [], warnings: [] }
      },
    }),
  )
  store.setState({ activeRailPanel: '2d' })

  await store.getState().setPreviewQuality('accurate')

  expect(preview3d).toEqual(['accurate'])
  expect(preview2d).toEqual(['accurate'])
})

test('loadPreview3D carries the current store quality', async () => {
  const calls: unknown[] = []
  const store = createWorkbenchStore(
    makeApi({
      fetchPreview: async (_p, _s, quality) => {
        calls.push(quality)
        return { meshes: [], wires: [], warnings: [] }
      },
    }),
  )
  store.setState({ previewQuality: 'accurate' })

  await store.getState().loadPreview3D()

  expect(calls).toEqual(['accurate'])
})

test('debounced draft preview carries the current quality tier', async () => {
  vi.useFakeTimers()
  try {
    const calls: unknown[] = []
    const store = createWorkbenchStore(
      makeApi({
        fetchPreview: async (_p, _s, quality) => {
          calls.push(quality)
          return { meshes: [], wires: [], warnings: [] }
        },
      }),
    )
    store.setState({ previewQuality: 'accurate' })

    await store.getState().setDraftParameter('A', 1.1)
    await vi.advanceTimersByTimeAsync(250)

    expect(calls).toEqual(['accurate'])
  } finally {
    vi.useRealTimers()
  }
})

test('loadPreview2D stores plan preview geometry', async () => {
  const store = createWorkbenchStore(makeApi())

  await store.getState().loadPreview2D()

  expect(store.getState().preview2d?.lines).toEqual([{ from: [0, 0], to: [1, 1] }])
})

test('loadPreview3D verifies dirty editor buffers without saving first', async () => {
  const calls: Array<{ parameters: Record<string, unknown>; scripts?: Record<string, string> }> = []
  const store = createWorkbenchStore(
    makeApi({
      fetchPreview: async (parameters, scripts) => {
        calls.push({ parameters, scripts })
        return {
          meshes: [{ name: 'dirty-block', vertices: [], faces: [] }],
          wires: [],
          warnings: ['preview uses editor buffer'],
          verification: { source: 'editor_buffer', script_overrides: ['3d.gdl'] },
        }
      },
    }),
  )
  store.setState({
    activeScriptName: '3d.gdl',
    scriptContents: { '3d.gdl': 'BLOCK 2, 1, 1', '2d.gdl': 'LINE2 0, 0, 1, 1' },
    dirtyScripts: { '3d.gdl': true, '2d.gdl': false },
  })

  await store.getState().loadPreview3D()

  expect(calls).toEqual([{ parameters: {}, scripts: { '3d.gdl': 'BLOCK 2, 1, 1' } }])
  expect(store.getState().preview?.meshes[0]?.name).toBe('dirty-block')
  expect(store.getState().warnings).toEqual(['preview uses editor buffer'])
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

test('opens config file via openConfig action', async () => {
  let called = false
  const store = createWorkbenchStore(makeApi({ openConfig: async () => { called = true; return { ok: true } } }))
  await store.getState().openConfig()
  expect(called).toBe(true)
})

test('tests llm connection using saved config', async () => {
  let callCount = 0
  const store = createWorkbenchStore(
    makeApi({
      testLlmConnection: async () => {
        callCount++
        return { ok: true, message: 'LLM connection OK', model: 'deepseek-chat', duration_ms: 34 }
      },
    }),
  )

  const result = await store.getState().testLlmConnection()

  expect(callCount).toBe(1)
  expect(result.ok).toBe(true)
  expect(result.duration_ms).toBe(34)
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

test('pollConfigRevision records the revision on first poll without reloading', async () => {
  const fetchRuntimeSettings = vi.fn(async () => ({ ok: true }))
  const store = createWorkbenchStore(
    makeApi({
      fetchConfigRevision: async () => ({ ok: true, revision: 'rev-1' }),
      fetchRuntimeSettings,
    }),
  )

  await store.getState().pollConfigRevision()

  expect(store.getState().configRevision).toBe('rev-1')
  expect(fetchRuntimeSettings).not.toHaveBeenCalled()
})

test('pollConfigRevision refreshes llm settings but not compiler when config file changes', async () => {
  let revision = 'rev-1'
  const store = createWorkbenchStore(
    makeApi({
      fetchConfigRevision: async () => ({ ok: true, revision }),
      fetchRuntimeSettings: async () => ({
        ok: true,
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
        llm: {
          model: 'deepseek-chat',
          models: ['deepseek-chat'],
          api_key: 'deepseek-key',
          api_base: '',
          max_retries: 5,
          assistant_settings: '',
        },
      }),
    }),
  )

  await store.getState().pollConfigRevision()
  expect(store.getState().llmSettings.model).toBe('glm-4-flash') // untouched yet

  // Simulate an external edit of config.toml.
  revision = 'rev-2'
  await store.getState().pollConfigRevision()

  expect(store.getState().configRevision).toBe('rev-2')
  expect(store.getState().llmSettings.model).toBe('deepseek-chat')
  expect(store.getState().llmSettings.api_key).toBe('deepseek-key')
  // Compiler settings must not be clobbered by a background refresh.
  expect(store.getState().compilerSettings.mode).toBe('mock')
})

test('pollConfigRevision is a no-op when the revision is unchanged', async () => {
  const fetchRuntimeSettings = vi.fn(async () => ({ ok: true }))
  const store = createWorkbenchStore(
    makeApi({
      fetchConfigRevision: async () => ({ ok: true, revision: 'rev-1' }),
      fetchRuntimeSettings,
    }),
  )

  await store.getState().pollConfigRevision()
  await store.getState().pollConfigRevision()

  expect(fetchRuntimeSettings).not.toHaveBeenCalled()
})

test('browses for LP_XMLConverter and returns draft compiler settings without saving', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseCompilerFile: async () => ({
        ok: true,
        path: '/Applications/LP_XMLConverter',
        compiler: { mode: 'lp', converter_path: '/Applications/LP_XMLConverter', output_dir: '' },
      }),
    }),
  )

  const draft = await store.getState().browseCompilerFile()

  expect(draft).toEqual({
    mode: 'lp',
    converter_path: '/Applications/LP_XMLConverter',
    output_dir: '',
  })
  expect(store.getState().compilerSettings).toEqual({ mode: 'mock', converter_path: '', output_dir: '' })
})

test('browses for compile output directory and returns draft compiler settings without saving', async () => {
  const store = createWorkbenchStore(
    makeApi({
      chooseOutputDirectory: async () => ({
        ok: true,
        path: '/workspace/output',
        compiler: { mode: 'mock', converter_path: '', output_dir: '/workspace/output' },
      }),
    }),
  )

  const draft = await store.getState().browseOutputDirectory()

  expect(draft).toEqual({
    mode: 'mock',
    converter_path: '',
    output_dir: '/workspace/output',
  })
  expect(store.getState().compilerSettings).toEqual({ mode: 'mock', converter_path: '', output_dir: '' })
})

test('manages project git state from settings actions', async () => {
  const commits: string[] = []
  const store = createWorkbenchStore(
    makeApi({
      commitProjectGit: async (message) => {
        commits.push(message ?? '')
        return {
          ok: true,
          git: { enabled: true, initialized: true, dirty: false, changes: [], last_commit: 'c0ffee1' },
        }
      },
    }),
  )

  await store.getState().loadProjectGitStatus()
  expect(store.getState().gitStatus?.initialized).toBe(false)

  await store.getState().initializeProjectGit()
  expect(store.getState().gitStatus?.enabled).toBe(true)
  expect(store.getState().gitStatus?.dirty).toBe(true)

  await store.getState().setProjectGitEnabled(false)
  expect(store.getState().gitStatus?.enabled).toBe(false)

  await store.getState().commitProjectGit('Checkpoint before LP compile')
  expect(commits).toEqual(['Checkpoint before LP compile'])
  expect(store.getState().gitStatus?.last_commit).toBe('c0ffee1')
  expect(store.getState().compileLog[0]).toBe('Git commit: c0ffee1')
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

test('compile saves dirty script buffers before invoking compiler', async () => {
  const savedScripts: Array<{ name: string; content: string }> = []
  let compileCalled = false
  let previewCalled = false
  const store = createWorkbenchStore(
    makeApi({
      saveProjectScript: async (name, content) => {
        savedScripts.push({ name, content })
        return { success: true, saved_at: '2026-06-01T08:00:00Z' }
      },
      fetchPreview: async () => {
        previewCalled = true
        return {
          meshes: [{ name: 'compiled-source', vertices: [], faces: [] }],
          wires: [],
          warnings: ['saved preview'],
          verification: { source: 'saved', script_overrides: [] },
        }
      },
      compileProject: async () => {
        compileCalled = true
        return {
          ok: true,
          compile: {
            success: true,
            mode: 'lp',
            output_path: '/workspace/output/Chair.gsm',
            stdout: '',
            stderr: '',
            errors: [],
            warnings: [],
          },
        }
      },
    }),
  )
  store.setState({
    activeScriptName: '3d.gdl',
    scriptContents: { '3d.gdl': 'BLOCK 1, 2, 3', '2d.gdl': 'LINE2 0, 0, 1, 1' },
    dirtyScripts: { '3d.gdl': true, '2d.gdl': false },
  })

  await store.getState().compileCurrentProject()

  expect(savedScripts).toEqual([{ name: '3d.gdl', content: 'BLOCK 1, 2, 3' }])
  expect(compileCalled).toBe(true)
  expect(previewCalled).toBe(true)
  expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
  expect(store.getState().preview?.meshes[0]?.name).toBe('compiled-source')
  expect(store.getState().warnings).toEqual(['saved preview'])
  expect(store.getState().compileLog[0]).toBe('LP compile passed: /workspace/output/Chair.gsm')
  expect(store.getState().compileLog).toContain('Saved 3d.gdl')
})

test('compile stops when saving dirty scripts fails', async () => {
  let compileCalled = false
  const store = createWorkbenchStore(
    makeApi({
      saveProjectScript: async () => ({ success: false, saved_at: '', error: 'disk denied' }),
      compileProject: async () => {
        compileCalled = true
        return { ok: false, error: 'should not compile' }
      },
    }),
  )
  store.setState({
    scriptContents: { '3d.gdl': 'BLOCK 1, 2, 3' },
    dirtyScripts: { '3d.gdl': true },
  })

  await store.getState().compileCurrentProject()

  expect(compileCalled).toBe(false)
  expect(store.getState().lastError).toBe('disk denied')
  expect(store.getState().compiling).toBe(false)
  expect(store.getState().compileLog[0]).toBe('Compile stopped: disk denied')
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
  let savedMessages: Array<{ role: string; content: string }> = []
  const store = createWorkbenchStore(
    makeApi({
      saveAssistantHistory: async (messages) => {
        savedMessages = messages
        return { ok: true, count: messages.length }
      },
      askAssistant: async (message: string) => ({
        ok: true,
        assistant: { kind: 'explain_project', reply: `reply to ${message}` },
      }),
    }),
  )
  // 聊天历史存在 <项目>/.openbrep/ 下，需有项目打开才会持久化
  store.setState({ project: { name: 'Demo', source: 'hsf', path: '/workspace/Demo' } })

  await store.getState().sendAssistantMessage('解释这个构件')

  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '解释这个构件' },
    { role: 'assistant', content: 'reply to 解释这个构件' },
  ])
  expect(savedMessages).toEqual(store.getState().assistantMessages)
  expect(store.getState().assistantBusy).toBe(false)
})

test('纯聊天无项目时不持久化历史（不落盘）', async () => {
  let saveCalled = false
  const store = createWorkbenchStore(
    makeApi({
      saveAssistantHistory: async () => {
        saveCalled = true
        return { ok: true, count: 0 }
      },
      askAssistant: async (message: string) => ({
        ok: true,
        assistant: { kind: 'explain_project', reply: `reply to ${message}` },
      }),
    }),
  )

  await store.getState().sendAssistantMessage('随便聊聊')

  expect(store.getState().assistantMessages).toHaveLength(2)
  expect(saveCalled).toBe(false)
  expect(store.getState().lastError).toBeNull()
})

test('load hydrates persisted assistant history', async () => {
  const store = createWorkbenchStore(
    makeApi({
      listAssistantHistory: async () => ({
        ok: true,
        messages: [
          { role: 'user', content: '旧问题' },
          { role: 'assistant', content: '旧回答' },
        ],
      }),
    }),
  )

  await store.getState().load()

  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '旧问题' },
    { role: 'assistant', content: '旧回答' },
  ])
})

test('clearAssistantHistory clears local and persisted assistant history', async () => {
  let cleared = false
  const store = createWorkbenchStore(
    makeApi({
      listAssistantHistory: async () => ({
        ok: true,
        messages: [{ role: 'user', content: '旧问题' }],
      }),
      clearAssistantHistory: async () => {
        cleared = true
        return { ok: true, count: 0 }
      },
    }),
  )

  await store.getState().load()
  await store.getState().clearAssistantHistory()

  expect(cleared).toBe(true)
  expect(store.getState().assistantMessages).toEqual([])
})

test('importAssistantHistory appends source messages and reloads history (P6a)', async () => {
  let importedPath = ''
  let listCalls = 0
  const store = createWorkbenchStore(
    makeApi({
      importAssistantHistory: async (sourcePath) => {
        importedPath = sourcePath
        return { ok: true, imported: 2, source_name: 'SourceShelf' }
      },
      listAssistantHistory: async () => {
        listCalls += 1
        return {
          ok: true,
          messages: [
            { role: 'user', content: '目标原有' },
            { role: 'user', content: '源项目问题' },
            { role: 'assistant', content: '源项目答复' },
          ],
        }
      },
    }),
  )

  await store.getState().importAssistantHistory('/workspace/hsf/SourceShelf')

  expect(importedPath).toBe('/workspace/hsf/SourceShelf')
  // 导入成功后 reload 历史，且消息流包含合并后的完整记录
  expect(listCalls).toBeGreaterThanOrEqual(1)
  expect(store.getState().assistantMessages).toEqual([
    { role: 'user', content: '目标原有' },
    { role: 'user', content: '源项目问题' },
    { role: 'assistant', content: '源项目答复' },
  ])
  // 导入结果作为一条系统提示写进 compileLog
  expect(store.getState().compileLog.some((line) => line.includes('已从「SourceShelf」导入 2 条'))).toBe(true)
})

test('importAssistantHistory writes an empty-source note without failing (P6a)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      importAssistantHistory: async () => ({ ok: true, imported: 0, source_name: 'EmptySource' }),
    }),
  )

  await store.getState().importAssistantHistory('/workspace/hsf/EmptySource')

  expect(store.getState().lastError).toBeNull()
  expect(store.getState().compileLog.some((line) => line.includes('没有可导入的聊天记录'))).toBe(true)
})

test('importAssistantHistory records api failure in lastError (P6a)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      importAssistantHistory: async () => ({
        ok: false,
        error: 'Source project path does not exist: /nope',
      }),
    }),
  )

  await store.getState().importAssistantHistory('/nope')

  expect(store.getState().lastError).toContain('/nope')
})

test('distillAssistantHistory seeds the draft channel on success (P6b)', async () => {
  let called = false
  const store = createWorkbenchStore(
    makeApi({
      distillAssistantHistory: async () => {
        called = true
        return { ok: true, instruction: '把书架层板数改成 5，保留现有 3D 代码', message_count: 3 }
      },
    }),
  )

  await store.getState().distillAssistantHistory()

  expect(called).toBe(true)
  expect(store.getState().assistantDraftSeed).toBe('把书架层板数改成 5，保留现有 3D 代码')
  expect(store.getState().lastError).toBeNull()
})

test('distillAssistantHistory records api failure in lastError (P6b)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      distillAssistantHistory: async () => ({ ok: false, error: '当前项目没有聊天记录可整理。' }),
    }),
  )

  await store.getState().distillAssistantHistory()

  expect(store.getState().lastError).toContain('聊天记录')
  expect(store.getState().assistantDraftSeed).toBeNull()
})

test('distillAssistantHistory discards the seed when the project switches mid-flight (P6b)', async () => {
  let resolveDistill: (v: { ok: boolean; instruction?: string; error?: string }) => void = () => {}
  const store = createWorkbenchStore(
    makeApi({
      distillAssistantHistory: async () =>
        new Promise((resolve) => {
          resolveDistill = resolve
        }),
    }),
  )

  const pending = store.getState().distillAssistantHistory()
  // 请求未返回时切换项目（epoch 变化）
  store.setState({ projectEpoch: store.getState().projectEpoch + 1 })
  resolveDistill({ ok: true, instruction: '旧项目的整理结果' })
  await pending

  expect(store.getState().assistantDraftSeed).toBeNull()
  expect(store.getState().lastError).toBeNull()
})

test('consumeAssistantDraftSeed clears the seed (P6b)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      distillAssistantHistory: async () => ({ ok: true, instruction: '整理结果', message_count: 1 }),
    }),
  )
  await store.getState().distillAssistantHistory()
  expect(store.getState().assistantDraftSeed).toBe('整理结果')

  store.getState().consumeAssistantDraftSeed()

  expect(store.getState().assistantDraftSeed).toBeNull()
})

test('hydrateSnapshot clears an un-consumed draft seed on project switch (P6b)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      distillAssistantHistory: async () => ({ ok: true, instruction: '旧项目整理结果', message_count: 2 }),
      loadProjectPath: async (path: string) => ({
        project: { name: 'NewProj', source: 'hsf', path },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        compiler: { mode: 'mock', converter_path: '', output_dir: '' },
      }),
    }),
  )
  await store.getState().distillAssistantHistory()
  expect(store.getState().assistantDraftSeed).toBe('旧项目整理结果')

  await store.getState().loadProjectPath('/workspace/NewProj')

  expect(store.getState().assistantDraftSeed).toBeNull()
})

test('load hydrates project memory status', async () => {
  const store = createWorkbenchStore(
    makeApi({
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 2,
          lesson_count: 1,
          has_learned_skill: true,
          total_bytes: 2048,
        },
      }),
    }),
  )

  await store.getState().load()

  expect(store.getState().memoryStatus).toEqual({
    memory_root: '/workspace/Chair/.openbrep/memory',
    chat_count: 2,
    lesson_count: 1,
    has_learned_skill: true,
    total_bytes: 2048,
  })
})

test('clearProjectMemory clears persisted memory and local assistant history', async () => {
  let cleared = false
  const store = createWorkbenchStore(
    makeApi({
      listAssistantHistory: async () => ({
        ok: true,
        messages: [{ role: 'user', content: '旧问题' }],
      }),
      clearProjectMemory: async () => {
        cleared = true
        return {
          ok: true,
          before: {
            memory_root: '/workspace/Chair/.openbrep/memory',
            chat_count: 1,
            lesson_count: 0,
            has_learned_skill: false,
            total_bytes: 512,
          },
        }
      },
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 0,
          lesson_count: 0,
          has_learned_skill: false,
          total_bytes: 0,
        },
      }),
    }),
  )

  await store.getState().load()
  await store.getState().clearProjectMemory()

  expect(cleared).toBe(true)
  expect(store.getState().assistantMessages).toEqual([])
  expect(store.getState().memoryStatus?.chat_count).toBe(0)
  expect(store.getState().compileLog[0]).toContain('Cleared project memory')
})

test('loadMemoryLessons stores project lessons', async () => {
  const store = createWorkbenchStore(
    makeApi({
      fetchMemoryLessons: async () => ({
        ok: true,
        lessons: [
          {
            fingerprint: 'fp-1',
            category: 'general_compile_error',
            summary: 'Unknown command FOO at line 3',
            guidance: 'Avoid unsupported commands.',
            example: '',
            count: 2,
            first_seen: '2026-05-30T10:00:00Z',
            last_seen: '2026-05-30T10:10:00Z',
            source: 'test',
            project_name: 'Chair',
            raw_excerpt: 'Unknown command FOO at line 3',
          },
        ],
      }),
    }),
  )

  await store.getState().loadMemoryLessons()

  expect(store.getState().memoryLessons).toHaveLength(1)
  expect(store.getState().memoryLessons[0].summary).toContain('FOO')
})

test('summarizeProjectMemory stores skill preview and refreshes memory state', async () => {
  let summarized = false
  let lessonRefreshes = 0
  const store = createWorkbenchStore(
    makeApi({
      summarizeProjectMemory: async () => {
        summarized = true
        return {
          ok: true,
          summary: {
            ok: true,
            lesson_count: 1,
            path: '/workspace/Chair/.openbrep/memory/skills/learned_skill.md',
            message: '已整理 1 条错题约束，扫描聊天命中 0 条，方式：规则整理',
          },
          skill: '# OpenBrep Learned GDL Error Avoidance\n\n- Avoid FOO',
        }
      },
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 0,
          lesson_count: 1,
          has_learned_skill: true,
          total_bytes: 1024,
        },
      }),
      fetchMemoryLessons: async () => {
        lessonRefreshes += 1
        return {
          ok: true,
          lessons: [
            {
              fingerprint: 'fp-1',
              category: 'general_compile_error',
              summary: 'Unknown command FOO at line 3',
              guidance: 'Avoid unsupported commands.',
              example: '',
              count: 1,
              first_seen: '2026-05-30T10:00:00Z',
              last_seen: '2026-05-30T10:00:00Z',
              source: 'test',
              project_name: 'Chair',
              raw_excerpt: 'Unknown command FOO at line 3',
            },
          ],
        }
      },
    }),
  )

  await store.getState().summarizeProjectMemory()

  expect(summarized).toBe(true)
  expect(lessonRefreshes).toBe(1)
  expect(store.getState().memoryStatus?.has_learned_skill).toBe(true)
  expect(store.getState().memoryLessons).toHaveLength(1)
  expect(store.getState().memorySkillPreview).toContain('Avoid FOO')
  expect(store.getState().compileLog[0]).toContain('已整理 1 条错题约束')
})

test('deleteMemoryLesson removes a lesson and refreshes memory status', async () => {
  let deleted = ''
  const store = createWorkbenchStore(
    makeApi({
      deleteMemoryLesson: async (fingerprint) => {
        deleted = fingerprint
        return { ok: true, deleted: fingerprint, remaining_count: 0 }
      },
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 0,
          lesson_count: 0,
          has_learned_skill: false,
          total_bytes: 0,
        },
      }),
      fetchMemoryLessons: async () => ({ ok: true, lessons: [] }),
    }),
  )

  await store.getState().loadMemoryLessons()
  store.setState({
    memoryLessons: [
      {
        fingerprint: 'general_compile_error:abc123',
        category: 'general_compile_error',
        summary: 'Unknown command FOO at line 3',
        guidance: 'Avoid unsupported commands.',
        example: '',
        count: 1,
        first_seen: '2026-05-30T10:00:00Z',
        last_seen: '2026-05-30T10:00:00Z',
        source: 'test',
        project_name: 'Chair',
        raw_excerpt: 'Unknown command FOO at line 3',
      },
    ],
  })

  await store.getState().deleteMemoryLesson('general_compile_error:abc123')

  expect(deleted).toBe('general_compile_error:abc123')
  expect(store.getState().memoryLessons).toEqual([])
  expect(store.getState().memoryStatus?.lesson_count).toBe(0)
  expect(store.getState().compileLog[0]).toContain('Deleted memory lesson')
})

test('ignoreMemoryLesson hides a lesson and refreshes memory status', async () => {
  let ignored = ''
  const store = createWorkbenchStore(
    makeApi({
      ignoreMemoryLesson: async (fingerprint) => {
        ignored = fingerprint
        return { ok: true, ignored: fingerprint, remaining_count: 0 }
      },
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 0,
          lesson_count: 0,
          has_learned_skill: false,
          total_bytes: 0,
        },
      }),
      fetchMemoryLessons: async () => ({ ok: true, lessons: [] }),
    }),
  )

  store.setState({
    memoryLessons: [
      {
        fingerprint: 'general_compile_error:abc123',
        category: 'general_compile_error',
        summary: 'Unknown command FOO at line 3',
        guidance: 'Avoid unsupported commands.',
        example: '',
        count: 1,
        first_seen: '2026-05-30T10:00:00Z',
        last_seen: '2026-05-30T10:00:00Z',
        source: 'test',
        project_name: 'Chair',
        raw_excerpt: 'Unknown command FOO at line 3',
      },
    ],
  })

  await store.getState().ignoreMemoryLesson('general_compile_error:abc123')

  expect(ignored).toBe('general_compile_error:abc123')
  expect(store.getState().memoryLessons).toEqual([])
  expect(store.getState().memoryStatus?.lesson_count).toBe(0)
  expect(store.getState().compileLog[0]).toContain('Ignored memory lesson')
})

test('updateMemoryLesson edits a lesson and refreshes memory status', async () => {
  let updatedFingerprint = ''
  const editedLesson = {
    fingerprint: 'general_compile_error:abc123',
    category: 'syntax',
    summary: 'FOO is not a valid GDL command.',
    guidance: 'Replace FOO with a supported primitive.',
    example: 'Use BLOCK A, B, ZZYZX instead.',
    count: 1,
    first_seen: '2026-05-30T10:00:00Z',
    last_seen: '2026-05-30T10:00:00Z',
    source: 'test',
    project_name: 'Chair',
    raw_excerpt: 'Unknown command FOO at line 3',
  }
  const store = createWorkbenchStore(
    makeApi({
      updateMemoryLesson: async (fingerprint, updates) => {
        updatedFingerprint = fingerprint
        return { ok: true, lesson: { ...editedLesson, ...updates } }
      },
      fetchMemoryStatus: async () => ({
        ok: true,
        memory: {
          memory_root: '/workspace/Chair/.openbrep/memory',
          chat_count: 0,
          lesson_count: 1,
          has_learned_skill: false,
          total_bytes: 0,
        },
      }),
      fetchMemoryLessons: async () => ({ ok: true, lessons: [editedLesson] }),
    }),
  )

  await store.getState().updateMemoryLesson('general_compile_error:abc123', {
    category: 'syntax',
    summary: 'FOO is not a valid GDL command.',
    guidance: 'Replace FOO with a supported primitive.',
    example: 'Use BLOCK A, B, ZZYZX instead.',
  })

  expect(updatedFingerprint).toBe('general_compile_error:abc123')
  expect(store.getState().memoryLessons[0].summary).toBe('FOO is not a valid GDL command.')
  expect(store.getState().memoryLessons[0].guidance).toBe('Replace FOO with a supported primitive.')
  expect(store.getState().memoryStatus?.lesson_count).toBe(1)
  expect(store.getState().compileLog[0]).toContain('Updated memory lesson')
})

test('adopts code blocks from an assistant history message into dirty script buffers', async () => {
  const store = createWorkbenchStore(
    makeApi({
      listAssistantHistory: async () => ({
        ok: true,
        messages: [
          { role: 'user', content: '改 3D' },
          { role: 'assistant', content: '```gdl\nBLOCK A, B, ZZYZX\nEND\n```' },
        ],
      }),
      extractAssistantCodeBlocks: async () => ({
        ok: true,
        blocks: [
          {
            path: 'scripts/3d.gdl',
            script_name: '3d.gdl',
            content: 'BLOCK A, B, ZZYZX\nEND',
          },
        ],
      }),
    }),
  )

  await store.getState().load()
  await store.getState().adoptAssistantMessageCode(1)

  expect(store.getState().activeScriptName).toBe('3d.gdl')
  expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK A, B, ZZYZX\nEND')
  expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
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
    {
      role: 'assistant',
      content:
        'created bookshelf\n\n📁 项目已创建：/workspace/Bookshelf\n可继续发修改指令（如「把层板数改成 5」），或在编辑器里直接改脚本、参数面板里调参数。',
    },
  ])
  expect(store.getState().assistantBusy).toBe(false)
})

test('createProjectFromPrompt passes image attachments to the API', async () => {
  let capturedImage = null
  const store = createWorkbenchStore(
    makeApi({
      createProjectFromPrompt: async (_message, _assistantSettings, images) => {
        capturedImage = images
        return {
          ok: true,
          assistant: {
            kind: 'create',
            reply: 'created from image',
            changed_files: ['scripts/3d.gdl'],
            intent: 'IMAGE',
          },
          project: { name: 'ImageShelf', source: 'hsf', path: '/workspace/ImageShelf' },
          parameters: [],
          preview: { meshes: [], wires: [], warnings: [] },
          warnings: [],
          compiler: { mode: 'mock', converter_path: '', output_dir: '' },
        }
      },
    }),
  )

  await store.getState().createProjectFromPrompt('照图生成书架', [
    { name: 'shelf.png', mime: 'image/png', b64: 'ZmFrZS1pbWFnZQ==' },
  ])

  expect(capturedImage).toEqual([
    { name: 'shelf.png', mime: 'image/png', b64: 'ZmFrZS1pbWFnZQ==' },
  ])
  expect(store.getState().assistantMessages[0].content).toContain('[图: shelf.png]')
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
    changedFiles: ['scripts/3d.gdl'],
  })
})

test('generateAssistantChanges passes image attachments to the API', async () => {
  let capturedImage = null
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async (_message, _assistantSettings, images) => {
        capturedImage = images
        return {
          ok: true,
          assistant: {
            kind: 'generate',
            reply: 'changed from image',
            changed_files: ['scripts/3d.gdl'],
            intent: 'MODIFY',
          },
          preview: { meshes: [], wires: [], warnings: [] },
          warnings: [],
        }
      },
    }),
  )

  await store.getState().generateAssistantChanges('按图调整', [
    { name: 'chair.jpg', mime: 'image/jpeg', b64: 'ZmFrZS1pbWFnZQ==' },
  ])

  expect(capturedImage).toEqual([
    { name: 'chair.jpg', mime: 'image/jpeg', b64: 'ZmFrZS1pbWFnZQ==' },
  ])
  expect(store.getState().assistantMessages[0].content).toContain('[图: chair.jpg]')
})

test('generateAssistantChanges shows an explicit thinking message while the request is running', async () => {
  let resolveGenerate!: (value: Awaited<ReturnType<WorkbenchApi['generateWithAssistant']>>) => void
  const pendingGenerate = new Promise<Awaited<ReturnType<WorkbenchApi['generateWithAssistant']>>>((resolve) => {
    resolveGenerate = resolve
  })
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async () => pendingGenerate,
    }),
  )

  const turn = store.getState().generateAssistantChanges('按图调整', [
    { name: 'chair.jpg', mime: 'image/jpeg', b64: 'ZmFrZS1pbWFnZQ==' },
  ])

  expect(store.getState().assistantBusy).toBe(true)
  expect(store.getState().assistantMessages.at(-1)?.content).toContain('Thinking...')
  expect(store.getState().assistantMessages.at(-1)?.content).toContain('Reading the attached reference image: chair.jpg.')

  resolveGenerate({
    ok: true,
    assistant: {
      kind: 'generate',
      reply: 'changed from image',
      changed_files: ['scripts/3d.gdl'],
      intent: 'MODIFY',
    },
    preview: { meshes: [], wires: [], warnings: [] },
    warnings: [],
    events: [{ type: 'status', data: { message: '正在分析参考图结构...' } }],
  })
  await turn

  expect(store.getState().assistantBusy).toBe(false)
  expect(store.getState().assistantMessages.at(-1)?.content).toContain('changed from image')
  expect(store.getState().assistantMessages.at(-1)?.content).toContain('Process:')
  expect(store.getState().assistantMessages.at(-1)?.content).not.toContain('Thinking...')
})

test('generateAssistantChanges exposes image generation failures as lastError', async () => {
  const error = '当前模型或网关不支持图片分析：unsupported image_url'
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async () => ({ ok: false, error }),
    }),
  )

  await store.getState().generateAssistantChanges('按图调整', [
    { name: 'chair.png', mime: 'image/png', b64: 'ZmFrZS1pbWFnZQ==' },
  ])

  expect(store.getState().lastError).toBe(error)
  expect(store.getState().assistantMessages.at(-1)).toEqual({
    role: 'assistant',
    content: error,
    errorCategory: 'general',
  })
})

test('generateAssistantChanges labels llm configuration errors', async () => {
  const error = 'LLM 认证失败：模型 `deepseek-chat` 的 API Key 可能无效'
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async () => ({ ok: false, error }),
    }),
  )

  await store.getState().generateAssistantChanges('改成参数化')

  expect(store.getState().lastError).toBe(`LLM settings error: ${error}`)
  expect(store.getState().assistantMessages.at(-1)?.content).toBe(`LLM settings error: ${error}`)
})

test('path image backend error surfaces with the named path (P5a)', async () => {
  const pathError = 'Image path does not exist: /Users/ren/missing.png'
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async (_message, _settings, images) => {
        // 路径贴图前端无法预校验，后端报错必须指名路径
        expect(images).toEqual([{ name: '/Users/ren/missing.png', mime: '', b64: '', path: '/Users/ren/missing.png' }])
        return { ok: false, error: pathError }
      },
    }),
  )

  await store.getState().generateAssistantChanges('按这张图改', [
    { name: '/Users/ren/missing.png', mime: '', b64: '', path: '/Users/ren/missing.png' },
  ])

  expect(store.getState().assistantMessages.at(-1)?.content).toContain('/Users/ren/missing.png')
  expect(store.getState().assistantMessages.at(-1)?.content).toContain('Image path does not exist')
})

test('generateAssistantChanges saves dirty editor buffers before calling the LLM', async () => {
  const calls: string[] = []
  const store = createWorkbenchStore(
    makeApi({
      saveProjectScript: async (name) => {
        calls.push(`save:${name}`)
        return { success: true, saved_at: '2026-06-11T10:00:00' }
      },
      generateWithAssistant: async () => {
        calls.push('generate')
        return {
          ok: true,
          assistant: { kind: 'generate', reply: 'done', changed_files: [], intent: 'MODIFY' },
          preview: { meshes: [], wires: [], warnings: [] },
          warnings: [],
        }
      },
    }),
  )

  await store.getState().load()
  await store.getState().openScript('3d.gdl')
  store.getState().updateActiveScriptContent('BLOCK 2, 2, 2')
  await store.getState().generateAssistantChanges('改尺寸')

  expect(calls.indexOf('save:3d.gdl')).toBeGreaterThanOrEqual(0)
  expect(calls.indexOf('save:3d.gdl')).toBeLessThan(calls.indexOf('generate'))
  expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
})

test('setDraftParameter debounces rapid preview requests while updating draft immediately', async () => {
  vi.useFakeTimers()
  try {
    const calls: Record<string, unknown>[] = []
    const store = createWorkbenchStore(
      makeApi({
        fetchPreview: async (parameters) => {
          calls.push({ ...parameters })
          return { meshes: [{ name: 'debounced', vertices: [], faces: [] }], wires: [], warnings: [] }
        },
      }),
    )

    await store.getState().setDraftParameter('A', 1.1)
    await store.getState().setDraftParameter('A', 1.2)
    await store.getState().setDraftParameter('A', 1.3)

    expect(store.getState().draftParameters).toEqual({ A: 1.3 })
    expect(calls).toEqual([])

    await vi.advanceTimersByTimeAsync(249)
    expect(calls).toEqual([])

    await vi.advanceTimersByTimeAsync(1)
    expect(calls).toEqual([{ A: 1.3 }])
    expect(store.getState().preview?.meshes[0]?.name).toBe('debounced')
  } finally {
    vi.useRealTimers()
  }
})

test('setDraftParameter ignores out-of-order preview responses', async () => {
  vi.useFakeTimers()
  try {
    let resolveFirst!: (value: Awaited<ReturnType<WorkbenchApi['fetchPreview']>>) => void
    const firstPreview = new Promise<Awaited<ReturnType<WorkbenchApi['fetchPreview']>>>((resolve) => {
      resolveFirst = resolve
    })
    let callCount = 0
    const store = createWorkbenchStore(
      makeApi({
        fetchPreview: async () => {
          callCount += 1
          if (callCount === 1) return firstPreview
          return { meshes: [{ name: 'latest', vertices: [], faces: [] }], wires: [], warnings: [] }
        },
      }),
    )

    await store.getState().setDraftParameter('A', 1.1)
    await vi.advanceTimersByTimeAsync(250)
    expect(callCount).toBe(1)

    await store.getState().setDraftParameter('A', 1.2)
    await vi.advanceTimersByTimeAsync(250)
    resolveFirst({ meshes: [{ name: 'stale', vertices: [], faces: [] }], wires: [], warnings: ['stale'] })
    await firstPreview

    expect(store.getState().preview?.meshes[0]?.name).toBe('latest')
    expect(store.getState().warnings).toEqual([])
    expect(store.getState().draftParameters).toEqual({ A: 1.2 })
  } finally {
    vi.useRealTimers()
  }
})

test('generateAssistantChanges attaches changed files to the reply message', async () => {
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async () => ({
        ok: true,
        assistant: { kind: 'generate', reply: 'done', changed_files: ['scripts/3d.gdl'], intent: 'MODIFY' },
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
      }),
    }),
  )

  await store.getState().generateAssistantChanges('加一块层板')

  const reply = store.getState().assistantMessages.at(-1)
  expect(reply?.changedFiles).toEqual(['scripts/3d.gdl'])
  expect(reply?.errorCategory).toBeUndefined()
})

test('generateAssistantChanges tags failed replies with an error category', async () => {
  const store = createWorkbenchStore(
    makeApi({
      generateWithAssistant: async () => ({ ok: false, error: 'LLM 认证失败：API Key 可能无效' }),
    }),
  )

  await store.getState().generateAssistantChanges('改成参数化')

  expect(store.getState().assistantMessages.at(-1)?.errorCategory).toBe('llm')
})

test('generateAssistantChanges discards results when the project switched mid-request', async () => {
  let resolveGenerate!: (value: Awaited<ReturnType<WorkbenchApi['generateWithAssistant']>>) => void
  const pendingGenerate = new Promise<Awaited<ReturnType<WorkbenchApi['generateWithAssistant']>>>((resolve) => {
    resolveGenerate = resolve
  })
  const store = createWorkbenchStore(
    makeApi({
      fetchSnapshot: async () => ({
        project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        session_id: 'backend-1',
        project_epoch: 1,
      }),
      loadProjectPath: async (path: string) => ({
        project: { name: 'Other', source: 'hsf', path },
        parameters: [],
        preview: { meshes: [], wires: [], warnings: [] },
        warnings: [],
        session_id: 'backend-1',
        project_epoch: 2,
      }),
      generateWithAssistant: async () => pendingGenerate,
    }),
  )

  await store.getState().load()
  const turn = store.getState().generateAssistantChanges('加一块层板')
  await store.getState().loadProjectPath('/workspace/Other')
  resolveGenerate({
    ok: true,
    assistant: { kind: 'generate', reply: 'stale reply', changed_files: ['scripts/3d.gdl'], intent: 'MODIFY' },
    preview: { meshes: [{ name: 'stale', vertices: [], faces: [] }], wires: [], warnings: ['stale'] },
    warnings: ['stale'],
  })
  await turn

  expect(store.getState().assistantBusy).toBe(false)
  expect(store.getState().project?.name).toBe('Other')
  expect(store.getState().preview?.meshes.some((mesh) => mesh.name === 'stale')).toBe(false)
  expect(store.getState().assistantMessages.at(-1)?.content ?? '').not.toContain('stale reply')
  expect(store.getState().compileLog[0]).toContain('discarded')
})


// ── Workspace 切片（P3-d2） ────────────────────────────────

test('workspace slice: open workspace hydrates state from scan result', async () => {
  const store = createWorkbenchStore(makeApi())
  const initial = store.getState()
  expect(initial.workspace).toBeNull()

  await store.getState().openWorkspace('/workspace')

  const state = store.getState()
  expect(state.workspace?.path).toBe('/workspace')
  expect(state.workspace?.project_count).toBe(1)
  expect(state.workspace?.projects[0]?.name).toBe('Chair')
  expect(state.workspaceBusy).toBe(false)
})

test('workspace slice: open failure surfaces lastError and keeps workspace null', async () => {
  const store = createWorkbenchStore(
    makeApi({
      workspaceOpen: async () => ({ ok: false, error: 'not a workspace', code: 'not_a_workspace', workspace: null }),
    }),
  )

  await store.getState().openWorkspace('/nope')

  const state = store.getState()
  expect(state.workspace).toBeNull()
  expect(state.lastError).toContain('not a workspace')
  expect(state.workspaceBusy).toBe(false)
})

test('workspace slice: refresh keeps active flag from backend scan', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().openWorkspace('/workspace')

  await store.getState().refreshWorkspace()

  const state = store.getState()
  expect(state.workspace?.path).toBe('/workspace')
  const project = state.workspace?.projects[0]
  expect(project?.active).toBe(true)
  expect(project?.artifact_count).toBe(2)
  expect(project?.origin?.imported_kind).toBe('gdl')
})

test('workspace slice: close clears workspace and search state', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().openWorkspace('/workspace')
  await store.getState().searchWorkspace('cylind')

  await store.getState().closeWorkspace()

  const state = store.getState()
  expect(state.workspace).toBeNull()
  expect(state.workspaceSearchQuery).toBeNull()
  expect(state.workspaceSearchHits).toEqual([])
  expect(state.workspaceBusy).toBe(false)
})

test('workspace slice: search stores hits; empty query clears without API call', async () => {
  let apiCalls = 0
  const store = createWorkbenchStore(
    makeApi({
      workspaceSearch: async (query: string) => {
        apiCalls += 1
        return {
          ok: true,
          query,
          hits: [{ project: 'Chair', location: 'scripts/3d.gdl', line: 2, snippet: 'CYLIND 1, 1' }],
          hit_count: 1,
          workspace: '/workspace',
        }
      },
    }),
  )
  await store.getState().openWorkspace('/workspace')

  await store.getState().searchWorkspace('cylind')
  expect(store.getState().workspaceSearchQuery).toBe('cylind')
  expect(store.getState().workspaceSearchHits.length).toBe(1)

  const callsAfterSearch = apiCalls
  await store.getState().searchWorkspace('   ')
  expect(apiCalls).toBe(callsAfterSearch)
  expect(store.getState().workspaceSearchQuery).toBeNull()
  expect(store.getState().workspaceSearchHits).toEqual([])
})

test('workspace slice: refresh is a no-op without attachment', async () => {
  let scanCalls = 0
  const store = createWorkbenchStore(
    makeApi({
      workspaceScan: async () => {
        scanCalls += 1
        return { ok: true, workspace: null, project_count: 0, projects: [] }
      },
    }),
  )

  await store.getState().refreshWorkspace()
  expect(scanCalls).toBe(0)
  expect(store.getState().workspace).toBeNull()
})

test('workspace slice: project switching reuses loadProjectPath and epoch guard stays intact', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().openWorkspace('/workspace')

  await store.getState().loadProjectPath('/workspace/hsf/Chair')

  expect(store.getState().project?.path).toBe('/workspace/hsf/Chair')
  expect(store.getState().workspace?.projects[0]?.name).toBe('Chair')
})


// ── Workspace 初始化一条龙（P3-d2b） ──────────────────────

test('workspace slice: initWorkspace runs init then open and hydrates workspace', async () => {
  const calls: string[] = []
  const store = createWorkbenchStore(
    makeApi({
      workspaceInit: async (path: string) => {
        calls.push('init')
        return { ok: true, workspace: path, project_count: 0, projects: [] }
      },
      workspaceOpen: async (path: string) => {
        calls.push('open')
        return {
          ok: true,
          workspace: path,
          project_count: 1,
          projects: [
            { name: 'Chair', path: `${path}/hsf/Chair`, parameter_count: 3, scripts_present: ['SCRIPT_3D'], latest_revision_id: null, origin: null, artifact_count: 0, active: false },
          ],
        }
      },
    }),
  )

  await store.getState().initWorkspace('/fresh')

  expect(calls).toEqual(['init', 'open'])
  const state = store.getState()
  expect(state.workspace?.path).toBe('/fresh')
  expect(state.workspace?.projects[0]?.name).toBe('Chair')
  expect(state.workspaceBusy).toBe(false)
  expect(state.workspaceInitHint).toBeNull()
})

test('workspace slice: initWorkspace failure sets lastError and does not call open', async () => {
  const calls: string[] = []
  const store = createWorkbenchStore(
    makeApi({
      workspaceInit: async () => {
        calls.push('init')
        return { ok: false, error: 'init failed', workspace: null }
      },
      workspaceOpen: async () => {
        calls.push('open')
        return { ok: true, workspace: '/x', project_count: 0, projects: [] }
      },
    }),
  )

  await store.getState().initWorkspace('/bad')

  expect(calls).toEqual(['init'])
  const state = store.getState()
  expect(state.workspace).toBeNull()
  expect(state.lastError).toContain('init failed')
  expect(state.workspaceBusy).toBe(false)
})

test('workspace slice: open not_a_workspace records init hint path', async () => {
  const store = createWorkbenchStore(
    makeApi({
      workspaceOpen: async () => ({ ok: false, code: 'not_a_workspace', error: 'not initialized', workspace: null }),
    }),
  )

  await store.getState().openWorkspace('/plain-dir')

  const state = store.getState()
  expect(state.workspace).toBeNull()
  expect(state.workspaceInitHint).toBe('/plain-dir')
  expect(state.lastError).toContain('not initialized')
})

test('workspace slice: open failure without not_a_workspace leaves hint null', async () => {
  const store = createWorkbenchStore(
    makeApi({
      workspaceOpen: async () => ({ ok: false, error: 'boom', workspace: null }),
    }),
  )

  await store.getState().openWorkspace('/x')

  expect(store.getState().workspaceInitHint).toBeNull()
  expect(store.getState().lastError).toContain('boom')
})

test('workspace slice: successful open/close clear the init hint', async () => {
  const failing = createWorkbenchStore(
    makeApi({
      workspaceOpen: async () => ({ ok: false, code: 'not_a_workspace', error: 'nope', workspace: null }),
    }),
  )
  await failing.getState().openWorkspace('/hinted')
  expect(failing.getState().workspaceInitHint).toBe('/hinted')

  // 成功后 hint 清空
  const ok = createWorkbenchStore(makeApi())
  await ok.getState().openWorkspace('/ok')
  expect(ok.getState().workspaceInitHint).toBeNull()

  // close 也清空
  await ok.getState().closeWorkspace()
  expect(ok.getState().workspaceInitHint).toBeNull()
})

test('workspace slice: browseWorkspaceDirectory returns picked path or null', async () => {
  const picked = createWorkbenchStore(
    makeApi({ chooseOutputDirectory: async () => ({ ok: true, path: '/picked' }) }),
  )
  expect(await picked.getState().browseWorkspaceDirectory()).toBe('/picked')

  const cancelled = createWorkbenchStore(
    makeApi({ chooseOutputDirectory: async () => ({ ok: false, cancelled: true }) }),
  )
  expect(await cancelled.getState().browseWorkspaceDirectory()).toBeNull()
})


test('workspace slice: trashWorkspaceProject success refreshes the project list', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().openWorkspace('/workspace')
  expect(store.getState().workspace?.project_count).toBe(1)

  await store.getState().trashWorkspaceProject('/workspace/hsf/Chair')

  // 成功后 refresh：scan 返回的列表仍在（mock scan 恒返回 Chair）
  const state = store.getState()
  expect(state.workspace?.project_count).toBe(1)
  expect(state.workspaceBusy).toBe(false)
  expect(state.lastError).toBeNull()
})

test('workspace slice: trashWorkspaceProject failure sets lastError', async () => {
  const store = createWorkbenchStore(
    makeApi({
      trashWorkspaceProject: async () => ({ ok: false, error: 'project active', code: 'project_active' }),
    }),
  )
  await store.getState().openWorkspace('/workspace')

  await store.getState().trashWorkspaceProject('/workspace/hsf/Chair')

  const state = store.getState()
  expect(state.lastError).toContain('project active')
  expect(state.workspaceBusy).toBe(false)
  expect(state.workspace?.project_count).toBe(1)  // 列表未变
})

test('workspace slice: trashWorkspaceProject no-op without attachment', async () => {
  let calls = 0
  const store = createWorkbenchStore(
    makeApi({
      trashWorkspaceProject: async (path: string) => {
        calls += 1
        return { ok: true, trashed_to: path }
      },
    }),
  )

  await store.getState().trashWorkspaceProject('/hsf/x')

  expect(calls).toBe(0)
})


// ── 计划确认门（V3）：MODIFY 先出计划，用户确认后才执行 ─────────────────

const PENDING_PLAN = {
  intent_summary: '给书架加一层层板',
  user_visible_changes: ['3D 几何会多出一层层板'],
  affected_files: ['scripts/3d.gdl'],
  risk: '几何形状变化',
}

test('sendChat modify requests plan and awaits confirmation (V3)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({ ok: true, awaiting_confirmation: true, pending_plan: PENDING_PLAN }),
    }),
  )
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')

  const state = store.getState()
  expect(state.pendingPlan).toEqual(PENDING_PLAN)
  expect(state.assistantBusy).toBe(false)
  const last = state.assistantMessages.at(-1)
  expect(last?.content).toContain('修改计划已生成')
  expect(last?.thinkingSteps?.[0]?.type).toBe('plan')
  expect(last?.thinkingSteps?.[0]?.userVisibleChanges).toEqual(PENDING_PLAN.user_visible_changes)
})

test('confirmPendingPlan(true) runs the confirmed execution stream (V3)', async () => {
  let confirmArgs: [boolean, boolean] | null = null
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({ ok: true, awaiting_confirmation: true, pending_plan: PENDING_PLAN }),
      confirmModifyPlan: async (approve: boolean, stream?: boolean, onEvent?: (event: AssistantStreamEvent) => void) => {
        confirmArgs = [approve, !!stream]
        onEvent?.({ type: 'status', data: { stage: 'compile', message: '编译中' } })
        return {
          ok: true,
          assistant: { kind: 'generate', reply: '✅ 已按确认的计划执行完成。', changed_files: ['scripts/3d.gdl'], intent: 'MODIFY' },
          preview: null,
          warnings: [],
          events: [],
        }
      },
      getProjectScript: async (scriptName: string) => ({
        name: scriptName,
        path: `scripts/${scriptName}`,
        content: `updated ${scriptName}`,
      }),
    }),
  )
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')
  await store.getState().confirmPendingPlan(true)

  const state = store.getState()
  expect(confirmArgs).toEqual([true, true])
  expect(state.pendingPlan).toBeNull()
  expect(state.assistantBusy).toBe(false)
  expect(state.assistantMessages.at(-1)?.content).toContain('已按确认的计划执行完成')
  // 计划步骤保留 + 执行流步骤追加
  const steps = state.assistantMessages.at(-1)?.thinkingSteps ?? []
  expect(steps.some((s) => s.type === 'plan')).toBe(true)
  expect(steps.some((s) => s.message === '编译中')).toBe(true)
})

test('confirmPendingPlan(false) cancels and clears the pending plan (V3)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({ ok: true, awaiting_confirmation: true, pending_plan: PENDING_PLAN }),
      confirmModifyPlan: async (approve: boolean) => ({ ok: true, cancelled: true }),
    }),
  )
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')
  expect(store.getState().pendingPlan).not.toBeNull()

  await store.getState().confirmPendingPlan(false)

  const state = store.getState()
  expect(state.pendingPlan).toBeNull()
  expect(state.assistantMessages.at(-1)?.content).toContain('已取消')
})

test('sendChat modify falls back to direct result when plan request fails (V3)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({
        ok: true,
        assistant: { kind: 'generate', reply: '计划失败，直接执行完成。', changed_files: [], intent: 'MODIFY' },
        plan_failed: true,
      }),
    }),
  )
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')

  const state = store.getState()
  expect(state.pendingPlan).toBeNull()
  expect(state.assistantMessages.at(-1)?.content).toContain('直接执行完成')
})

test('confirmPendingPlan without a pending plan sets lastError (V3)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  await store.getState().confirmPendingPlan(true)
  expect(store.getState().lastError).toContain('没有待确认的修改计划')
})
// ── 模式级 skill 提案（P2-d） ─────────────────────────────

const SKILL_PROPOSAL = {
  name: 'shelf_loop_pattern',
  pattern_type: 'shelf_loop',
  content: '## 适用场景 / When to Use\n层板循环对象。\n\n## 写法要点\n- FOR 循环 + ADD/DEL 配对。',
  evidence: { intent: 'MODIFY', changed_files: ['scripts/3d.gdl'], project: 'Shelf' },
}

test('sendChat stores skill proposal from generate result (P2-d)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({
        ok: true,
        assistant: { kind: 'generate', reply: '修改完成。', changed_files: ['scripts/3d.gdl'], intent: 'MODIFY' },
        skill_proposal: SKILL_PROPOSAL,
        plan_failed: true,
      }),
    }),
  )
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')

  expect(store.getState().pendingSkillProposal).toEqual(SKILL_PROPOSAL)
})

test('successful generate without proposal clears stale skill proposal (P2-d)', async () => {
  const store = createWorkbenchStore(
    makeApi({
      requestModifyPlan: async () => ({
        ok: true,
        assistant: { kind: 'generate', reply: '修改完成。', changed_files: [], intent: 'MODIFY' },
        skill_proposal: null,
        plan_failed: true,
      }),
    }),
  )
  await store.getState().load()
  store.setState({ pendingSkillProposal: SKILL_PROPOSAL })
  await store.getState().sendChat('给书架加一层层板')

  expect(store.getState().pendingSkillProposal).toBeNull()
})

test('confirmPendingSkillProposal(true) approves and clears the proposal (P2-d)', async () => {
  let approveArg: boolean | null = null
  const store = createWorkbenchStore(
    makeApi({
      confirmSkillProposal: async (approve: boolean) => {
        approveArg = approve
        return { ok: true, skill: 'shelf_loop_pattern', verified: true, gate: 'structural', status: 'verified' }
      },
    }),
  )
  await store.getState().load()
  store.setState({ pendingSkillProposal: SKILL_PROPOSAL })

  await store.getState().confirmPendingSkillProposal(true)

  const state = store.getState()
  expect(approveArg).toBe(true)
  expect(state.pendingSkillProposal).toBeNull()
  expect(state.assistantMessages.at(-1)?.content).toContain('已沉淀并通过验证')
})

test('confirmPendingSkillProposal(false) discards the proposal (P2-d)', async () => {
  let approveArg: boolean | null = null
  const store = createWorkbenchStore(
    makeApi({
      confirmSkillProposal: async (approve: boolean) => {
        approveArg = approve
        return { ok: true, discarded: true, message: '已丢弃 skill 提案。' }
      },
    }),
  )
  await store.getState().load()
  store.setState({ pendingSkillProposal: SKILL_PROPOSAL })

  await store.getState().confirmPendingSkillProposal(false)

  const state = store.getState()
  expect(approveArg).toBe(false)
  expect(state.pendingSkillProposal).toBeNull()
  expect(state.assistantMessages.at(-1)?.content).toContain('已丢弃')
})

test('confirmPendingSkillProposal without a pending proposal sets lastError (P2-d)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  await store.getState().confirmPendingSkillProposal(true)
  expect(store.getState().lastError).toContain('没有待确认的 skill 提案')
})

// ── P2a：修改前后对比 ghost 快照 ─────────────────────────────────────────
// ghost 锚定"最近一次 AI 任务发起时的预览"：sendChat 捕获；参数防抖刷新 /
// 手动 Update / 质量档切换不覆盖；项目切换清空。

const GHOST_REFRESHED_PREVIEW = {
  meshes: [{ name: 'after', vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces: [[0, 1, 2]] }],
  wires: [],
  warnings: [],
}

test('sendChat snapshots the pre-task preview as ghost (P2a)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  expect(store.getState().preview).not.toBeNull()

  await store.getState().sendChat('给书架加一层层板')

  const state = store.getState()
  expect(state.previewGhost).toEqual(state.preview)
  expect(state.previewGhostLabel).toBe('preview.ghost.preTask')
})

test('sendChat with no preview leaves ghost null (P2a, CREATE path)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  store.setState({ preview: null })

  await store.getState().sendChat('给书架加一层层板')

  expect(store.getState().previewGhost).toBeNull()
  expect(store.getState().previewGhostLabel).toBeNull()
})

test('preview refresh (debounce / manual update / quality switch) does not overwrite ghost (P2a)', async () => {
  const store = createWorkbenchStore(makeApi({ fetchPreview: async () => GHOST_REFRESHED_PREVIEW }))
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')
  const ghost = store.getState().previewGhost
  expect(ghost).not.toBeNull()

  // 防抖刷新 / 手动 Update 最终都走 loadPreview3D；质量档切换也同链
  await store.getState().loadPreview3D()
  expect(store.getState().preview).toEqual(GHOST_REFRESHED_PREVIEW)
  expect(store.getState().previewGhost).toEqual(ghost)

  await store.getState().setPreviewQuality('accurate')
  expect(store.getState().preview).toEqual(GHOST_REFRESHED_PREVIEW)
  expect(store.getState().previewGhost).toEqual(ghost)
  expect(store.getState().previewGhostLabel).toBe('preview.ghost.preTask')
})

test('project switch clears the ghost snapshot (P2a)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  await store.getState().sendChat('给书架加一层层板')
  expect(store.getState().previewGhost).not.toBeNull()

  await store.getState().loadProjectPath('/workspace/Other')

  expect(store.getState().previewGhost).toBeNull()
  expect(store.getState().previewGhostLabel).toBeNull()
})

test('loading the app (fetchSnapshot) starts with no ghost (P2a)', async () => {
  const store = createWorkbenchStore(makeApi())
  await store.getState().load()
  expect(store.getState().previewGhost).toBeNull()
  expect(store.getState().previewGhostLabel).toBeNull()
})
