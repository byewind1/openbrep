import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot } from '../workbenchStoreUtils'

export function createAssistantActions({ api, get, set }: WorkbenchActionContext) {
  async function persistAssistantHistory() {
    const result = await api.saveAssistantHistory(get().assistantMessages)
    if (!result.ok && result.error) {
      set({ lastError: result.error })
    }
  }

  return {
    setActiveRailPanel(panel: '3d' | '2d' | 'ai') {
      set({ activeRailPanel: panel })
    },

    async loadAssistantHistory() {
      const result = await api.listAssistantHistory()
      if (!result.ok) {
        if (result.error) {
          set({ lastError: result.error })
        }
        return
      }
      set({ assistantMessages: result.messages ?? [] })
    },

    async clearAssistantHistory() {
      const result = await api.clearAssistantHistory()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to clear assistant history.' })
        return
      }
      set({ assistantMessages: [] })
    },

    async sendAssistantMessage(message: string) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.askAssistant(trimmed)
      const reply = result.ok && result.assistant ? result.assistant.reply : result.error ?? 'Assistant request failed.'
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
      }))
      await persistAssistantHistory()
    },

    async createProjectFromPrompt(message: string) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.createProjectFromPrompt(trimmed, get().llmSettings.assistant_settings)
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set((state) => ({
          assistantBusy: false,
          assistantMessages: [
            ...state.assistantMessages,
            { role: 'assistant', content: result.error ?? 'Create request failed.' },
          ],
          lastError: result.error ?? 'Create request failed.',
        }))
        return
      }
      set(hydrateSnapshot(result, get().compilerSettings, get().llmSettings))
      await get().loadRecentProjects()
      await get().loadScripts()
      await get().loadRevisions()
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'assistant', content: result.assistant?.reply ?? 'Project created.' },
        ],
      }))
      await persistAssistantHistory()
    },

    async generateAssistantChanges(message: string) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.generateWithAssistant(trimmed, get().llmSettings.assistant_settings)
      const changedFiles = result.assistant?.changed_files ?? []
      const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
      const reply =
        result.ok && result.assistant
          ? `${result.assistant.reply}${suffix}`
          : result.error ?? 'Generation request failed.'
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
        preview: result.preview ?? state.preview,
        warnings: result.warnings ?? result.preview?.warnings ?? state.warnings,
        draftParameters: {},
      }))
      await persistAssistantHistory()
      if (result.ok) {
        await get().refreshProjectWorkspace({
          preferredScriptName: changedFiles[0] ?? '',
          refreshAllScripts: true,
          refreshPreview: false,
          runDiagnostics: true,
        })
      }
    },
  }
}
