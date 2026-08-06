import type { WorkspaceInfo, WorkspaceScanResult } from '../../api/types'
import type { WorkbenchActionContext, WorkbenchSet } from '../workbenchStoreTypes'

function toWorkspaceInfo(result: WorkspaceScanResult, fallbackPath: string): WorkspaceInfo {
  return {
    path: result.workspace ?? fallbackPath,
    project_count: result.project_count ?? (result.projects ?? []).length,
    projects: result.projects ?? [],
  }
}

/** open/init 成功共用：落 workspace、清搜索态、清 hint。 */
function applyWorkspaceOpened(
  set: WorkbenchSet,
  result: WorkspaceScanResult,
  fallbackPath: string,
) {
  set({
    workspace: toWorkspaceInfo(result, fallbackPath),
    workspaceInitHint: null,
    workspaceSearchHits: [],
    workspaceSearchQuery: null,
    workspaceSearchResult: null,
    workspaceBusy: false,
  })
}

/** open/init 失败共用：not_a_workspace 时把尝试路径记入 hint 引导条，否则仅 lastError。 */
function applyWorkspaceOpenFailed(
  set: WorkbenchSet,
  result: WorkspaceScanResult,
  attemptedPath: string,
  fallbackMessage: string,
) {
  set({
    workspaceBusy: false,
    lastError: result.error ?? fallbackMessage,
    workspaceInitHint: result.code === 'not_a_workspace' ? attemptedPath : null,
  })
}

/**
 * 工作区切片：open / init（一条龙：init 成功后紧跟 open）/ close / refresh / search。
 *
 * 项目切换复用现有 loadProjectPath（别新写一套）；projectEpoch 防串扰由
 * hydrateSnapshot 在 load 类 action 里处理，本切片不触碰项目状态。
 */
export function createWorkspaceActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async openWorkspace(path: string) {
      const trimmed = path.trim()
      if (!trimmed) return
      set({ workspaceBusy: true, lastError: null })
      const result = await api.workspaceOpen(trimmed)
      if (result.ok === false) {
        applyWorkspaceOpenFailed(set, result, trimmed, 'Failed to open workspace.')
        return
      }
      applyWorkspaceOpened(set, result, trimmed)
    },

    async initWorkspace(path: string) {
      const trimmed = path.trim()
      if (!trimmed) return
      set({ workspaceBusy: true, lastError: null })
      const initResult = await api.workspaceInit(trimmed)
      if (initResult.ok === false) {
        set({
          workspaceBusy: false,
          lastError: initResult.error ?? 'Failed to initialize workspace.',
        })
        return
      }
      // init 只建四区 + workspace.toml，不含 scan；紧跟 open 拿 scan 结果
      const openResult = await api.workspaceOpen(trimmed)
      if (openResult.ok === false) {
        applyWorkspaceOpenFailed(set, openResult, trimmed, 'Workspace initialized but failed to open.')
        return
      }
      applyWorkspaceOpened(set, openResult, trimmed)
    },

    async closeWorkspace() {
      set({ workspaceBusy: true, lastError: null })
      await api.workspaceClose()
      set({
        workspace: null,
        workspaceInitHint: null,
        workspaceSearchHits: [],
        workspaceSearchQuery: null,
        workspaceSearchResult: null,
        workspaceBusy: false,
      })
    },

    async refreshWorkspace() {
      if (!get().workspace) return
      set({ workspaceBusy: true, lastError: null })
      const result = await api.workspaceScan()
      if (result.ok === false) {
        set({
          workspaceBusy: false,
          lastError: result.error ?? 'Failed to refresh workspace.',
        })
        return
      }
      set({
        workspace: toWorkspaceInfo(result, get().workspace?.path ?? ''),
        workspaceBusy: false,
      })
    },

    async searchWorkspace(query: string) {
      if (!get().workspace) return
      const trimmed = query.trim()
      if (!trimmed) {
        // 清空搜索：不空打后端
        set({ workspaceSearching: false, workspaceSearchQuery: null, workspaceSearchHits: [], workspaceSearchResult: null })
        return
      }
      set({ workspaceSearching: true, workspaceSearchQuery: trimmed })
      const result = await api.workspaceSearch(trimmed)
      set({
        workspaceSearching: false,
        workspaceSearchResult: result,
        workspaceSearchHits: result.ok ? result.hits ?? [] : [],
      })
    },

    async browseWorkspaceDirectory() {
      const result = await api.chooseOutputDirectory()
      if (result.ok && result.path) {
        return result.path
      }
      return null
    },

    clearWorkspaceInitHint() {
      set({ workspaceInitHint: null })
    },

    async trashWorkspaceProject(path: string) {
      if (!get().workspace) return
      set({ workspaceBusy: true, lastError: null })
      const result = await api.trashWorkspaceProject(path)
      if (result.ok === false) {
        set({
          workspaceBusy: false,
          lastError: result.error ?? 'Failed to move project to trash.',
        })
        return
      }
      // 成功：刷新列表（scan）
      await get().refreshWorkspace()
    },
  }
}
