import { useState, useRef, useCallback, useMemo } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import type { AssistantImageAttachment, AssistantMessage, LlmModelOption, VerificationReport } from '../api/types'
import { detectChatIntent, isResumeMessage, INTENT_LABELS } from '../state/chatIntent'
import { validateAssistantImageFile } from './assistantImage'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  hasProject: boolean
  interruptedContext?: { message: string; intent: string } | null
  onChat: (message: string, image?: AssistantImageAttachment | null) => void
  onStop: () => void
  onClearHistory: () => void
  onAdoptCode: (index: number) => void
  onOpenScript?: (scriptName: string) => void
  onSaveRevision?: (message: string) => void
  modelOptions?: LlmModelOption[]
  currentModel?: string
  onModelChange?: (model: string) => Promise<void>
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
  modelOptions = [],
  currentModel = '',
  onModelChange,
}: AssistantPanelProps) {
  const [draft, setDraft] = useState('')
  const [image, setImage] = useState<AssistantImageAttachment | null>(null)
  const [imageError, setImageError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)

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
    onChat(message, image)
    setImage(null)
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

  function attachImage(file: File | null) {
    setImageError('')
    if (!file) return
    const error = validateAssistantImageFile(file)
    if (error) {
      setImageError(error)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      setImage({
        name: file.name,
        mime: file.type || 'image/png',
        b64: comma >= 0 ? result.slice(comma + 1) : result,
      })
    }
    reader.onerror = () => setImageError('Image read failed')
    reader.readAsDataURL(file)
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
              {message.verification ? <VerificationCard report={message.verification} /> : null}
              {message.role === 'assistant' && message.content.includes('```') ? (
                <button type="button" disabled={busy} onClick={() => onAdoptCode(index)}>
                  Adopt code
                </button>
              ) : null}
            </article>
          ))
        ) : (
          <p className="assistant-empty">Ready</p>
        )}
      </div>
      <form className="assistant-input" onSubmit={submitMessage}>
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
          />
        </div>
        <div className="assistant-attachment-row">
          <label className="assistant-attach-button">
            Attach image
            <input
              type="file"
              aria-label="Attach image"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(event) => attachImage(event.currentTarget.files?.[0] ?? null)}
            />
          </label>
          {image ? (
            <button
              type="button"
              className="assistant-image-chip"
              disabled={busy}
              aria-label={`Remove image ${image.name}`}
              onClick={() => setImage(null)}
            >
              {image.name}
            </button>
          ) : (
            <span>{imageError || 'No image'}</span>
          )}
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

function VerificationCard({ report }: { report: VerificationReport }) {
  const compileCheck = report.checks.find((c) => c.check_type === 'compile')
  const compileLabel = compileCheck
    ? `${STATUS_ICON[compileCheck.status] ?? '❓'} ${
        compileCheck.status === 'pass'
          ? '编译通过'
          : compileCheck.status === 'fail'
            ? '编译失败'
            : compileCheck.status === 'not_run'
              ? '未编译'
              : '未知'
      }`
    : null
  const failedChecks = report.checks.filter((c) => c.status === 'fail')
  const unknownChecks = report.checks.filter(
    (c) => c.status === 'unknown' || c.status === 'not_run',
  )
  return (
    <div className={`assistant-verification ${report.passed ? 'is-pass' : 'is-fail'}`}>
      <div className="assistant-verification-header">
        <strong>验证报告</strong>
        <em className={`assistant-verification-confidence confidence-${report.confidence}`}>
          置信度 {CONFIDENCE_LABEL[report.confidence] ?? report.confidence}
        </em>
      </div>
      <div className="assistant-verification-counts">
        <span>✅ {report.counts.pass ?? 0}</span>
        <span>❌ {report.counts.fail ?? 0}</span>
        <span>❓ {report.counts.unknown ?? 0}</span>
        <span>⏸️ {report.counts.not_run ?? 0}</span>
        {compileLabel ? <span className="assistant-verification-compile">{compileLabel}</span> : null}
      </div>
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
