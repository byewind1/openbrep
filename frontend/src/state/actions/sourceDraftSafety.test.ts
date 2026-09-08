import { beforeEach, describe, expect, test, vi } from 'vitest'
import { createWorkbenchStore } from '../workbenchStore'
import type { WorkbenchApi } from '../workbenchStoreTypes'
import { collectScriptOverrides, hasAnyDraft, keepValidDraftParameters } from './sourceActionHelpers'

// SF1 草稿保护：AC01–AC14、AC18、AC20、AC27 的 store 级回归。
// 约定：带 path 的 Chair 项目视为"已落盘"，untitled 视为无路径。

function snapshot(project: Record<string, unknown> | null = {
  name: 'Chair',
  source: 'hsf',
  path: '/workspace/Chair',
}) {
  return {
    project,
    parameters: [
      { name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true },
      { name: 'B', type_tag: 'Length', description: 'Depth', value: '0.5', is_fixed: true },
    ],
    preview: { meshes: [], wires: [], warnings: [] },
    warnings: [],
    compiler: { mode: 'mock' as const, converter_path: '', output_dir: '' },
  }
}

type ApiOverrides = { [K in keyof WorkbenchApi]?: unknown }

function makeApi(overrides: ApiOverrides = {}): WorkbenchApi {
  // mock 后端内容表：save 后 get 返回已保存内容（贴近真实后端）
  const scriptStore: Record<string, string> = {}
  return {
    fetchSnapshot: vi.fn(async () => snapshot()),
    listProjectScripts: vi.fn(async () => ({
      scripts: [
        { name: '3d.gdl', path: 'scripts/3d.gdl', exists: true, size: 10 },
        { name: '2d.gdl', path: 'scripts/2d.gdl', exists: true, size: 10 },
        { name: 'paramlist.xml', path: 'paramlist.xml', exists: true, size: 10 },
      ],
    })),
    getProjectScript: vi.fn(async (name: string) => ({
      name,
      path: `scripts/${name}`,
      content: scriptStore[name] ?? `disk ${name}`,
    })),
    saveProjectScript: vi.fn(async (name: string, content: string) => {
      scriptStore[name] = content
      return { success: true, saved_at: '2026-09-08T00:00:00Z' }
    }),
    saveProject: vi.fn(async () => ({ ok: true, saved_to: '/workspace/Chair', ...snapshot() })),
    exportHsfProject: vi.fn(async (_parentDir = '', name = '') => ({
      ok: true,
      saved_to: `/exports/${name}`,
      ...snapshot({ name, source: 'hsf', path: `/exports/${name}` }),
    })),
    applyParameters: vi.fn(async (parameters: Record<string, unknown>) => ({
      ok: true,
      changed: parameters,
      ...snapshot(),
    })),
    addProjectParameter: vi.fn(async (parameter: { name: string; type_tag: string; value: unknown; description?: string }) => ({
      ok: true,
      added: { name: parameter.name, type_tag: parameter.type_tag, description: parameter.description ?? '', value: String(parameter.value), is_fixed: false },
      ...snapshot(),
    })),
    updateProjectParameter: vi.fn(async () => ({ ok: true, ...snapshot() })),
    deleteProjectParameter: vi.fn(async (name: string) => ({
      ok: true,
      deleted: name,
      ...snapshot(),
      parameters: [{ name: 'A', type_tag: 'Length', description: 'Width', value: '1.0', is_fixed: true }],
    })),
    saveProjectRevision: vi.fn(async () => ({ ok: true, latest_revision_id: 'r1' })),
    listProjectRevisions: vi.fn(async () => ({ ok: true, revisions: [], latest_revision_id: null })),
    compileProject: vi.fn(async () => ({ ok: true, compile: { success: true, mode: 'mock', output_path: '/tmp/x.gsm' } })),
    mockCompile: vi.fn(async () => ({ success: true, mode: 'mock', issues: [], duration_ms: 1 })),
    fetchPreview: vi.fn(async () => ({ meshes: [], wires: [], warnings: [] })),
    askAssistant: vi.fn(async () => ({ ok: true, assistant: { reply: 'hello' } })),
    generateWithAssistant: vi.fn(async () => ({ ok: true, assistant: { reply: 'done', changed_files: [] } })),
    generateWithAssistantStream: vi.fn(async () => ({ ok: true, assistant: { reply: 'done', changed_files: [] } })),
    requestModifyPlan: vi.fn(async () => ({ ok: true, assistant: { reply: 'plan' } })),
    confirmModifyPlan: vi.fn(async () => ({ ok: true, assistant: { reply: 'done', changed_files: [] } })),
    listRecentProjects: vi.fn(async () => ({ ok: true, projects: [] })),
    listAssistantHistory: vi.fn(async () => ({ ok: true, messages: [] })),
    fetchMemoryStatus: vi.fn(async () => ({
      ok: true,
      memory: { memory_root: '', chat_count: 0, lesson_count: 0, has_learned_skill: false, total_bytes: 0 },
    })),
    ...overrides,
  } as unknown as WorkbenchApi
}

