import type { WorkspaceInfo, WorkspaceScanResult } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

function toWorkspaceInfo(result: WorkspaceScanResult, fallbackPath: string): WorkspaceInfo {
  return {
    path: result.workspace ?? fallbackPath,
    project_count: result.project_count ?? (result.projects ?? []).length,
    projects: result.projects ?? [],
  }
}

/**
 * 工作区切片：open / close / refresh（scan）/ search。
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
        set({
          workspaceBusy: false,
          lastError: result.error ?? 'Failed to open workspace.',
        })
        return
      }
      set({
        workspace: toWorkspaceInfo(result, trimmed),
        workspaceSearchHits: [],
        workspaceSearchQuery: null,
        workspaceSearchResult: null,
        workspaceBusy: false,
      })
    },

    async closeWorkspace() {
      set({ workspaceBusy: true, lastError: null })
      await api.workspaceClose()
      set({
        workspace: null,
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
  }
}
