import type { ProjectWorkspaceRefreshOptions, WorkbenchActionContext } from '../workbenchStoreTypes'
import { normalizeScriptName, nowTimeText, pruneDirtyScripts, selectPreferredScript } from '../workbenchStoreUtils'

export function createScriptActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async refreshProjectWorkspace(options: ProjectWorkspaceRefreshOptions = {}) {
      const preferredScriptName = normalizeScriptName(options.preferredScriptName ?? '')
      const refreshAllScripts = options.refreshAllScripts ?? true
      const refreshPreview = options.refreshPreview ?? true
      const refreshParameters = options.refreshParameters ?? false
      const runDiagnostics = options.runDiagnostics ?? false

      await get().loadScripts()
      const existingScripts = get().scripts.filter((script) => script.exists)
      const targetNames = refreshAllScripts
        ? existingScripts.map((script) => script.name)
        : [preferredScriptName || get().activeScriptName || ''].filter(Boolean)

      for (const scriptName of [...new Set(targetNames)]) {
        const updated = await api.getProjectScript(scriptName)
        if (updated) {
          set((state) => ({
            scriptContents: { ...state.scriptContents, [scriptName]: updated.content },
            dirtyScripts: { ...state.dirtyScripts, [scriptName]: false },
          }))
        }
      }

      if (preferredScriptName && existingScripts.some((script) => script.name === preferredScriptName)) {
        await get().openScript(preferredScriptName)
      }

      if (refreshPreview) {
        const preview = await api.fetchPreview({})
        set({ preview, warnings: preview.warnings ?? [] })
      }

      if (runDiagnostics) {
        await get().runMockCompile()
      }

      // HF3：MODIFY 交付后参数快照重拉（vl.gdl VALUES / paramlist 可能已变——
      // 新枚举项、新参数立即出现在参数面板，不需要重开项目）。snapshot 回落
      // （project=null）时保留现有 UI 状态。绝不写 draftParameters：用户未保存
      // 的草稿值不受影响（元数据刷新与草稿值正交；任务完成路径的草稿清空语义
      // 由调用方决定）。preview 不在此覆盖（调用方显式传 refreshPreview 或
      // 保留流式结果）。
      if (refreshParameters) {
        const snapshot = await api.fetchSnapshot()
        if (snapshot.project !== null) {
          set({
            project: snapshot.project,
            parameters: snapshot.parameters,
          })
        }
      }
    },

    async loadScripts() {
      set({ scriptLoading: true })
      const result = await api.listProjectScripts()
      const scripts = result.scripts ?? []
      const activeScriptName = selectPreferredScript(scripts, get().activeScriptName)
      set((state) => ({
        scripts,
        activeScriptName,
        dirtyScripts: pruneDirtyScripts(state.dirtyScripts, scripts),
        scriptLoading: false,
      }))
      if (activeScriptName && !get().scriptContents[activeScriptName]) {
        await get().openScript(activeScriptName)
      }
    },

    async openScript(name: string) {
      const target = name.trim()
      if (!target) return
      const cached = get().scriptContents[target]
      if (typeof cached === 'string') {
        set({ activeScriptName: target, lastError: null })
        return
      }
      set({ scriptLoading: true, lastError: null })
      const result = await api.getProjectScript(target)
      if (!result) {
        set({
          scriptLoading: false,
          lastError: `Failed to open script: ${target}`,
        })
        return
      }
      set((state) => ({
        scriptLoading: false,
        activeScriptName: target,
        scriptContents: { ...state.scriptContents, [target]: result.content },
      }))
    },

    updateActiveScriptContent(content: string) {
      const activeScriptName = get().activeScriptName
      if (!activeScriptName) return
      set((state) => ({
        scriptContents: { ...state.scriptContents, [activeScriptName]: content },
        dirtyScripts: { ...state.dirtyScripts, [activeScriptName]: true },
      }))
    },

    // P11：参数面板「参数脚本」tab 按名编辑 vl.gdl，不切换 activeScriptName
    // （主编辑器当前打开的脚本不受影响）。
    updateScriptContent(name: string, content: string) {
      set((state) => ({
        scriptContents: { ...state.scriptContents, [name]: content },
        dirtyScripts: { ...state.dirtyScripts, [name]: true },
      }))
    },

    // P11：参数面板「参数脚本」tab 的保存：复用脚本保存链路（saveProjectScript +
    // 脏标记清理）；vl.gdl 保存后 VALUES 可能已变，重新拉快照刷新参数枚举
    // （options/range）与预览，让「参数」tab 反映最新脚本内容。
    async saveScript(name: string) {
      const content = get().scriptContents[name]
      if (typeof content !== 'string') return
      set({ scriptSaving: true, lastError: null })
      const result = await api.saveProjectScript(name, content)
      if (!result.success) {
        set({ scriptSaving: false, lastError: result.error ?? `Failed to save script: ${name}` })
        return
      }
      const snapshot = await api.fetchSnapshot()
      // 快照刷新失败会回落成空 fallback（project=null）：此时保留现有 UI 状态，
      // 不把参数面板清空（保存本身已成功）。
      const snapshotFields =
        snapshot.project !== null
          ? {
              project: snapshot.project,
              parameters: snapshot.parameters,
              preview: snapshot.preview,
              warnings: snapshot.warnings ?? [],
            }
          : {}
      set((state) => ({
        scriptSaving: false,
        dirtyScripts: { ...state.dirtyScripts, [name]: false },
        lastSavedAt: nowTimeText(),
        compileLog: [`Saved ${name} at ${result.saved_at}`, ...state.compileLog].slice(0, 20),
        ...snapshotFields,
      }))
    },

    // 统一的“读当前脚本前先落盘”入口：编译、AI 生成等操作必须先走这里，
    // 否则后端会基于旧脚本工作，并在刷新时覆盖用户未保存的手改。
    async flushDirtyScripts() {
      const dirtyScriptNames = Object.entries(get().dirtyScripts)
        .filter(([, dirty]) => dirty)
        .map(([name]) => name)
      let didSave = false

      for (const scriptName of dirtyScriptNames) {
        const content = get().scriptContents[scriptName]
        if (typeof content !== 'string') continue

        const result = await api.saveProjectScript(scriptName, content)
        if (!result.success) {
          set({ lastError: result.error ?? `Failed to save ${scriptName}.` })
          return { ok: false, didSave }
        }

        set((state) => ({
          dirtyScripts: { ...state.dirtyScripts, [scriptName]: false },
          lastSavedAt: nowTimeText(),
          compileLog: [`Saved ${scriptName}`, ...state.compileLog].slice(0, 20),
        }))
        didSave = true
      }

      return { ok: true, didSave }
    },

    async saveActiveScript() {
      const activeScriptName = get().activeScriptName
      if (!activeScriptName) return
      const content = get().scriptContents[activeScriptName]
      if (typeof content !== 'string') return
      set({ scriptSaving: true, lastError: null })
      const result = await api.saveProjectScript(activeScriptName, content)
      if (result.success) {
        set((state) => ({
          scriptSaving: false,
          dirtyScripts: { ...state.dirtyScripts, [activeScriptName]: false },
          lastSavedAt: nowTimeText(),
          compileLog: [`Saved ${activeScriptName} at ${result.saved_at}`, ...state.compileLog].slice(0, 20),
        }))
        await get().refreshProjectWorkspace({
          preferredScriptName: activeScriptName,
          refreshAllScripts: false,
          refreshPreview: true,
          runDiagnostics: true,
        })
        // P11：vl.gdl 保存后 VALUES 可能已变 → 重新拉快照，参数枚举（options/range）
        // 与预览反映最新脚本内容（主编辑器保存路径与参数面板「参数脚本」tab 一致）。
        if (activeScriptName === 'vl.gdl') {
          const snapshot = await api.fetchSnapshot()
          // 快照回落（project=null）时保留现有 UI 状态，避免清空参数面板
          if (snapshot.project !== null) {
            set({
              parameters: snapshot.parameters,
              project: snapshot.project,
              preview: snapshot.preview,
              warnings: snapshot.warnings ?? [],
            })
          }
        }
        return
      }
      set({
        scriptSaving: false,
        lastError: result.error ?? `Failed to save script: ${activeScriptName}`,
      })
    },
  }
}