async function loadedStore(api: WorkbenchApi) {
  const store = createWorkbenchStore(api)
  await store.getState().load()
  return store
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('SF1 Save（F01）', () => {
  test('AC01 非当前标签脏：Save 保存所有脏脚本，编辑保留且 clean', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    // 当前标签是 3d.gdl（load 后默认打开），脏的是非当前的 2d.gdl
    store.getState().updateScriptContent('2d.gdl', 'PROJECT2 3, 270, 2\n! AC01\n')

    const ok = await store.getState().saveProject()

    expect(ok).toBe(true)
    expect(api.saveProjectScript).toHaveBeenCalledWith('2d.gdl', 'PROJECT2 3, 270, 2\n! AC01\n')
    expect(api.saveProject).toHaveBeenCalledTimes(1)
    expect(store.getState().dirtyScripts['2d.gdl']).toBe(false)
    expect(store.getState().scriptContents['2d.gdl']).toBe('PROJECT2 3, 270, 2\n! AC01\n')
  })

  test('AC02 多标签都脏：Save 全部入盘（保持原正向场景）', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK A,B,ZZYZX ! AC02_3D\n')
    store.getState().updateScriptContent('2d.gdl', 'PROJECT2 3,270,2 ! AC02_2D\n')

    await store.getState().saveProject()

    expect(api.saveProjectScript).toHaveBeenCalledWith('3d.gdl', 'BLOCK A,B,ZZYZX ! AC02_3D\n')
    expect(api.saveProjectScript).toHaveBeenCalledWith('2d.gdl', 'PROJECT2 3,270,2 ! AC02_2D\n')
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().dirtyScripts['2d.gdl']).toBe(false)
  })

  test('AC03 第 2 个脚本写入失败：第 1 个可已保存，其余仍脏，project save 未调用', async () => {
    const api = makeApi({
      saveProjectScript: vi.fn(async (name: string) =>
        name === '2d.gdl' ? { success: false, error: 'disk full' } : { success: true, saved_at: '' },
      ),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'ok-3d\n')
    store.getState().updateScriptContent('2d.gdl', 'fail-2d\n')

    const ok = await store.getState().saveProject()

    expect(ok).toBe(false)
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().dirtyScripts['2d.gdl']).toBe(true)
    expect(store.getState().scriptContents['2d.gdl']).toBe('fail-2d\n')
    expect(api.saveProject).not.toHaveBeenCalled()
    expect(store.getState().lastError).toBe('disk full')
  })

  test('AC04 dirty 但 content 缺失：明确失败而非跳过', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    // 直接注入 dirty 标记但没有编辑器内容
    store.setState({ dirtyScripts: { '3d.gdl': true }, scriptContents: {} })

    const ok = await store.getState().saveProject()

    expect(ok).toBe(false)
    expect(api.saveProjectScript).not.toHaveBeenCalled()
    expect(api.saveProject).not.toHaveBeenCalled()
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(store.getState().lastError).toContain('missing')
  })

  test('AC05 保存等待期间脚本被更新：较新文本与 dirty 不被旧响应清掉；busy 有短时只读', async () => {
    let release!: (value: { success: boolean; saved_at: string }) => void
    const gate = new Promise<{ success: boolean; saved_at: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({
      saveProjectScript: vi.fn(() => gate),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'submitted-text\n')

    const pending = store.getState().saveProject()
    // 等待期间：源操作 busy（编辑器只读），用户/并发路径更新内容
    await vi.waitFor(() => expect(store.getState().sourceActionBusy).toBe(true))
    store.getState().updateScriptContent('3d.gdl', 'newer-text\n')
    release({ success: true, saved_at: '' })
    const ok = await pending

    expect(ok).toBe(true)
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().scriptContents['3d.gdl']).toBe('newer-text\n')
    // 内容已变：不被旧响应清 dirty
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
  })

  test('AC06 参数草稿存在时 Save：不调用 Apply，参数草稿仍在', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    await store.getState().setDraftParameter('A', 2)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    await store.getState().saveProject()

    expect(api.applyParameters).not.toHaveBeenCalled()
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
  })

  test('AC07 untitled Save→取消命名：无导出、无 flush、所有草稿保持', async () => {
    const api = makeApi({
      fetchSnapshot: vi.fn(async () => snapshot({ name: 'Untitled GDL Object', source: 'untitled' })),
      saveProject: vi.fn(async () => ({
        ok: false,
        needs_save_as: true,
        error: 'Project has no HSF path. Use Save As HSF.',
        ...snapshot({ name: 'Untitled GDL Object', source: 'untitled' }),
      })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'untitled-draft\n')

    await store.getState().saveProject()
    expect(store.getState().needsSaveAs).toBe(true)
    // 未命名阶段不先 flush 到任何目录
    expect(api.saveProjectScript).not.toHaveBeenCalled()
    expect(api.exportHsfProject).not.toHaveBeenCalled()

    // 用户取消命名
    store.getState().clearNeedsSaveAs()

    expect(api.exportHsfProject).not.toHaveBeenCalled()
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(store.getState().scriptContents['3d.gdl']).toBe('untitled-draft\n')
  })
})

