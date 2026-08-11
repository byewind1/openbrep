import { useState, useRef, useCallback, useMemo } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import type { AssistantImageAttachment, AssistantMessage, LlmModelOption, ModifyAcceptance, PendingPlan, SkillProposal, VerificationReport } from '../api/types'
import { detectChatIntent, isResumeMessage, INTENT_LABELS } from '../state/chatIntent'
import { attachmentLabel, isImagePathText, MAX_ASSISTANT_IMAGES, validateAssistantImageFile } from './assistantImage'
import { AssistantThinkingTimeline } from './AssistantThinkingTimeline'
import { PanelEmpty } from './PanelEmpty'
import { useT } from '../i18n'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  hasProject: boolean
  interruptedContext?: { message: string; intent: string } | null
  onChat: (message: string, images?: AssistantImageAttachment[]) => void
  onStop: () => void
  onClearHistory: () => void
  onAdoptCode: (index: number) => void
  onOpenScript?: (scriptName: string) => void
  onSaveRevision?: (message: string) => void
  onRevealLine?: (scriptName: string, lineNumber: number) => void
  modelOptions?: LlmModelOption[]
  currentModel?: string
  onModelChange?: (model: string) => Promise<void>
  // 计划确认门（V3）：待确认计划 + 确认/取消回调
  pendingPlan?: PendingPlan | null
  onConfirmPlan?: (approve: boolean) => void
  // 模式级 skill 提案（P2-d）：待确认提案 + 沉淀/忽略回调
  pendingSkillProposal?: SkillProposal | null
  onConfirmSkillProposal?: (approve: boolean) => void
}

/** 面板内带 token 的已贴图片（token 与草稿里的 [图N] 对应，按 attach 顺序递增）。 */
interface AttachedImage extends AssistantImageAttachment {
  token: string
}

const SLASH_COMMANDS = [
  { id: 'model', label: '/model', description: '切换 AI 模型' },
] as const

