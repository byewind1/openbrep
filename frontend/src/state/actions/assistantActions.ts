import type { AssistantImageAttachment, AssistantStreamEvent, AssistantThinkingStep, GenerateResult, PendingPlan } from '../../api/types'
import type { AssistantMessage } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { detectChatIntent, isResumeMessage } from '../chatIntent'
import { classifyAssistantError, formatAssistantRequestError, hydrateSnapshot, normalizeScriptName } from '../workbenchStoreUtils'

const ASSISTANT_PENDING_PREFIX = 'Thinking...'
// 计划确认门（V3）的待确认/执行中内容：保留 Thinking... 前缀，
// 让 replacePendingAssistantMessage 能正确替换上一条 pending 消息
const PLAN_PENDING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n📝 修改计划已生成，请确认后执行。`
const PLAN_EXECUTING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n⏳ 正在按已确认的计划执行修改…`
const INTERRUPTED_CONTENT = '⏹ 已中断'

function eventToThinkingStep(event: AssistantStreamEvent): AssistantThinkingStep | null {
  const { type, data } = event
  if (type === 'status' && typeof data.message === 'string') {
    return {
      type: 'status',
      stage: typeof data.stage === 'string' ? data.stage : undefined,
      message: data.message,
    }
  }
  if (type === 'tool_call') {
    const name = typeof data.display_name === 'string' ? data.display_name : String(data.name ?? 'tool')
    const summary = typeof data.summary === 'string' ? data.summary : ''
    return {
      type: 'tool_call',
      stage: typeof data.stage === 'string' ? data.stage : undefined,
      message: name,
      detail: summary,
      ok: data.ok === true,
    }
  }
  if (type === 'plan') {
    return {
      type: 'plan',
      stage: 'plan',
      message: 'AI 计划：' + (typeof data.intent_summary === 'string' ? data.intent_summary : '制定修改方案'),
      intentSummary: typeof data.intent_summary === 'string' ? data.intent_summary : undefined,
      affectedFiles: Array.isArray(data.affected_files) ? data.affected_files.filter((f): f is string => typeof f === 'string') : undefined,
      parameterChanges: Array.isArray(data.parameter_changes) ? data.parameter_changes : undefined,
      strategy: typeof data.strategy === 'string' ? data.strategy : undefined,
    }
  }
  if (type === 'assistant_delta' && typeof data.content === 'string') {
    return {
      type: 'assistant_delta',
      stage: 'think',
      message: 'AI 思考中',
      detail: data.content,
    }
  }
  if (type === 'compile_result') {
    const success = data.success === true
    return {
      type: 'status',
      stage: 'compile',
      message: success ? '✅ 编译通过' : '❌ 编译失败',
      detail: typeof data.error === 'string' && data.error ? data.error : undefined,
      ok: success,
    }
  }
  return null
}

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
  // 流式修改执行的统一收尾：最终答复 + 时间线 + 预览刷新 + 历史持久化
  async function finishModifyStream(
    result: GenerateResult,
    epoch: number,
    initialContent: string,
    thinkingSteps: AssistantThinkingStep[],
  ) {
    if (projectSwitchedSince(epoch)) {
      discardStaleResult('Generation result discarded: project switched during the request.')
      return
    }
    const changedFiles = result.assistant?.changed_files ?? []
    const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
    const finalReply =
      result.ok && result.assistant
        ? `${result.assistant.reply}${suffix}`
        : formatAssistantRequestError(result.error, 'Generation request failed.')
    const replyExtras = result.ok
      ? {
          changedFiles,
          verification: result.assistant?.verification ?? undefined,
          acceptance: result.assistant?.acceptance ?? undefined,
          thinkingSteps: [...thinkingSteps],
        }
      : { errorCategory: classifyAssistantError(finalReply), thinkingSteps: [...thinkingSteps] }
    set((state) => ({
      assistantBusy: false,
      assistantMessages: replacePendingAssistantMessage(state.assistantMessages, finalReply, replyExtras),
      lastError: result.ok ? null : finalReply,
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
  }

  async function _createProject(message: string, image: AssistantImageAttachment | null = null, signal?: AbortSignal) {
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
    const result = await api.createProjectFromPrompt(trimmed, get().llmSettings.assistant_settings, image, signal)
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
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error, {
          errorCategory: classifyAssistantError(error),
        }),
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
        { verification: result.assistant?.verification ?? undefined },
      ),
    }))
    await persistAssistantHistory()
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
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          reply,
          result.ok ? {} : { errorCategory: classifyAssistantError(reply) },
        ),
        lastError: result.ok ? null : reply,
      }))
      await persistAssistantHistory()
    },

    async createProjectFromPrompt(message: string, image: AssistantImageAttachment | null = null) {
      return _createProject(message, image)
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
      const replyExtras = result.ok
        ? { changedFiles, verification: result.assistant?.verification ?? undefined, acceptance: result.assistant?.acceptance ?? undefined }
        : { errorCategory: classifyAssistantError(reply) }
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, reply, replyExtras),
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

    // ── Unified chat entry point ───────────────────────────────────────────
    // Detects intent → routes to explain / generate / create.
    // Supports AbortController for ESC / stop-button interruption.
    async sendChat(message: string, image: AssistantImageAttachment | null = null) {
      const trimmed = message.trim()
      if (!trimmed) return

      const hasProject = !!get().project
      const interrupted = get().interruptedContext

      // Follow-up after an interrupt: "继续" retries the original
      let finalMessage = trimmed
      let intent = detectChatIntent(trimmed, hasProject)
      if (interrupted && isResumeMessage(trimmed)) {
        finalMessage = interrupted.message
        intent = detectChatIntent(interrupted.message, hasProject)
      }

      const controller = new AbortController()
      set({ chatAbortController: controller, interruptedContext: null })

      try {
        if (intent === 'create') {
          // createProjectFromPrompt manages its own pending messages;
          // pass signal so the stop button can abort project creation too.
          await _createProject(finalMessage, image, controller.signal)
        } else if (intent === 'modify') {
          // 计划确认门（V3）：MODIFY 先出非代码语言计划，用户确认后才执行
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', image)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, image) },
              { role: 'assistant', content: initialContent, thinkingSteps: [] },
            ],
          }))
          const flushed = await get().flushDirtyScripts()
          if (!flushed.ok) {
            const error = get().lastError ?? 'Failed to save scripts before generation.'
            set((state) => ({
              assistantBusy: false,
              assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
            }))
            return
          }
          const epoch = get().projectEpoch
          const planResult = await api.requestModifyPlan(finalMessage, settings, image, controller.signal)
          if (projectSwitchedSince(epoch)) {
            discardStaleResult('Generation result discarded: project switched during the request.')
            return
          }
          if (planResult.awaiting_confirmation && planResult.pending_plan) {
            const plan: PendingPlan = planResult.pending_plan
            set((state) => ({
              assistantBusy: false,
              pendingPlan: plan,
              assistantMessages: replacePendingAssistantMessage(
                state.assistantMessages,
                PLAN_PENDING_CONTENT,
                {
                  thinkingSteps: [{
                    type: 'plan',
                    stage: 'plan',
                    message: 'AI 计划：' + plan.intent_summary,
                    intentSummary: plan.intent_summary,
                    affectedFiles: plan.affected_files,
                    userVisibleChanges: plan.user_visible_changes,
                    risk: plan.risk,
                  }],
                },
              ),
            }))
            return
          }
          // 计划失败回落 / micro_modify / V1 DSL 命中：直接展示执行结果
          await finishModifyStream(planResult, epoch, pendingAssistantMessage('generate', image), [])
        } else if (intent === 'debug') {
          // DEBUG 不走确认门：默认走 agent loop 流式路径，实时显示每一步事件
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', image)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, image) },
              { role: 'assistant', content: initialContent, thinkingSteps: [] },
            ],
          }))
          const flushed = await get().flushDirtyScripts()
          if (!flushed.ok) {
            const error = get().lastError ?? 'Failed to save scripts before generation.'
            set((state) => ({
              assistantBusy: false,
              assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
            }))
            return
          }
          const epoch = get().projectEpoch
          const thinkingSteps: AssistantThinkingStep[] = []

          const result = await api.generateWithAssistantStream(
            finalMessage,
            settings,
            image,
            (event: AssistantStreamEvent) => {
              const step = eventToThinkingStep(event)
              if (step) {
                thinkingSteps.push(step)
              }
              set((state) => ({
                assistantMessages: replacePendingAssistantMessage(
                  state.assistantMessages,
                  initialContent,
                  { thinkingSteps: [...thinkingSteps] },
                ),
              }))
            },
            controller.signal,
          )
          await finishModifyStream(result, epoch, initialContent, thinkingSteps)
        } else {
          // explain
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: finalMessage },
              { role: 'assistant', content: pendingAssistantMessage('explain') },
            ],
          }))
          const epoch = get().projectEpoch
          const result = await api.askAssistant(finalMessage, controller.signal)
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
            assistantMessages: replacePendingAssistantMessage(
              state.assistantMessages,
              reply,
              result.ok ? {} : { errorCategory: classifyAssistantError(reply) },
            ),
            lastError: result.ok ? null : reply,
          }))
          await persistAssistantHistory()
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          set((state) => ({
            assistantBusy: false,
            chatAbortController: null,
            interruptedContext: { message: finalMessage, intent },
            assistantMessages: state.assistantMessages.map((m, i) =>
              i === state.assistantMessages.length - 1 &&
              m.role === 'assistant' &&
              m.content.startsWith(ASSISTANT_PENDING_PREFIX)
                ? { ...m, content: INTERRUPTED_CONTENT, interrupted: true, thinkingSteps: m.thinkingSteps }
                : m,
            ),
          }))
          return
        }
        throw e
      } finally {
        set({ chatAbortController: null })
      }
    },

    stopChat() {
      get().chatAbortController?.abort()
    },

    async confirmPendingPlan(approve: boolean) {
      // 计划确认门（V3）：approve=true → 带已确认计划执行（SSE 接回进度流）；false → 取消
      const plan = get().pendingPlan
      if (!plan) {
        set({ lastError: '没有待确认的修改计划，请先发起一次修改。' })
        return
      }
      if (!approve) {
        const result = await api.confirmModifyPlan(false)
        set((state) => ({
          pendingPlan: null,
          assistantMessages: replacePendingAssistantMessage(state.assistantMessages, '⏹ 已取消本次修改。'),
        }))
        if (!result.ok && result.error) {
          set({ lastError: result.error })
        }
        await persistAssistantHistory()
        return
      }
      const lastMessage = get().assistantMessages[get().assistantMessages.length - 1]
      const thinkingSteps: AssistantThinkingStep[] = [...(lastMessage?.thinkingSteps ?? [])]
      set((state) => ({ pendingPlan: null, assistantBusy: true }))
      const epoch = get().projectEpoch
      const result = await api.confirmModifyPlan(true, true, (event: AssistantStreamEvent) => {
        const step = eventToThinkingStep(event)
        if (step) {
          thinkingSteps.push(step)
        }
        set((state) => ({
          assistantMessages: replacePendingAssistantMessage(
            state.assistantMessages,
            PLAN_EXECUTING_CONTENT,
            { thinkingSteps: [...thinkingSteps] },
          ),
        }))
      })
      await finishModifyStream(result, epoch, '⏳ 正在按已确认的计划执行修改…', thinkingSteps)
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

function replacePendingAssistantMessage(messages: AssistantMessage[], reply: string, extras: Partial<AssistantMessage> = {}) {
  const replyMessage = { role: 'assistant' as const, content: reply, ...extras }
  const last = messages.at(-1)
  if (last?.role === 'assistant' && last.content.startsWith(ASSISTANT_PENDING_PREFIX)) {
    return [...messages.slice(0, -1), replyMessage]
  }
  return [...messages, replyMessage]
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
