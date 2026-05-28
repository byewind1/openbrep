import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createAssistantActions({ api, get, set }: WorkbenchActionContext) {
  return {
    setActiveRailPanel(panel: '3d' | 'ai') {
      set({ activeRailPanel: panel })
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
      if (result.ok) {
        await get().loadScripts()
        const refreshedScripts = get().scripts.filter((script) => script.exists)
        for (const script of refreshedScripts) {
          const updated = await api.getProjectScript(script.name)
          if (updated) {
            set((state) => ({
              scriptContents: { ...state.scriptContents, [script.name]: updated.content },
              dirtyScripts: { ...state.dirtyScripts, [script.name]: false },
            }))
          }
        }
      }
    },
  }
}