describe('SF1 参数 Apply / CRUD（F03）', () => {
  test('AC08 Apply 前存在脚本草稿：先写脚本再写参数，两者都保留', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! AC08\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().applyDraftParameters()

    expect(ok).toBe(true)
    // 顺序：先脚本后参数
    expect(api.saveProjectScript).toHaveBeenCalledWith('3d.gdl', 'BLOCK 1,1,1 ! AC08\n')
    expect(api.applyParameters).toHaveBeenCalledWith({ A: 2 })
    const scriptCall = (api.saveProjectScript as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0]
    const applyCall = (api.applyParameters as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0]
    expect(scriptCall).toBeLessThan(applyCall)
    // 脚本已保存且编辑器内容不丢；参数草稿已提交清理
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK 1,1,1 ! AC08\n')
    expect(store.getState().draftParameters).toEqual({})
  })

  test('AC09 Apply 前 flush 失败：参数 API 零调用，参数草稿仍在', async () => {
    const api = makeApi({
      saveProjectScript: vi.fn(async () => ({ success: false, error: 'disk full' })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().applyDraftParameters()

    expect(ok).toBe(false)
    expect(api.applyParameters).not.toHaveBeenCalled()
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().applying).toBe(false)
  })

  test('AC10 参数 API 失败：脚本已保存事实真实、参数草稿仍在', async () => {
    const api = makeApi({
      applyParameters: vi.fn(async () => ({ ok: false, error: 'backend rejected', ...snapshot() })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().applyDraftParameters()

    expect(ok).toBe(false)
    expect(api.saveProjectScript).toHaveBeenCalled()
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().lastError).toContain('Scripts saved')
  })

  test('AC11 参数增/改/删：先 flush 脚本、不覆盖脏脚本、不清空无关参数草稿', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! AC11\n')
    await store.getState().setDraftParameter('A', 2)

    const added = await store.getState().addProjectParameter({
      name: 'seat_height',
      type_tag: 'Length',
      value: 0.45,
    })

    expect(added).toBe(true)
    expect(api.saveProjectScript).toHaveBeenCalledWith('3d.gdl', 'BLOCK 1,1,1 ! AC11\n')
    expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK 1,1,1 ! AC11\n')
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    // 无关参数草稿保留（A 在新参数列表中仍合法）
    expect(store.getState().draftParameters).toEqual({ A: 2 })

    // 删除参数只清被删参数的草稿
    await store.getState().setDraftParameter('seat_height', 0.5)
    const deleted = await store.getState().deleteProjectParameter('seat_height')
    expect(deleted).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 2 })
  })
})

