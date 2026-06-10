import type { AssistantImageAttachment } from '../../api/types'
import type { AssistantMessage } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { formatAssistantRequestError, hydrateSnapshot, normalizeScriptName } from '../workbenchStoreUtils'

const ASSISTANT_PENDING_PREFIX = 'Thinking...'

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

  // 长操作期间用户切换了项目 → 丢弃过期结果，防止写进新项目的 state
  function projectSwitchedSince(epochAtStart: number) {
    return get().projectEpoch !== epochAtStart
  }

  function discardStaleResult(note: string) {
    set((state) => ({
      assistantBusy: false,
      compileLog: [note, ...state.compileLog].slice(0, 20),
    }))
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
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: trimmed },
          { role: 'assistant', content: pendingAssistantMessage('explain') },
        ],
      }))
      const epoch = get().projectEpoch
      const result = await api.askAssistant(trimmed)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Assistant reply discarded: project switched during the request.')
        return
      }
      const reply =
        result.ok && result.assistant
          ? result.assistant.reply
          : formatAssistantRequestError(result.error, 'Assistant request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, reply),
        lastError: result.ok ? null : reply,
      }))
      await persistAssistantHistory()
    },

    async createProjectFromPrompt(message: string, image: AssistantImageAttachment | null = null) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: userMessageContent(trimmed, image) },
          { role: 'assistant', content: pendingAssistantMessage('create', image) },
        ],
      }))
      const epoch = get().projectEpoch
      const result = await api.createProjectFromPrompt(trimmed, get().llmSettings.assistant_settings, image)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult(
          result.ok && result.project
            ? `Project "${result.project.name}" was created, but the workspace switched meanwhile. Open it from recent projects.`
            : 'Create result discarded: project switched during the request.',
        )
        return
      }
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        const error = formatAssistantRequestError(result.error, 'Create request failed.')
        set((state) => ({
          assistantBusy: false,
          assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
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
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          `${result.assistant?.reply ?? 'Project created.'}${formatAssistantEventSummary(result.events)}`,
        ),
      }))
      await persistAssistantHistory()
    },

    async generateAssistantChanges(message: string, image: AssistantImageAttachment | null = null) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: userMessageContent(trimmed, image) },
          { role: 'assistant', content: pendingAssistantMessage('generate', image) },
        ],
      }))
      // 生成基于磁盘上的 HSF，先把编辑器手改落盘，否则会被生成结果静默覆盖
      const flushed = await get().flushDirtyScripts()
      if (!flushed.ok) {
        const error = get().lastError ?? 'Failed to save edited scripts before generation.'
        set((state) => ({
          assistantBusy: false,
          assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
        }))
        return
      }
      const epoch = get().projectEpoch
      const result = await api.generateWithAssistant(trimmed, get().llmSettings.assistant_settings, image)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Generation result discarded: project switched during the request.')
        return
      }
      const changedFiles = result.assistant?.changed_files ?? []
      const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
      const eventSummary = formatAssistantEventSummary(result.events)
      const reply =
        result.ok && result.assistant
          ? `${result.assistant.reply}${suffix}${eventSummary}`
          : formatAssistantRequestError(result.error, 'Generation request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, reply),
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

function pendingAssistantMessage(action: 'explain' | 'create' | 'generate', image?: AssistantImageAttachment | null) {
  const steps =
    action === 'generate'
      ? [
          'Inspecting the loaded HSF project.',
          image ? `Reading the attached reference image: ${image.name}.` : 'Preparing generation context.',
          'Calling the configured LLM.',
          'Applying returned GDL changes and refreshing preview.',
        ]
      : action === 'create'
        ? [
            image ? `Reading the attached reference image: ${image.name}.` : 'Preparing a new HSF project plan.',
            'Calling the configured LLM.',
            'Writing generated HSF source.',
            'Building the initial preview.',
          ]
        : ['Reading the current HSF project.', 'Preparing a concise explanation.']
  return `${ASSISTANT_PENDING_PREFIX}\n${steps.map((step) => `- ${step}`).join('\n')}`
}

function replacePendingAssistantMessage(messages: AssistantMessage[], reply: string) {
  const last = messages.at(-1)
  if (last?.role === 'assistant' && last.content.startsWith(ASSISTANT_PENDING_PREFIX)) {
    return [...messages.slice(0, -1), { role: 'assistant' as const, content: reply }]
  }
  return [...messages, { role: 'assistant' as const, content: reply }]
}

function formatAssistantEventSummary(events?: Array<{ type: string; data: unknown }>) {
  const messages = (events ?? [])
    .map((event) => {
      const data = event.data
      if (data && typeof data === 'object' && 'message' in data && typeof data.message === 'string') {
        return data.message
      }
      if (event.type === 'compile_result') {
        return 'Compile verification finished.'
      }
      if (event.type === 'vision_analysis_done') {
        return 'Reference image analysis finished.'
      }
      if (event.type === 'object_plan_done') {
        return 'GDL object plan finished.'
      }
      return ''
    })
    .filter(Boolean)
    .filter((message, index, all) => all.indexOf(message) === index)
    .slice(0, 5)

  if (!messages.length) {
    return ''
  }
  return `\n\nProcess:\n${messages.map((message) => `- ${message}`).join('\n')}`
}
