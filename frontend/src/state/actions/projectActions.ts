import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot, nowTimeText } from '../workbenchStoreUtils'
import type { HsfExportResult, WorkbenchSnapshot } from '../../api/types'
import {
  beginSourceAction,
  captureProjectIdentity,
  collectScriptOverrides,
  endSourceAction,
  formatDraftKeptNotice,
  keepValidDraftParameters,
  sameProjectIdentity,
} from './sourceActionHelpers'

export function createProjectActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async load() {
      set({ loading: true, lastError: null })
      const snapshot = await api.fetchSnapshot()
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async loadProjectPath(path: string) {
      const normalizedPath = path.trim()
      if (!normalizedPath) return
      set({ loading: true, lastError: null })
      const snapshot = await api.loadProjectPath(normalizedPath)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? `Failed to open HSF project: ${normalizedPath}`,
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async newProject() {
      set({ loading: true, lastError: null })
      const snapshot = await api.newProject()
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to create a new project.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      if (!snapshot.project) {
        set({ loading: false })
        return
      }
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async importGdlFile(path = '') {
      set({ loading: true, lastError: null })
      const snapshot = await api.importGdlFile(path)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to import GDL file.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async importGsmFile(path = '') {
      set({ loading: true, lastError: null })
      const snapshot = await api.importGsmFile(path)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to import GSM file.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async importBlenderScript(path = '') {
      set({ loading: true, lastError: null })
      const snapshot = await api.importBlenderScript(path)
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to import Blender script.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    // SF1 Save As：把当前脚本草稿（scriptOverrides）写入新副本，不顺手改写原项目；
    // 失败/取消保留旧项目、草稿与 dirty 状态，不 hydrate 空 fallback。
    // 成功后激活副本；仍合法的参数草稿保留到新项目（不自动 Apply）。
    // R1-01：调用者未显式传 overrides 时由 action 统一收集；缺失 buffer 明确失败。
    async exportHsfProject(parentDir = '', name = '', scriptOverrides?: Record<string, string>) {
      const guard = beginSourceAction(get, set, 'save-as')
      if (!guard.ok) {
        set({ lastError: guard.reason ?? 'Save As is blocked.' })
        return false
      }
      const identity = captureProjectIdentity(get())
      try {
        set({ loading: true, lastError: null })
        let overrides = scriptOverrides
        if (overrides === undefined) {
          const collected = collectScriptOverrides(get())
          if (collected.error) {
            set({ loading: false, lastError: collected.error })
            return false
          }
          overrides = collected.overrides
        }
        const result = await api.exportHsfProject(parentDir, name, overrides)
        if (result.ok === false) {
          // 原生文件框取消：不当成错误；其余失败保留全部草稿
          set({ loading: false, lastError: result.cancelled ? null : result.error ?? 'Failed to export HSF project.' })
          return false
        }
        if (!sameProjectIdentity(get(), identity)) {
          set({ loading: false })
          return false
        }
        const keptDrafts = keepValidDraftParameters(
          get().draftParameters,
          (result.parameters ?? []).map((parameter) => parameter.name),
        )
        set(hydrateSnapshot(result, get().compilerSettings, get().llmSettings))
        await get().loadRecentProjects()
        await get().loadScripts()
        await get().loadRevisions()
        await get().loadAssistantHistory()
        await get().loadMemoryStatus()
        set((state) => ({
          loading: false,
          lastSavedAt: nowTimeText(),
          needsSaveAs: false,
          draftParameters: keptDrafts.kept,
          compileLog: [
            ...(result.saved_to ? [`Saved HSF source: ${result.saved_to}`] : []),
            // R1-04：只有确实保留的草稿才提示；被丢弃的字段明确列出
            ...formatDraftKeptNotice(keptDrafts.kept, keptDrafts.dropped),
            ...state.compileLog,
          ].slice(0, 20),
        }))
        return true
      } catch (exc) {
        set({
          loading: false,
          lastError: exc instanceof Error ? exc.message : String(exc ?? 'Failed to export HSF project.'),
        })
        return false
      } finally {
        endSourceAction(set)
      }
    },

    // SF1 Save（已有路径）：先 flush 全部脏脚本，再保存项目；不自动 Apply 参数草稿。
    // 成功后不清空同项目编辑状态/参数草稿，只合并项目与参数元数据；
    // 无路径项目走 needsSaveAs 命名引导（不先写临时目录、不丢草稿）。
    // R1-03：异常分支复位 loading/sourceActionBusy 并显示错误。
    async saveProject() {
      const guard = beginSourceAction(get, set, 'save')
      if (!guard.ok) {
        set({ lastError: guard.reason ?? 'Save is blocked.' })
        return false
      }
      const identity = captureProjectIdentity(get())
      try {
        const hasPath = Boolean(get().project?.path)
        if (hasPath) {
          const flushed = await get().flushDirtyScripts()
          if (!flushed.ok) {
            set({ lastError: get().lastError ?? flushed.error ?? 'Failed to save scripts.' })
            return false
          }
          if (!sameProjectIdentity(get(), identity)) return false
        }
        set({ loading: true, lastError: null })
        const result = await api.saveProject()
        if (result.ok === false) {
          if (result.needs_save_as) {
            // P7c：新建空白项目首次保存 → 置 needsSaveAs，由组件弹命名引导
            // ThemedDialog（默认「未命名构件」），不再把"Use Save As HSF"当错误展示。
            set({ loading: false, needsSaveAs: true })
            return false
          }
          set({
            loading: false,
            lastError: result.error ?? 'Failed to save HSF project.',
          })
          return false
        }
        if (!sameProjectIdentity(get(), identity)) {
          set({ loading: false })
          return false
        }
        // 同项目合并：保留 scriptContents/dirtyScripts/draftParameters，
        // 只更新项目/参数元数据（脏 XML 落盘后后端参数可能已变化）与保存结果。
        set((state) => ({
          loading: false,
          project: result.project ?? state.project,
          parameters: result.parameters ?? state.parameters,
          preview: result.preview ?? state.preview,
          warnings: result.warnings ?? state.warnings,
          lastSavedAt: nowTimeText(),
          needsSaveAs: false,
          compileLog: result.saved_to ? [`Saved HSF source: ${result.saved_to}`, ...state.compileLog].slice(0, 20) : state.compileLog,
        }))
        // 保存了脏 paramlist.xml 后，同项目刷新参数元数据（保留合法参数草稿）。
        if (Object.values(get().dirtyScripts).every((dirty) => !dirty)) {
          try {
            const snapshot = await api.fetchSnapshot()
            if (snapshot.project !== null && sameProjectIdentity(get(), identity)) {
              const kept = keepValidDraftParameters(
                get().draftParameters,
                (snapshot.parameters ?? []).map((parameter) => parameter.name),
              )
              set({ project: snapshot.project, parameters: snapshot.parameters, draftParameters: kept.kept })
            }
          } catch {
            // best-effort：刷新失败不改变 save 成功语义
          }
        }
        return true
      } catch (exc) {
        set({
          loading: false,
          lastError: exc instanceof Error ? exc.message : String(exc ?? 'Failed to save HSF project.'),
        })
        return false
      } finally {
        endSourceAction(set)
      }
    },

    async saveProjectAs(parentDir = '', name = '', scriptOverrides?: Record<string, string>) {
      return get().exportHsfProject(parentDir, name, scriptOverrides)
    },

    async clearNeedsSaveAs() {
      set({ needsSaveAs: false })
    },

    async closeProject() {
      set({ loading: true, lastError: null })
      const snapshot = await api.closeProject()
      if (snapshot.ok === false) {
        set({
          loading: false,
          lastError: snapshot.error ?? 'Failed to close current project.',
        })
        return
      }
      set(hydrateSnapshot(snapshot, get().compilerSettings, get().llmSettings))
      if (!snapshot.project) {
        set({ loading: false })
        return
      }
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async browseProjectDirectory() {
      set({ loading: true, lastError: null })
      const result = await api.chooseProjectDirectory()
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({
          loading: false,
          lastError: result.cancelled ? null : result.error ?? 'Failed to open HSF project directory.',
        })
        return
      }
      set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadAssistantHistory()
      await get().loadMemoryStatus()
      set({ loading: false })
    },

    async loadRecentProjects() {
      const result = await api.listRecentProjects()
      if (result.ok) {
        set({ recentProjects: result.projects ?? [] })
      }
    },
  }
}