describe('SF1 Save Revision（F05）', () => {
  test('AC12 保存版本前先保存全部脚本', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! AC12\n')

    const ok = await store.getState().saveRevision('rev with edits')

    expect(ok).toBe(true)
    expect(api.saveProjectScript).toHaveBeenCalledWith('3d.gdl', 'BLOCK 1,1,1 ! AC12\n')
    expect(api.saveProjectRevision).toHaveBeenCalledWith('rev with edits')
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().revisionLoading).toBe(false)
  })

  test('AC13 flush 失败不创建版本；版本 API 失败复位且不报成功', async () => {
    const failingFlush = makeApi({
      saveProjectScript: vi.fn(async () => ({ success: false, error: 'disk full' })),
    })
    const store1 = await loadedStore(failingFlush)
    store1.getState().updateScriptContent('3d.gdl', 'dirty\n')
    const ok1 = await store1.getState().saveRevision('x')
    expect(ok1).toBe(false)
    expect(failingFlush.saveProjectRevision).not.toHaveBeenCalled()
    expect(store1.getState().revisionLoading).toBe(false)
    expect(store1.getState().dirtyScripts['3d.gdl']).toBe(true)

    const failingApi = makeApi({
      saveProjectRevision: vi.fn(async () => ({ ok: false, error: 'revision backend down' })),
    })
    const store2 = await loadedStore(failingApi)
    store2.getState().updateScriptContent('3d.gdl', 'dirty\n')
    const ok2 = await store2.getState().saveRevision('keep this message')
    expect(ok2).toBe(false)
    expect(failingApi.saveProjectScript).toHaveBeenCalled()
    expect(store2.getState().revisionLoading).toBe(false)
    expect(store2.getState().lastError).toBe('revision backend down')
    // 脚本已保存（flush 成功）但版本未建成；草稿文本仍在编辑器
    expect(store2.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store2.getState().scriptContents['3d.gdl']).toBe('dirty\n')
    expect(store2.getState().compileLog.join('\n')).not.toContain('Saved revision r1')
  })

  test('AC14 有未 Apply 参数：提示不纳入版本，参数草稿保留', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().saveRevision('with param draft')

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(api.applyParameters).not.toHaveBeenCalled()
    expect(store.getState().compileLog.join('\n')).toContain('unapplied parameter drafts are not included')
  })
})