export function AssistantPanel({
  messages,
  busy,
  hasProject,
  interruptedContext,
  onChat,
  onStop,
  onClearHistory,
  onAdoptCode,
  onOpenScript,
  onSaveRevision,
  onRevealLine,
  modelOptions = [],
  currentModel = '',
  onModelChange,
  pendingPlan = null,
  onConfirmPlan,
  pendingSkillProposal = null,
  onConfirmSkillProposal,
}: AssistantPanelProps) {
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<AttachedImage[]>([])
  const [imageError, setImageError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const t = useT()

  // slash command state
  const [pickerMode, setPickerMode] = useState<null | 'commands' | 'models'>(null)
  const [pickerIndex, setPickerIndex] = useState(0)
  const [modelSwitching, setModelSwitching] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // intent indicator: computed from current draft
  const detectedIntent = useMemo(() => {
    const t = draft.trim()
    if (!t || pickerMode !== null) return null
    return detectChatIntent(t, hasProject)
  }, [draft, hasProject, pickerMode])

  // slash command derived values
  const commandQuery = pickerMode === 'commands' ? draft.slice(1).toLowerCase() : ''
  const visibleCommands = SLASH_COMMANDS.filter(
    (c) => c.id.startsWith(commandQuery) && (c.id !== 'model' || (modelOptions.length > 0 && !!onModelChange)),
  )
  const modelQuery = pickerMode === 'models' ? draft.toLowerCase() : ''
  const visibleModelOptions = modelOptions.filter(
    (m) => !modelQuery || m.id.toLowerCase().includes(modelQuery) || m.label.toLowerCase().includes(modelQuery),
  )
  const customModels = visibleModelOptions.filter((m) => m.kind === 'custom')
  const officialModels = visibleModelOptions.filter((m) => m.kind === 'official')

  // ── Submit ──────────────────────────────────────────────────────────────
  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    handleSend()
  }

  function handleSend() {
    if (pickerMode !== null || busy) return
    const message = draft.trim()
    if (!message) return
    setDraft('')
    onChat(
      message,
      attachments.map(({ token: _token, ...img }) => img),
    )
    setAttachments([])
  }

  // P4-C 空态：示例提示词只填入输入框，不自动发送
  function fillExample(example: string) {
    setDraft(example)
    textareaRef.current?.focus()
  }

  // ── Keyboard ─────────────────────────────────────────────────────────────
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // ESC: stop generation or close picker
    if (event.key === 'Escape') {
      if (busy) { onStop(); event.preventDefault(); return }
      if (pickerMode !== null) { closePicker(); event.preventDefault(); return }
    }

    // Ctrl+C while busy → stop (only when no text is selected)
    if (event.key === 'c' && event.ctrlKey && busy) {
      const ta = textareaRef.current
      if (ta && ta.selectionStart === ta.selectionEnd) {
        onStop()
        event.preventDefault()
        return
      }
    }

    // Picker navigation
    if (pickerMode !== null) {
      handlePickerKeyDown(event)
      return
    }

    // Enter = send (Shift+Enter inserts newline, composition in progress = skip)
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      handleSend()
    }
  }

  // ── Draft change ──────────────────────────────────────────────────────────
  function handleDraftChange(value: string) {
    setDraft(value)
    if (pickerMode === 'models') return
    if (value.startsWith('/') && !busy) {
      if (pickerMode !== 'commands') { setPickerMode('commands'); setPickerIndex(0) }
    } else {
      setPickerMode(null)
    }
    // 输入本地路径文本：拖尾为完整绝对路径（/ ~ 或盘符开头 + 图片扩展名）→ 转 chip
    const trimmed = value.trim()
    const trailing = trimmed.split(/\s+/).pop() ?? ''
    if (trailing && isImagePathText(trailing)) {
      setDraft(trimmed.slice(0, -trailing.length).trimEnd())
      attachPathImage(trailing)
    }
  }

  // ── Picker helpers ────────────────────────────────────────────────────────
  const closePicker = useCallback(() => {
    setPickerMode(null)
    setDraft('')
    textareaRef.current?.focus()
  }, [])

  function selectCommand(id: string) {
    if (id === 'model') {
      setPickerMode('models'); setDraft(''); setPickerIndex(0)
      textareaRef.current?.focus()
    }
  }

  async function selectModel(model: string) {
    if (!onModelChange) return
    setModelSwitching(true)
    try { await onModelChange(model) }
    catch { /* 切换失败已写入 store.lastError，由顶栏 error pill 展示 */ }
    finally {
      setModelSwitching(false); setPickerMode(null); setDraft('')
      textareaRef.current?.focus()
    }
  }

  function handlePickerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const list = pickerMode === 'commands' ? visibleCommands : visibleModelOptions
    const listLen = list.length
    if (event.key === 'ArrowDown') { setPickerIndex((i) => (i + 1) % Math.max(listLen, 1)); event.preventDefault(); return }
    if (event.key === 'ArrowUp') { setPickerIndex((i) => (i - 1 + Math.max(listLen, 1)) % Math.max(listLen, 1)); event.preventDefault(); return }
    if (event.key === 'Enter' && listLen > 0) {
      if (pickerMode === 'commands') selectCommand(visibleCommands[Math.min(pickerIndex, visibleCommands.length - 1)].id)
      else void selectModel(visibleModelOptions[Math.min(pickerIndex, visibleModelOptions.length - 1)].id)
      event.preventDefault()
    }
  }

  // ── 多图贴图（P5a）：粘贴 / 拖入 / 本地路径 ─────────────────────────────
  /** 下一个 token 编号：取现存 token 数字后缀的最大值 + 1（删除中间项后不复用编号，避免 token 撞车）。 */
  function nextTokenBase(): number {
    return attachments.reduce((max, a) => {
      const n = Number(a.token.replace('图', ''))
      return Number.isFinite(n) ? Math.max(max, n) : max
    }, 0)
  }

  function attachPathImage(pathText: string) {
    setImageError('')
    if (attachments.length >= MAX_ASSISTANT_IMAGES) {
      setImageError(`最多 ${MAX_ASSISTANT_IMAGES} 张图片。`)
      return
    }
    const trimmed = pathText.trim()
    const token = `图${nextTokenBase() + 1}`
    setAttachments((prev) => [
      ...prev,
      { name: trimmed, path: trimmed, mime: '', b64: '', token },
    ])
    setDraft((prev) => `${prev}[${token}]`)
  }

  async function attachFiles(files: File[] | null) {
    if (!files || !files.length) return
    setImageError('')
    const room = MAX_ASSISTANT_IMAGES - attachments.length
    if (files.length > room) {
      setImageError(
        `最多 ${MAX_ASSISTANT_IMAGES} 张图片（已有 ${attachments.length} 张，最多再添 ${room} 张）。`,
      )
      return
    }
    const validated: AssistantImageAttachment[] = []
    for (const file of files) {
      const error = validateAssistantImageFile(file)
      if (error) {
        setImageError(`${file.name}: ${error}`)
        continue
      }
      const attachment = await readFileAsAttachment(file)
      if (attachment) validated.push(attachment)
    }
    if (!validated.length) return
    const base = nextTokenBase()
    const tokens = validated.map((_, i) => `图${base + i + 1}`)
    setAttachments((prev) => [...prev, ...validated.map((img, i) => ({ ...img, token: tokens[i] }))])
    setDraft((prev) => prev + tokens.map((token) => `[${token}]`).join(''))
  }

  function readFileAsAttachment(file: File): Promise<AssistantImageAttachment | null> {
    return new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = String(reader.result || '')
        const comma = result.indexOf(',')
        resolve({
          name: file.name,
          mime: file.type || 'image/png',
          b64: comma >= 0 ? result.slice(comma + 1) : result,
        })
      }
      reader.onerror = () => {
        setImageError('Image read failed')
        resolve(null)
      }
      reader.readAsDataURL(file)
    })
  }

  function removeAttachment(token: string) {
    setAttachments((prev) => prev.filter((a) => a.token !== token))
    setDraft((prev) => prev.replace(`[${token}]`, ''))
  }

  function handlePaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    const items = event.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length) {
      event.preventDefault()
      void attachFiles(imageFiles)
      return
    }
    const text = event.clipboardData.getData('text/plain')
    if (isImagePathText(text)) {
      event.preventDefault()
      attachPathImage(text)
    }
  }

  function handleDragOver(event: React.DragEvent) {
    event.preventDefault()
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault()
    const files = Array.from(event.dataTransfer?.files ?? [])
    if (files.length) void attachFiles(files)
  }


  return (
    <aside className="assistant-panel">
      <div className="panel-heading">
        <h2>AI</h2>
        <div className="assistant-heading-actions">
          <span>{busy ? 'Working' : 'Ready'}</span>
          <button type="button" disabled={messages.length === 0} onClick={() => setHistoryOpen(true)}>
            History
          </button>
          <button type="button" disabled={busy || messages.length === 0} onClick={onClearHistory}>
            Clear
          </button>
        </div>
      </div>
      <div className="assistant-thread">
        {messages.length ? (
          messages.map((message, index) => (
            <article
              className={`assistant-message ${message.role}${message.interrupted ? ' is-interrupted' : ''}`}
              key={`${message.role}-${index}`}
            >
              <span>
                {message.role === 'user' ? '你' : 'OpenBrep'}
                {message.interrupted ? (
                  <em className="assistant-error-badge assistant-interrupted">已中断</em>
                ) : message.errorCategory ? (
                  <em className={`assistant-error-badge assistant-error-${message.errorCategory}`}>
                    {errorCategoryLabel(message.errorCategory)}
                  </em>
                ) : null}
              </span>
              <p>{message.content}</p>
              {message.role === 'user' && message.images?.length ? (
                <div className="assistant-message-images">
                  {message.images.map((img, i) => (
                    <span className="assistant-message-image-chip" key={`${attachmentLabel(img)}-${i}`} title={attachmentLabel(img)}>
                      {img.b64 ? (
                        <img src={`data:${img.mime || 'image/png'};base64,${img.b64}`} alt={attachmentLabel(img)} />
                      ) : (
                        <span className="assistant-message-image-icon">📁</span>
                      )}
                      <em>{attachmentLabel(img)}</em>
                    </span>
                  ))}
                </div>
              ) : null}
              {message.role === 'assistant' && message.thinkingSteps ? (
                <AssistantThinkingTimeline
                  steps={message.thinkingSteps}
                  busy={busy && index === messages.length - 1}
                  interrupted={message.interrupted}
                />
              ) : null}

              {message.changedFiles?.length ? (
                <div className="assistant-change-card">
                  <strong>Changed files</strong>
                  <div className="assistant-change-files">
                    {message.changedFiles.map((file) => (
                      <button
                        type="button"
                        key={file}
                        disabled={busy || !onOpenScript}
                        title={`Open ${file} in the editor`}
                        onClick={() => onOpenScript?.(file.split('/').pop() ?? file)}
                      >
                        {file}
                      </button>
                    ))}
                  </div>
                  {onSaveRevision ? (
                    <button
                      type="button"
                      className="assistant-save-revision"
                      disabled={busy}
                      onClick={() => onSaveRevision(revisionMessageFor(messages, index))}
                    >
                      Save revision
                    </button>
                  ) : null}
                </div>
              ) : null}
              {message.verification ? <VerificationCard report={message.verification} onRevealLine={onRevealLine} /> : null}
              {message.acceptance ? <AcceptanceCard acceptance={message.acceptance} /> : null}
              {message.role === 'assistant' && message.content.includes('```') ? (
                <button type="button" disabled={busy} onClick={() => onAdoptCode(index)}>
                  Adopt code
                </button>
              ) : null}
            </article>
          ))
        ) : (
          <PanelEmpty icon="✦" title={t('assistant.empty.title')} hint={t('assistant.empty.hint')}>
            <div className="panel-empty-examples">
              <button type="button" onClick={() => fillExample(t('assistant.empty.example.generate'))}>
                {t('assistant.empty.example.generate')}
              </button>
              <button type="button" onClick={() => fillExample(t('assistant.empty.example.modify'))}>
                {t('assistant.empty.example.modify')}
              </button>
              <button type="button" onClick={() => fillExample(t('assistant.empty.example.explain'))}>
                {t('assistant.empty.example.explain')}
              </button>
            </div>
          </PanelEmpty>
        )}
        {pendingPlan ? <PlanConfirmCard plan={pendingPlan} busy={busy} onConfirm={onConfirmPlan} /> : null}
        {pendingSkillProposal ? (
          <SkillProposalCard proposal={pendingSkillProposal} busy={busy} onConfirm={onConfirmSkillProposal} />
        ) : null}
      </div>
      <form className="assistant-input" aria-label="Assistant input" onSubmit={submitMessage} onDragOver={handleDragOver} onDrop={handleDrop}>
        <div className="assistant-input-wrap">
          {pickerMode === 'commands' && visibleCommands.length > 0 && (
            <div className="slash-picker" role="listbox" aria-label="命令列表">
              <div className="slash-picker-header">命令</div>
              {visibleCommands.map((cmd, i) => (
                <button
                  key={cmd.id}
                  type="button"
                  role="option"
                  aria-selected={i === pickerIndex % visibleCommands.length}
                  className={`slash-picker-item${i === pickerIndex % visibleCommands.length ? ' is-active' : ''}`}
                  onClick={() => selectCommand(cmd.id)}
                >
                  <span className="slash-picker-label">{cmd.label}</span>
                  <span className="slash-picker-desc">{cmd.description}</span>
                </button>
              ))}
            </div>
          )}
          {pickerMode === 'models' && (
            <div className="slash-picker slash-picker--models" role="listbox" aria-label="模型列表">
              <div className="slash-picker-header">
                选择模型
                {modelSwitching && <span className="slash-picker-loading">切换中…</span>}
              </div>
              {visibleModelOptions.length === 0 ? (
                <p className="slash-picker-empty">无匹配模型</p>
              ) : (
                <>
                  {customModels.length > 0 && (
                    <ModelGroup
                      label="自定义模型"
                      options={customModels}
                      flatOffset={0}
                      currentModel={currentModel}
                      pickerIndex={pickerIndex}
                      totalVisible={visibleModelOptions.length}
                      modelSwitching={modelSwitching}
                      onSelect={selectModel}
                    />
                  )}
                  {officialModels.length > 0 && (
                    <ModelGroup
                      label="官方模型"
                      options={officialModels}
                      flatOffset={customModels.length}
                      currentModel={currentModel}
                      pickerIndex={pickerIndex}
                      totalVisible={visibleModelOptions.length}
                      modelSwitching={modelSwitching}
                      onSelect={selectModel}
                    />
                  )}
                </>
              )}
            </div>
          )}
          <textarea
            ref={textareaRef}
            rows={3}
            aria-label="Ask or generate"
            placeholder={
              pickerMode === 'models'
                ? '输入过滤…'
                : busy
                  ? '生成中… ESC 停止'
                  : interruptedContext
                    ? `已中断 — 输入继续，或发送"继续"重试`
                    : '发送消息… Enter 发送  Shift+Enter 换行  / 触发命令'
            }
            value={draft}
            disabled={modelSwitching}
            onChange={(event) => handleDraftChange(event.currentTarget.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
          />
        </div>
        {attachments.length ? (
          <div className="assistant-attachment-chips">
            {attachments.map((img) => (
              <span className={`assistant-image-chip${img.path ? ' is-path' : ''}`} key={img.token}>
                {img.b64 ? (
                  <img
                    className="assistant-image-chip-thumb"
                    src={`data:${img.mime || 'image/png'};base64,${img.b64}`}
                    alt=""
                  />
                ) : (
                  <span className="assistant-image-chip-icon">📁</span>
                )}
                <span className="assistant-image-chip-label">
                  {img.token}: {attachmentLabel(img)}
                </span>
                <button
                  type="button"
                  className="assistant-image-chip-remove"
                  disabled={busy}
                  aria-label={`Remove image ${img.token}`}
                  title={`移除 ${img.token}`}
                  onClick={() => removeAttachment(img.token)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="assistant-attachment-row">
          <label className="assistant-attach-button">
            Attach image
            <input
              type="file"
              aria-label="Attach image"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(event) => {
                void attachFiles(Array.from(event.currentTarget.files ?? []))
                event.currentTarget.value = ''
              }}
            />
          </label>
          {attachments.length ? (
            <span className="assistant-attach-count">{attachments.length}/{MAX_ASSISTANT_IMAGES}</span>
          ) : (
            <span>{'Paste, drop, or type a local image path'}</span>
          )}
          {imageError ? <span className="assistant-attach-error">{imageError}</span> : null}
        </div>
        <div className="assistant-footer-row">
          {detectedIntent && !busy && (
            <span className={`chat-intent-badge intent-${detectedIntent}`}>
              → {INTENT_LABELS[detectedIntent]}
            </span>
          )}
          {interruptedContext && !busy && (
            <button
              type="button"
              className="chat-resume-btn"
              onClick={() => { onChat('继续'); }}
            >
              ↩ 重试上次
            </button>
          )}
          <div className="assistant-actions">
            {busy ? (
              <button type="button" className="chat-stop-btn" onClick={onStop}>
                ■ 停止
              </button>
            ) : (
              <button type="submit" className="chat-send-btn" disabled={draft.trim().length === 0}>
                发送
              </button>
            )}
          </div>
        </div>
      </form>
      <AssistantHistoryDrawer
        open={historyOpen}
        messages={messages}
        busy={busy}
        onClose={() => setHistoryOpen(false)}
        onAdoptCode={onAdoptCode}
      />
    </aside>
  )
}

function errorCategoryLabel(category: NonNullable<AssistantMessage['errorCategory']>) {
  if (category === 'llm') return 'LLM settings'
  if (category === 'compile') return 'Compile'
  return 'Error'
}

// revision 信息取触发本次生成的用户指令（往前找最近一条 user 消息），截断防止过长
function revisionMessageFor(messages: AssistantMessage[], assistantIndex: number) {
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      const instruction = messages[index].content.trim()
      return `AI: ${instruction.length > 60 ? `${instruction.slice(0, 60)}…` : instruction}`
    }
  }
  return 'AI generated changes'
}

function checkIcon(status: string): string {
  if (status === 'pass') return '✅'
  if (status === 'fail') return '❌'
  if (status === 'warn') return '⚠️'
  if (status === 'skipped') return '⏭'
  return '•'
}

function AcceptanceCard({ acceptance }: { acceptance: ModifyAcceptance }) {
  const t = useT()
  const delta = acceptance.geometry_delta
  const meshFrom = delta.mesh_count?.from
  const meshTo = delta.mesh_count?.to
  const bboxFrom = delta.bbox_size?.from
  const bboxTo = delta.bbox_size?.to
  const countsFrom = delta.counts_2d?.from
  const countsTo = delta.counts_2d?.to
  const hasGeometryCompare =
    meshFrom !== undefined || meshTo !== undefined ||
    bboxFrom !== undefined || bboxTo !== undefined ||
    countsFrom !== undefined || countsTo !== undefined
  return (
    <div className="acceptance-card">
      <strong className="acceptance-title">{t('assistant.acceptance.title')}</strong>
      {acceptance.summary_lines?.length ? (
        <ul className="acceptance-summary">
          {acceptance.summary_lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      ) : null}
      {hasGeometryCompare ? (
        <div className="acceptance-geometry">
          <strong>{t('assistant.acceptance.geometry')}</strong>
          <table className="acceptance-geometry-table">
            <thead>
              <tr>
                <th />
                <th>{t('assistant.acceptance.before')}</th>
                <th>{t('assistant.acceptance.after')}</th>
              </tr>
            </thead>
            <tbody>
              {meshFrom !== undefined || meshTo !== undefined ? (
                <tr>
                  <td>{t('assistant.acceptance.meshCount')}</td>
                  <td>{meshFrom ?? '—'}</td>
                  <td>{meshTo ?? '—'}</td>
                </tr>
              ) : null}
              {bboxFrom !== undefined || bboxTo !== undefined ? (
                <tr>
                  <td>{t('assistant.acceptance.bbox')}</td>
                  <td>{formatSize(bboxFrom)}</td>
                  <td>{formatSize(bboxTo)}</td>
                </tr>
              ) : null}
              {countsFrom !== undefined || countsTo !== undefined ? (
                <tr>
                  <td>{t('assistant.acceptance.counts2d')}</td>
                  <td>{formatCounts(countsFrom)}</td>
                  <td>{formatCounts(countsTo)}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
          {delta.reason ? <p className="acceptance-geometry-note">{delta.reason}</p> : null}
        </div>
      ) : null}
      {acceptance.checks?.length ? (
        <ul className="acceptance-checks">
          {acceptance.checks.map((check, i) => (
            <li key={i} className={`acceptance-check status-${check.status}`}>
              <span className="acceptance-check-icon">{checkIcon(check.status)}</span>
              <span className="acceptance-check-name">{check.name}</span>
              <span className="acceptance-check-detail">{check.detail}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function formatSize(size?: number[] | null): string {
  if (!size || !size.length) return '—'
  return size.map((v) => Number(v.toFixed(3))).join('×')
}

function formatCounts(counts?: { lines: number; polygons: number; circles: number; arcs: number } | null): string {
  if (!counts) return '—'
  return `${counts.lines}/${counts.polygons}/${counts.circles}/${counts.arcs}`
}

function PlanConfirmCard({
  plan,
  busy,
  onConfirm,
}: {
  plan: PendingPlan
  busy: boolean
  onConfirm?: (approve: boolean) => void
}) {
  const t = useT()
  return (
    <div className="plan-confirm-card" role="group" aria-label={t('assistant.plan.title')}>
      <div className="plan-confirm-header">
        <strong>{t('assistant.plan.title')}</strong>
        <span className="plan-confirm-risk">{t('assistant.plan.risk')}: {plan.risk || '无'}</span>
      </div>
      <p className="plan-confirm-intent">{plan.intent_summary}</p>
      {plan.user_visible_changes.length ? (
        <div className="plan-confirm-section">
          <strong>{t('assistant.plan.userChanges')}</strong>
          <ul>
            {plan.user_visible_changes.map((change, i) => (
              <li key={i}>{change}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {plan.affected_files.length ? (
        <div className="plan-confirm-section">
          <strong>{t('assistant.plan.affectedFiles')}</strong>
          <ul>
            {plan.affected_files.map((file) => (
              <li key={file}>{file}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="plan-confirm-actions">
        <button type="button" className="plan-confirm-approve" disabled={busy} onClick={() => onConfirm?.(true)}>
          {t('assistant.plan.confirm')}
        </button>
        <button type="button" className="plan-confirm-reject" disabled={busy} onClick={() => onConfirm?.(false)}>
          {t('assistant.plan.cancel')}
        </button>
      </div>
    </div>
  )
}

function SkillProposalCard({
  proposal,
  busy,
  onConfirm,
}: {
  proposal: SkillProposal
  busy: boolean
  onConfirm?: (approve: boolean) => void
}) {
  const t = useT()
  const evidence = proposal.evidence ?? null
  return (
    <div className="skill-proposal-card" role="group" aria-label={t('assistant.skillProposal.title')}>
      <div className="skill-proposal-header">
        <strong>{t('assistant.skillProposal.title')}</strong>
        <span className="skill-proposal-type">{proposal.pattern_type}</span>
      </div>
      <p className="skill-proposal-name">{proposal.name}</p>
      <pre className="skill-proposal-content">{proposal.content}</pre>
      {evidence && (evidence.changed_files?.length || evidence.project) ? (
        <div className="skill-proposal-section">
          <strong>{t('assistant.skillProposal.evidence')}</strong>
          <ul>
            {evidence.project ? <li>{t('assistant.skillProposal.project')}: {evidence.project}</li> : null}
            {(evidence.changed_files ?? []).map((file, i) => (
              <li key={`${file}-${i}`}>{file}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="skill-proposal-actions">
        <button
          type="button"
          className="plan-confirm-approve"
          disabled={busy}
          onClick={() => onConfirm?.(true)}
        >
          {t('assistant.skillProposal.approve')}
        </button>
        <button
          type="button"
          className="plan-confirm-reject"
          disabled={busy}
          onClick={() => onConfirm?.(false)}
        >
          {t('assistant.skillProposal.ignore')}
        </button>
      </div>
    </div>
  )
}

function AssistantHistoryDrawer({
  open,
  messages,
  busy,
  onClose,
  onAdoptCode,
}: {
  open: boolean
  messages: AssistantMessage[]
  busy: boolean
  onClose: () => void
  onAdoptCode: (index: number) => void
}) {
  if (!open) return null

  return (
    <>
      <button className="history-scrim" type="button" aria-label="Close assistant history" onClick={onClose} />
      <aside className="assistant-history-drawer" role="dialog" aria-label="Assistant history">
        <div className="history-header">
          <div>
            <strong>History</strong>
            <span>{messages.length} messages</span>
          </div>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="history-list">
          {messages.map((message, index) => (
            <article className={`history-message ${message.role}`} key={`${message.role}-${index}`}>
              <div className="history-message-meta">
                <span>{message.role === 'user' ? '你' : 'OpenBrep'}</span>
                <em>#{index + 1}</em>
              </div>
              <p>{message.content}</p>
              {message.role === 'assistant' && message.content.includes('```') ? (
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Adopt code from message ${index + 1}`}
                  onClick={() => onAdoptCode(index)}
                >
                  Adopt code
                </button>
              ) : null}
            </article>
          ))}
        </div>
      </aside>
    </>
  )
}

// ── Model group for slash picker ─────────────────────────────────────────
function ModelGroup({
  label,
  options,
  flatOffset,
  currentModel,
  pickerIndex,
  totalVisible,
  modelSwitching,
  onSelect,
}: {
  label: string
  options: LlmModelOption[]
  flatOffset: number
  currentModel: string
  pickerIndex: number
  totalVisible: number
  modelSwitching: boolean
  onSelect: (id: string) => void
}) {
  return (
    <>
      <div className="slash-picker-group-label">{label}</div>
      {options.map((opt, localIdx) => {
        const flatIdx = flatOffset + localIdx
        const isActive = flatIdx === pickerIndex % Math.max(totalVisible, 1)
        return (
          <button
            key={opt.id}
            type="button"
            role="option"
            aria-selected={isActive}
            disabled={modelSwitching}
            className={`slash-picker-item${isActive ? ' is-active' : ''}${opt.id === currentModel ? ' is-current' : ''}`}
            onClick={() => onSelect(opt.id)}
          >
            <span className="slash-picker-label">{opt.label}</span>
            <span className="slash-picker-desc">{opt.provider}</span>
            {opt.id === currentModel && <span className="slash-picker-badge">当前</span>}
          </button>
        )
      })}
    </>
  )
}

// ── Verification evidence card ────────────────────────────────────────────
// Shows the self-correcting agent's proof-oriented report: what was checked,
// pass/fail/unknown counts, compile status, confidence, and residual risks.
// Compact by design — detailed evidence stays in trace/revision files.
const CONFIDENCE_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}
const STATUS_ICON: Record<string, string> = {
  pass: '✅',
  fail: '❌',
  unknown: '❓',
  not_run: '⏸️',
}

function VerificationCard({
  report,
  onRevealLine,
}: {
  report: VerificationReport
  onRevealLine?: (scriptName: string, lineNumber: number) => void
}) {
  const compileCheck = report.checks.find((c) => c.check_type === 'compile')
  const isSkippedNoCompiler =
    compileCheck?.status === 'not_run' &&
    compileCheck.detail.includes('SKIPPED_NO_COMPILER')
  const compileLabel = compileCheck
    ? `${STATUS_ICON[compileCheck.status] ?? '❓'} ${
        compileCheck.status === 'pass'
          ? '编译通过'
          : compileCheck.status === 'fail'
            ? '编译失败'
            : compileCheck.status === 'not_run'
              ? isSkippedNoCompiler ? '无编译器' : '未编译'
              : '未知'
      }`
    : null
  const failedChecks = report.checks.filter((c) => c.status === 'fail')
  const unknownChecks = report.checks.filter(
    (c) => c.status === 'unknown' || (c.status === 'not_run' && !isSkippedNoCompiler),
  )
  // 编译失败时的行级错误列表
  const compileLineErrors = compileCheck?.line_errors ?? []

  return (
    <div className={`assistant-verification ${report.passed ? 'is-pass' : 'is-fail'}`}>
      <div className="assistant-verification-header">
        <strong>验证报告</strong>
        <em className={`assistant-verification-confidence confidence-${report.confidence}`}>
          置信度 {CONFIDENCE_LABEL[report.confidence] ?? report.confidence}
        </em>
        {report.graph_powered ? (
          <span className="assistant-verification-graph-badge" title="本次任务使用了 GDL 知识图谱约束或诊断">
            🔌 图谱
          </span>
        ) : null}
      </div>
      <div className="assistant-verification-counts">
        <span>✅ {report.counts.pass ?? 0}</span>
        <span>❌ {report.counts.fail ?? 0}</span>
        <span>❓ {report.counts.unknown ?? 0}</span>
        <span>⏸️ {report.counts.not_run ?? 0}</span>
        {compileLabel ? <span className="assistant-verification-compile">{compileLabel}</span> : null}
      </div>
      {isSkippedNoCompiler ? (
        <p className="assistant-verification-no-compiler">
          ⚠️ 未配置 LP_XMLConverter，跳过编译验证。请在设置中配置编译器路径以获得完整校验。
        </p>
      ) : null}
      {report.fixes_applied.length ? (
        <p className="assistant-verification-fixes">
          已修复：{report.fixes_applied.slice(0, 2).join('；')}
        </p>
      ) : null}
      {failedChecks.length ? (
        <ul className="assistant-verification-fails">
          {failedChecks.slice(0, 3).map((c, i) => (
            <li key={i}>❌ {c.name}：{c.detail}</li>
          ))}
        </ul>
      ) : null}
      {compileLineErrors.length ? (
        <ul className="assistant-verification-line-errors">
          {compileLineErrors.slice(0, 5).map((e, i) => (
            <li key={i}>
              {onRevealLine ? (
                <button
                  type="button"
                  className="verification-line-error-link"
                  onClick={() => onRevealLine('3d.gdl', e.line_number)}
                  title={`跳转到第 ${e.line_number} 行`}
                >
                  第 {e.line_number} 行：{e.message}
                </button>
              ) : (
                <span>第 {e.line_number} 行：{e.message}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {unknownChecks.length ? (
        <p className="assistant-verification-unknowns">
          {unknownChecks.length} 项检查无自动化覆盖
        </p>
      ) : null}
      {report.remaining_risks.length ? (
        <p className="assistant-verification-risks">
          残余风险：{report.remaining_risks.slice(0, 2).join('；')}
        </p>
      ) : null}
    </div>
  )
}
