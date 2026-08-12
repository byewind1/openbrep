import type { AssistantImageAttachment, AssistantStreamEvent, AssistantThinkingStep, GenerateResult, PendingPlan } from '../../api/types'
import type { AssistantMessage } from '../../api/types'
import type { PreviewGhostLabel, WorkbenchActionContext } from '../workbenchStoreTypes'
import { detectChatIntent, isResumeMessage } from '../chatIntent'
import { attachmentLabel } from '../../components/assistantImage'
import { classifyAssistantError, formatAssistantRequestError, hydrateSnapshot, normalizeScriptName } from '../workbenchStoreUtils'

/** P2a ghost 快照原因：任务前（i18n key，zh/en 见 locales） */
const PREVIEW_GHOST_LABEL_PRE_TASK: PreviewGhostLabel = 'preview.ghost.preTask'

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
  function userMessageContent(message: string, images?: AssistantImageAttachment[] | null) {
    const labels = (images ?? []).map((img) => attachmentLabel(img)).join(', ')
    return images && images.length ? `${message}\n[图: ${labels}]` : message
  }

  async function persistAssistantHistory() {
    // 无项目时不写盘：聊天历史存在 <项目>/.openbrep/ 下，
    // 纯聊天不应触发任何落盘，也避免后端报错污染 lastError
    if (!get().project) return
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
      // 模式级 skill 提案（P2-d）：成功交付后弹"沉淀提案"确认卡；无提案则清掉旧的
      pendingSkillProposal: result.ok ? (result.skill_proposal ?? null) : state.pendingSkillProposal,
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

  async function _createProject(
    message: string,
    images: AssistantImageAttachment[] = [],
    signal?: AbortSignal,
  ) {
    const trimmed = message.trim()
    if (!trimmed) return
    set((state) => ({
      assistantBusy: true,
      assistantMessages: [
        ...state.assistantMessages,
        { role: 'user', content: userMessageContent(trimmed, images), images: images.length ? images : undefined },
        { role: 'assistant', content: pendingAssistantMessage('create', images) },
      ],
    }))
    const epoch = get().projectEpoch
    const result = await api.createProjectFromPrompt(trimmed, get().llmSettings.assistant_settings, images, signal)
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
    const projectPath = result.project.path ?? ''
    const locationNote = projectPath
      ? `\n\n📁 项目已创建：${projectPath}\n可继续发修改指令（如「把层板数改成 5」），或在编辑器里直接改脚本、参数面板里调参数。`
      : ''
    set((state) => ({
      assistantBusy: false,
      assistantMessages: replacePendingAssistantMessage(
        state.assistantMessages,
        `${result.assistant?.reply ?? 'Project created.'}${locationNote}${formatAssistantEventSummary(result.events)}`,
        { verification: result.assistant?.verification ?? undefined },
      ),
      // 模式级 skill 提案（P2-d）：CREATE 成功交付后弹"沉淀提案"确认卡
      pendingSkillProposal: result.skill_proposal ?? null,
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

    /** P6a：从另一个项目追加合并聊天记录到当前项目（纯文件操作，无 LLM）。 */
    async importAssistantHistory(sourcePath: string) {
      const result = await api.importAssistantHistory(sourcePath)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to import assistant history.' })
        return
      }
      await get().loadAssistantHistory()
      const imported = result.imported ?? 0
      const sourceName = result.source_name ?? sourcePath
      const note =
        imported > 0
          ? `已从「${sourceName}」导入 ${imported} 条聊天记录（追加合并，不覆盖现有记录）`
          : `项目「${sourceName}」没有可导入的聊天记录`
      set((state) => ({
        compileLog: [note, ...state.compileLog].slice(0, 20),
      }))
    },

    /** P6b：LLM 把当前项目聊天记录整理成指令 → 填入 AI 输入框草稿（不自动发送）。
     *  长操作期间切换项目 → 丢弃结果（projectEpoch 守卫），防止旧项目整理结果
     *  填进新项目的输入框。 */
    async distillAssistantHistory() {
      const epoch = get().projectEpoch
      const result = await api.distillAssistantHistory()
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Distill result discarded: project switched during the request.')
        return
      }
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to distill assistant history.' })
        return
      }
      const instruction = (result.instruction ?? '').trim()
      if (!instruction) {
        set({ lastError: 'Failed to distill assistant history: empty instruction.' })
        return
      }
      // 只填草稿通道，绝不自动发送；面板监听 seed 后填入输入框并消费
      set({ assistantDraftSeed: instruction })
    },

    consumeAssistantDraftSeed() {
      set({ assistantDraftSeed: null })
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

    async createProjectFromPrompt(message: string, images: AssistantImageAttachment[] = []) {
      return _createProject(message, images)
    },

    async generateAssistantChanges(message: string, images: AssistantImageAttachment[] = []) {
      const trimmed = message.trim()
      if (!trimmed) return
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: userMessageContent(trimmed, images), images: images.length ? images : undefined },
          { role: 'assistant', content: pendingAssistantMessage('generate', images) },
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
      const result = await api.generateWithAssistant(trimmed, get().llmSettings.assistant_settings, images)
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
    async sendChat(message: string, images: AssistantImageAttachment[] = []) {
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

      // P2a ghost：任务发起时快照"任务前"预览（修改前后对比用）。
      // CREATE（无项目）时 preview 为 null → ghost 置 null；参数防抖刷新 /
      // 手动 Update / 质量档切换都不覆盖；项目切换经 hydrateSnapshot 清空。
      const previewAtTaskStart = get().preview
      set({
        previewGhost: previewAtTaskStart,
        previewGhostLabel: previewAtTaskStart ? PREVIEW_GHOST_LABEL_PRE_TASK : null,
      })

      const controller = new AbortController()
      set({ chatAbortController: controller, interruptedContext: null })

      try {
        if (intent === 'create') {
          // createProjectFromPrompt manages its own pending messages;
          // pass signal so the stop button can abort project creation too.
          await _createProject(finalMessage, images, controller.signal)
        } else if (intent === 'modify') {
          // 计划确认门（V3）：MODIFY 先出非代码语言计划，用户确认后才执行
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', images)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, images), images: images.length ? images : undefined },
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
          const planResult = await api.requestModifyPlan(finalMessage, settings, images, controller.signal)
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
          await finishModifyStream(planResult, epoch, pendingAssistantMessage('generate', images), [])
        } else if (intent === 'debug') {
          // DEBUG 不走确认门：默认走 agent loop 流式路径，实时显示每一步事件
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', images)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, images), images: images.length ? images : undefined },
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
            images,
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

    async confirmPendingSkillProposal(approve: boolean) {
      // 模式级 skill 提案（P2-d）：approve → propose+verify 双闸晋升；false → 丢弃
      const proposal = get().pendingSkillProposal
      if (!proposal) {
        set({ lastError: '没有待确认的 skill 提案。' })
        return
      }
      const epoch = get().projectEpoch
      const result = await api.confirmSkillProposal(approve)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Skill proposal result discarded: project switched during the request.')
        return
      }
      set((state) => ({
        pendingSkillProposal: null,
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          approve
            ? result.ok
              ? result.verified
                ? `✅ skill「${proposal.name}」已沉淀并通过验证（${result.gate} 门禁）`
                : `📝 skill「${proposal.name}」已落盘为 proposed（验证未过，暂不注入）`
              : `❌ skill「${proposal.name}」沉淀失败：${result.error ?? '未知错误'}`
            : `🗑 已丢弃 skill 提案「${proposal.name}」。`,
        ),
      }))
      if (!result.ok && result.error) {
        set({ lastError: result.error })
      }
      await persistAssistantHistory()
    },
  }
}

function pendingAssistantMessage(action: 'explain' | 'create' | 'generate', images?: AssistantImageAttachment[] | null) {
  const labels = (images ?? []).map((img) => attachmentLabel(img)).join(', ')
  const imageStep = images && images.length ? `Reading the attached reference image: ${labels}.` : null
  const steps =
    action === 'generate'
      ? [
          'Inspecting the loaded HSF project.',
          imageStep ?? 'Preparing generation context.',
          'Calling the configured LLM.',
          'Applying returned GDL changes and refreshing preview.',
        ]
      : action === 'create'
        ? [
            imageStep ?? 'Preparing a new HSF project plan.',
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