describe('SF1 Save As（F02）', () => {
  test('AC15/20 前端把脏脚本作为 overrides 传给导出；参数草稿不自动入盘', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! AC15\n')
    store.getState().updateScriptContent('2d.gdl', '')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().exportHsfProject('', 'CopyName', collectScriptOverrides(store.getState()).overrides)

    expect(ok).toBe(true)
    const exportCall = (api.exportHsfProject as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(exportCall[2]).toEqual({ '3d.gdl': 'BLOCK 1,1,1 ! AC15\n', '2d.gdl': '' })
    expect(api.applyParameters).not.toHaveBeenCalled()
    // 新项目激活；参数草稿保留并提示未应用
    expect(store.getState().project?.path).toBe('/exports/CopyName')
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().compileLog.join('\n')).toContain('Parameter drafts kept (not applied)')
  })

  test('AC18 导出失败：旧项目、脚本内容、dirty、参数草稿全部保留', async () => {
    const api = makeApi({
      exportHsfProject: vi.fn(async () => ({
        ok: false,
        error: 'Target HSF directory already exists and is not empty',
        ...snapshot(),
      })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! AC18\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().exportHsfProject('', 'CopyName', { '3d.gdl': 'BLOCK 1,1,1 ! AC18\n' })

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/workspace/Chair')
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(store.getState().scriptContents['3d.gdl']).toBe('BLOCK 1,1,1 ! AC18\n')
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().loading).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
  })
})

describe('SF1 重入与冲突（AC27）', () => {
  test('重复/并发调用：第二次被拒绝，busy 最终复位', async () => {
    let release!: (value: { success: boolean; saved_at: string }) => void
    const gate = new Promise<{ success: boolean; saved_at: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ saveProjectScript: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const first = store.getState().saveProject()
    await vi.waitFor(() => expect(store.getState().sourceActionBusy).toBe(true))
    const second = await store.getState().saveProject()
    expect(second).toBe(false)
    expect(store.getState().lastError).toContain('in progress')

    release({ success: true, saved_at: '' })
    await first
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().loading).toBe(false)
  })

  test('AI 执行中 / 编译中 / 加载中：源操作被拒绝', async () => {
    const api = makeApi()
    const store = await loadedStore(api)

    store.setState({ assistantBusy: true })
    expect(await store.getState().saveProject()).toBe(false)
    store.setState({ assistantBusy: false, compiling: true })
    expect(await store.getState().saveProject()).toBe(false)
    store.setState({ compiling: false, loading: true })
    expect(await store.getState().saveProject()).toBe(false)
    store.setState({ loading: false })

    expect(store.getState().sourceActionBusy).toBe(false)
    expect(api.saveProject).not.toHaveBeenCalled()
  })

  test('网络异常抛错：返回 false、busy 仍复位（不卡死后续操作）', async () => {
    const api = makeApi({
      saveProjectScript: vi.fn(async () => {
        throw new Error('network down')
      }),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const ok = await store.getState().saveProject()

    expect(ok).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().lastError).toContain('network down')
  })
})

describe('SF1 辅助函数', () => {
  test('collectScriptOverrides 只取 dirty 且空串是有效覆盖', () => {
    const { overrides } = collectScriptOverrides({
      dirtyScripts: { '3d.gdl': true, '2d.gdl': true, 'vl.gdl': false },
      scriptContents: { '3d.gdl': 'x', '2d.gdl': '' },
    } as never)
    expect(overrides).toEqual({ '3d.gdl': 'x', '2d.gdl': '' })
  })

  test('collectScriptOverrides dirty=true 但 content 缺失时返回错误', () => {
    const { overrides, error } = collectScriptOverrides({
      dirtyScripts: { '3d.gdl': true, '2d.gdl': true },
      scriptContents: { '3d.gdl': 'x' },
    } as never)
    expect(overrides).toEqual({ '3d.gdl': 'x' })
    expect(error).toContain('missing editor buffer')
    expect(error).toContain('2d.gdl')
  })

  test('hasAnyDraft：脚本草稿或参数草稿任一存在', () => {
    expect(hasAnyDraft({ dirtyScripts: {}, draftParameters: {} })).toBe(false)
    expect(hasAnyDraft({ dirtyScripts: { '3d.gdl': true }, draftParameters: {} })).toBe(true)
    expect(hasAnyDraft({ dirtyScripts: {}, draftParameters: { A: 1 } })).toBe(true)
  })

  test('keepValidDraftParameters：保留合法项、报告被丢字段', () => {
    const { kept, dropped } = keepValidDraftParameters({ A: 1, gone: 2 }, ['A', 'B'])
    expect(kept).toEqual({ A: 1 })
    expect(dropped).toEqual(['gone'])
  })
})

// R1 返工回归：针对 5318f06 验收结论 CHANGES REQUESTED 的五项补充。
describe('R1-01 统一导出入口', () => {
  test('exportHsfProject 未传 overrides 时由 action 统一收集当前草稿', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! R1_01_AUTO\n')
    store.getState().updateScriptContent('2d.gdl', '')

    const ok = await store.getState().exportHsfProject('', 'AutoCollect')

    expect(ok).toBe(true)
    const exportCall = (api.exportHsfProject as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(exportCall[2]).toEqual({ '3d.gdl': 'BLOCK 1,1,1 ! R1_01_AUTO\n', '2d.gdl': '' })
  })

  test('dirty=true 但 content 缺失：export API 不调用、草稿保留', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.setState({ dirtyScripts: { '3d.gdl': true }, scriptContents: {} })

    const ok = await store.getState().exportHsfProject('', 'MissingBuffer')

    expect(ok).toBe(false)
    expect(api.exportHsfProject).not.toHaveBeenCalled()
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(store.getState().lastError).toContain('missing editor buffer')
    expect(store.getState().loading).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
  })

  test('首次 Save（needs_save_as）→命名→导出副本含当前草稿', async () => {
    const api = makeApi({
      fetchSnapshot: vi.fn(async () => snapshot({ name: 'Untitled GDL Object', source: 'untitled' })),
      saveProject: vi.fn(async () => ({
        ok: false,
        needs_save_as: true,
        error: 'Project has no HSF path. Use Save As HSF.',
        ...snapshot({ name: 'Untitled GDL Object', source: 'untitled' }),
      })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'BLOCK 1,1,1 ! R1_01_FIRST\n')

    const saved = await store.getState().saveProject()
    expect(saved).toBe(false)
    expect(store.getState().needsSaveAs).toBe(true)

    const exported = await store.getState().exportHsfProject('', 'FirstNamedSave')

    expect(exported).toBe(true)
    const exportCall = (api.exportHsfProject as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(exportCall[2]).toEqual({ '3d.gdl': 'BLOCK 1,1,1 ! R1_01_FIRST\n' })
    expect(store.getState().project?.path).toBe('/exports/FirstNamedSave')
  })
})

describe('R1-02 异步身份检查与反向冲突保护', () => {
  test('参数 write 等待期间 projectEpoch 改变：旧结果不覆盖新项目', async () => {
    let release!: (value: { ok: boolean; parameters: unknown[] }) => void
    const gate = new Promise<{ ok: boolean; parameters: unknown[] }>((resolve) => {
      release = resolve
    })
    const api = makeApi({
      applyParameters: vi.fn(() => gate),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const pending = store.getState().applyDraftParameters()
    await vi.waitFor(() => expect(store.getState().applying).toBe(true))
    // 模拟用户在等待期间切换了项目
    store.setState({ projectEpoch: 99, sessionId: 's-new', project: { name: 'Other', source: 'hsf', path: '/other' } })
    release({ ok: true, parameters: [] })
    const ok = await pending

    expect(ok).toBe(false)
    // 新项目状态不被旧响应覆盖
    expect(store.getState().project?.path).toBe('/other')
    expect(store.getState().applying).toBe(false)
  })

  test('sourceActionBusy=true 时 compileCurrentProject 不调用外部 API', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.setState({ sourceActionBusy: true })

    await store.getState().compileCurrentProject()

    expect(api.compileProject).not.toHaveBeenCalled()
    expect(api.saveProjectScript).not.toHaveBeenCalled()
    expect(store.getState().lastError).toContain('source operation is in progress')
  })

  test('sourceActionBusy=true 时 sendChat 不调用外部 API', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.setState({ sourceActionBusy: true })

    await store.getState().sendChat('hello')

    expect(api.askAssistant).not.toHaveBeenCalled()
    expect(api.generateWithAssistant).not.toHaveBeenCalled()
    expect(store.getState().lastError).toContain('source operation is in progress')
  })
})

describe('R1-03 异常必须复位完整状态并显示错误', () => {
  test('api.saveProject reject 后 loading/busy 复位、脚本内容保留、下次可执行', async () => {
    const api = makeApi({
      saveProject: vi.fn(async () => {
        throw new Error('network down')
      }),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'save-fail-draft\n')

    const ok = await store.getState().saveProject()

    expect(ok).toBe(false)
    expect(store.getState().loading).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
    // flush 已成功（脚本已保存），dirty 已清；编辑器内容保留
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().scriptContents['3d.gdl']).toBe('save-fail-draft\n')
    expect(store.getState().lastError).toContain('network down')

    // 下一次操作可以执行（busy 不复位到卡住状态）
    store.setState({ lastError: null })
    expect(store.getState().sourceActionBusy).toBe(false)
  })

  test('api.exportHsfProject reject 后状态复位、旧项目草稿保留', async () => {
    const api = makeApi({
      exportHsfProject: vi.fn(async () => {
        throw new Error('export crashed')
      }),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'export-fail-draft\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().exportHsfProject('', 'FailCopy')

    expect(ok).toBe(false)
    expect(store.getState().loading).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().project?.path).toBe('/workspace/Chair')
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().lastError).toContain('export crashed')
  })

  test('参数 API reject 后 applying 复位、脚本已保存、内容保留、错误可见', async () => {
    const api = makeApi({
      addProjectParameter: vi.fn(async () => {
        throw new Error('param backend down')
      }),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const ok = await store.getState().addProjectParameter({ name: 'C', type_tag: 'Length', value: 1 })

    expect(ok).toBe(false)
    expect(store.getState().applying).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
    // flush 已成功，dirty 已清；内容保留
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().scriptContents['3d.gdl']).toBe('dirty\n')
    expect(store.getState().lastError).toContain('param backend down')
  })

  test('saveRevision API reject 后 revisionLoading 复位、脚本已保存', async () => {
    const api = makeApi({
      saveProjectRevision: vi.fn(async () => {
        throw new Error('revision backend down')
      }),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'revision-draft\n')

    const ok = await store.getState().saveRevision('r1 message')

    expect(ok).toBe(false)
    expect(store.getState().revisionLoading).toBe(false)
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().scriptContents['3d.gdl']).toBe('revision-draft\n')
    expect(store.getState().lastError).toContain('revision backend down')
  })

  test('脚本已保存但参数 API 返回 ok=false：如实提示“Scripts saved, but ...”', async () => {
    const api = makeApi({
      applyParameters: vi.fn(async () => ({ ok: false, error: 'param rejected', ...snapshot() })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().applyDraftParameters()

    expect(ok).toBe(false)
    expect(store.getState().dirtyScripts['3d.gdl']).toBe(false)
    expect(store.getState().draftParameters).toEqual({ A: 2 })
    expect(store.getState().lastError).toContain('Scripts saved')
  })
})

describe('R1-04 失效参数草稿提示准确', () => {
  test('全部字段失效时 drafts 清空且不谎称已保留', async () => {
    const api = makeApi({
      exportHsfProject: vi.fn(async (_parentDir = '', name = '') => ({
        ok: true,
        saved_to: `/exports/${name}`,
        ...snapshot({ name, source: 'hsf', path: `/exports/${name}` }),
        parameters: [{ name: 'B', type_tag: 'Length', value: '0.5' }],
      })),
    })
    const store = await loadedStore(api)
    await store.getState().setDraftParameter('A', 9)

    const ok = await store.getState().exportHsfProject('', 'AllDropped')

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({})
    const log = store.getState().compileLog.join('\n')
    expect(log).toContain('Parameter drafts dropped')
    expect(log).toContain('A')
    expect(log).not.toContain('Parameter drafts kept')
  })

  test('部分字段保留/部分失效：分别提示 kept 与 dropped', async () => {
    const api = makeApi({
      exportHsfProject: vi.fn(async (_parentDir = '', name = '') => ({
        ok: true,
        saved_to: `/exports/${name}`,
        ...snapshot({ name, source: 'hsf', path: `/exports/${name}` }),
        parameters: [
          { name: 'A', type_tag: 'Length', value: '1.0' },
          { name: 'B', type_tag: 'Length', value: '0.5' },
        ],
      })),
    })
    const store = await loadedStore(api)
    await store.getState().setDraftParameter('A', 9)
    await store.getState().setDraftParameter('gone', 99)

    const ok = await store.getState().exportHsfProject('', 'PartialKept')

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 9 })
    const log = store.getState().compileLog.join('\n')
    expect(log).toContain('Parameter drafts kept')
    expect(log).toContain('Parameter drafts dropped')
    expect(log).toContain('gone')
  })

  test('Apply 后删除参数：只清被删项，保留其它合法草稿', async () => {
    const api = makeApi({
      deleteProjectParameter: vi.fn(async () => ({
        ok: true,
        deleted: 'B',
        ...snapshot(),
        parameters: [{ name: 'A', type_tag: 'Length', value: '1.0' }],
      })),
    })
    const store = await loadedStore(api)
    await store.getState().setDraftParameter('A', 9)
    await store.getState().setDraftParameter('B', 99)

    const ok = await store.getState().deleteProjectParameter('B')

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 9 })
  })
})

// R2 返工：两个原返工遗漏的最小修复。
describe('R2-A 普通 Save 失效参数草稿告知', () => {
  test('Save 后某参数草稿失效：清理该草稿并在 compileLog 提示字段名', async () => {
    const api = makeApi({
      fetchSnapshot: vi.fn(async () => ({
        ...snapshot(),
        parameters: [{ name: 'B', type_tag: 'Length', value: '0.5' }],
      })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('vl.gdl', '! vl dirty\n')
    await store.getState().setDraftParameter('A', 9)

    const ok = await store.getState().saveProject()

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({})
    const log = store.getState().compileLog.join('\n')
    expect(log).toContain('Saved HSF source')
    expect(log).toContain('Parameter drafts dropped')
    expect(log).toContain('A')
    expect(log).not.toContain('Parameter drafts kept')
  })

  test('Save 后部分参数草稿失效：保留合法项并提示失效字段', async () => {
    const api = makeApi({
      fetchSnapshot: vi.fn(async () => ({
        ...snapshot(),
        parameters: [
          { name: 'A', type_tag: 'Length', value: '1.0' },
          { name: 'B', type_tag: 'Length', value: '0.5' },
        ],
      })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('vl.gdl', '! vl dirty\n')
    await store.getState().setDraftParameter('A', 9)
    await store.getState().setDraftParameter('gone', 99)

    const ok = await store.getState().saveProject()

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 9 })
    const log = store.getState().compileLog.join('\n')
    expect(log).toContain('Parameter drafts kept')
    expect(log).toContain('Parameter drafts dropped')
    expect(log).toContain('gone')
    expect(api.applyParameters).not.toHaveBeenCalled()
  })

  test('Save 后全部参数草稿仍合法：只提示 kept，不提示 dropped', async () => {
    const api = makeApi()
    const store = await loadedStore(api)
    store.getState().updateScriptContent('vl.gdl', '! vl dirty\n')
    await store.getState().setDraftParameter('A', 9)

    const ok = await store.getState().saveProject()

    expect(ok).toBe(true)
    expect(store.getState().draftParameters).toEqual({ A: 9 })
    const log = store.getState().compileLog.join('\n')
    expect(log).toContain('Parameter drafts kept')
    expect(log).not.toContain('Parameter drafts dropped')
  })
})

describe('R2-B 过期响应不覆盖新项目状态', () => {
  async function staleResponseMatrix(
    action: () => Promise<boolean>,
    release: (value: any) => void,
    store: ReturnType<typeof createWorkbenchStore>,
  ) {
    const pending = action()
    await vi.waitFor(() => expect(store.getState().applying).toBe(true))
    // 旧请求等待期间切换到新项目/epoch
    store.setState({
      projectEpoch: 99,
      sessionId: 's-new',
      project: { name: 'NEW', source: 'hsf', path: '/new' },
      lastError: 'NEW_PROJECT_ERROR',
    })
    return { pending, release }
  }

  test('过期参数成功响应：不覆盖新项目 project/草稿/错误', async () => {
    let release!: (value: { ok: boolean; parameters: unknown[] }) => void
    const gate = new Promise<{ ok: boolean; parameters: unknown[] }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ applyParameters: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const { pending } = await staleResponseMatrix(
      () => store.getState().applyDraftParameters(),
      release,
      store,
    )
    release({ ok: true, parameters: [] })
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
    expect(store.getState().draftParameters).toEqual({ A: 2 })
  })

  test('过期参数失败响应：不覆盖新项目错误状态', async () => {
    let release!: (value: { ok: boolean; error?: string }) => void
    const gate = new Promise<{ ok: boolean; error?: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ applyParameters: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const { pending } = await staleResponseMatrix(
      () => store.getState().applyDraftParameters(),
      release,
      store,
    )
    release({ ok: false, error: 'OLD_PROJECT_ERROR' })
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
  })

  test('过期参数 reject：不覆盖新项目错误状态', async () => {
    let rejectGate!: (err: Error) => void
    const gate = new Promise<{ ok: boolean }>((_, reject) => {
      rejectGate = reject
    })
    // 消费可能的未处理 rejection 跟踪，实际仍由 action catch 处理
    gate.catch(() => {})
    const api = makeApi({ applyParameters: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const pending = store.getState().applyDraftParameters()
    await vi.waitFor(() => expect(store.getState().applying).toBe(true))
    store.setState({
      projectEpoch: 99,
      sessionId: 's-new',
      project: { name: 'NEW', source: 'hsf', path: '/new' },
      lastError: 'NEW_PROJECT_ERROR',
    })
    rejectGate(new Error('OLD_PROJECT_REJECT'))
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
  })

  test('同项目参数失败仍显示错误并可再次执行', async () => {
    const api = makeApi({
      applyParameters: vi.fn(async () => ({ ok: false, error: 'SAME_PROJECT_FAIL', ...snapshot() })),
    })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')
    await store.getState().setDraftParameter('A', 2)

    const ok = await store.getState().applyDraftParameters()

    expect(ok).toBe(false)
    expect(store.getState().lastError).toBe('Scripts saved, but apply parameters failed: SAME_PROJECT_FAIL')
    expect(store.getState().sourceActionBusy).toBe(false)
    expect(store.getState().applying).toBe(false)
    // 下一次操作可执行
    const ok2 = await store.getState().applyDraftParameters()
    expect(ok2).toBe(false)
  })

  test('Save 过期失败响应：不覆盖新项目错误', async () => {
    let release!: (value: { ok: boolean; error?: string }) => void
    const gate = new Promise<{ ok: boolean; error?: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ saveProject: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const pending = store.getState().saveProject()
    await vi.waitFor(() => expect(store.getState().loading).toBe(true))
    store.setState({
      projectEpoch: 99,
      sessionId: 's-new',
      project: { name: 'NEW', source: 'hsf', path: '/new' },
      lastError: 'NEW_PROJECT_ERROR',
    })
    release({ ok: false, error: 'OLD_SAVE_ERROR' })
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
  })

  test('Save As 过期失败响应：不覆盖新项目错误', async () => {
    let release!: (value: { ok: boolean; error?: string }) => void
    const gate = new Promise<{ ok: boolean; error?: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ exportHsfProject: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const pending = store.getState().exportHsfProject('', 'Copy')
    await vi.waitFor(() => expect(store.getState().loading).toBe(true))
    store.setState({
      projectEpoch: 99,
      sessionId: 's-new',
      project: { name: 'NEW', source: 'hsf', path: '/new' },
      lastError: 'NEW_PROJECT_ERROR',
    })
    release({ ok: false, error: 'OLD_EXPORT_ERROR' })
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
  })

  test('Save Revision 过期失败响应：不覆盖新项目错误', async () => {
    let release!: (value: { ok: boolean; error?: string }) => void
    const gate = new Promise<{ ok: boolean; error?: string }>((resolve) => {
      release = resolve
    })
    const api = makeApi({ saveProjectRevision: vi.fn(() => gate) })
    const store = await loadedStore(api)
    store.getState().updateScriptContent('3d.gdl', 'dirty\n')

    const pending = store.getState().saveRevision('msg')
    await vi.waitFor(() => expect(store.getState().revisionLoading).toBe(true))
    store.setState({
      projectEpoch: 99,
      sessionId: 's-new',
      project: { name: 'NEW', source: 'hsf', path: '/new' },
      lastError: 'NEW_PROJECT_ERROR',
    })
    release({ ok: false, error: 'OLD_REVISION_ERROR' })
    const ok = await pending

    expect(ok).toBe(false)
    expect(store.getState().project?.path).toBe('/new')
    expect(store.getState().lastError).toBe('NEW_PROJECT_ERROR')
  })
})
