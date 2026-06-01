import type { AssistantImageAttachment } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { formatAssistantRequestError, hydrateSnapshot, normalizeScriptName } from '../workbenchStoreUtils'

export function createAssistantActions({ api, get, set }: WorkbenchActionContext) {
  function userMessageContent(message: string, image?: AssistantImageAttachment | null) {
    return image ? `${message}\n[image: ${image.name}]` : message
  }

  async function persistAssistantHistory() {
    const result = await api.saveAssistantHistory(get().assistantMessages)
    if (!result.ok && result.error) {
      set({ lastError: result.error })
    }
  }

  return {
    setActiveRailPanel(panel: '3d' | '2d' | 'inspect' | 'ai') {
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

    async adoptAssistantMessageCode(index: number) {
      const message = get().assistantMessages[index]
      if (!message || message.role !== 'assistant') {
        set({ lastError: 'Select an assistant message with code to adopt.' })
        return
      }
      const result = await api.extractAssistantCodeBlocks(message.content)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to extract code from assistant message.' })
        return
      }
      if (!result.blocks.length) {
        set({ lastError: 'No GDL or XML code blocks found in this assistant message.' })
        return
      }
      const normalizedBlocks = result.blocks
        .map((block) => ({
          scriptName: normalizeScriptName(block.script_name || block.path.split('/').pop() || ''),
          content: block.content,
        }))
        .filter((block) => block.scriptName && typeof block.content === 'string')
      if (!normalizedBlocks.length) {
        set({ lastError: 'No supported script files found in this assistant message.' })
        return
      }
      set((state) => {
        const scriptContents = { ...state.scriptContents }
        const dirtyScripts = { ...state.dirtyScripts }
        for (const block of normalizedBlocks) {
          scriptContents[block.scriptName] = block.content
          dirtyScripts[block.scriptName] = true
        }
        return {
          activeScriptName: normalizedBlocks[0].scriptName,
          scriptContents,
          dirtyScripts,
          lastError: null,
          compileLog: [`Adopted code from assistant history: ${normalizedBlocks.map((block) => block.scriptName).join(', ')}`, ...state.compileLog].slice(0, 20),
        }
      })
    },

    async sendAssistantMessage(message: string) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: trimmed }],
      }))
      const result = await api.askAssistant(trimmed)
      const reply =
        result.ok && result.assistant
          ? result.assistant.reply
          : formatAssistantRequestError(result.error, 'Assistant request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
        lastError: result.ok ? null : reply,
      }))
      await persistAssistantHistory()
    },

    async createProjectFromPrompt(message: string, image: AssistantImageAttachment | null = null) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: userMessageContent(trimmed, image) }],
      }))
      const result = await api.createProjectFromPrompt(trimmed, get().llmSettings.assistant_settings, image)
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        const error = formatAssistantRequestError(result.error, 'Create request failed.')
        set((state) => ({
          assistantBusy: false,
          assistantMessages: [
            ...state.assistantMessages,
            { role: 'assistant', content: error },
          ],
          lastError: error,
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

    async generateAssistantChanges(message: string, image: AssistantImageAttachment | null = null) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [...state.assistantMessages, { role: 'user', content: userMessageContent(trimmed, image) }],
      }))
      const result = await api.generateWithAssistant(trimmed, get().llmSettings.assistant_settings, image)
      const changedFiles = result.assistant?.changed_files ?? []
      const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
      const reply =
        result.ok && result.assistant
          ? `${result.assistant.reply}${suffix}`
          : formatAssistantRequestError(result.error, 'Generation request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: [...state.assistantMessages, { role: 'assistant', content: reply }],
        lastError: result.ok ? null : reply,
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
