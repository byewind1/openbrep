import { useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { useT } from '../i18n'

export interface ConfirmOptions {
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
}

export interface PromptOptions {
  title: string
  message?: string
  defaultValue?: string
}

interface ConfirmRequest {
  id: number
  kind: 'confirm'
  options: ConfirmOptions
  settle: (ok: boolean) => void
}

interface PromptRequest {
  id: number
  kind: 'prompt'
  options: PromptOptions
  settle: (value: string | null) => void
}

type PendingRequest = ConfirmRequest | PromptRequest

export interface ThemedDialogApi {
  confirm: (options: ConfirmOptions) => Promise<boolean>
  prompt: (options: PromptOptions) => Promise<string | null>
  dialogNode: ReactNode
}

/**
 * 主题化 confirm/prompt（P4-B）：替代 window.confirm / window.prompt。
 * 状态全在 hook 内；prompt 空串放行（与 window.prompt 一致），仅取消/Esc/遮罩返回 null。
 */
export function useThemedDialog(): ThemedDialogApi {
  const [pending, setPending] = useState<PendingRequest | null>(null)
  const nextRequestId = useRef(0)

  const confirm = (options: ConfirmOptions) =>
    new Promise<boolean>((resolve) => {
      setPending({
        id: ++nextRequestId.current,
        kind: 'confirm',
        options,
        settle: (ok) => {
          resolve(ok)
          setPending(null)
        },
      })
    })

  const prompt = (options: PromptOptions) =>
    new Promise<string | null>((resolve) => {
      setPending({
        id: ++nextRequestId.current,
        kind: 'prompt',
        options,
        settle: (value) => {
          resolve(value)
          setPending(null)
        },
      })
    })

  return {
    confirm,
    prompt,
    // key 用请求 id：连续请求时强制重建，输入态不残留。
    dialogNode: pending ? <ThemedDialog key={pending.id} request={pending} /> : null,
  }
}

function ThemedDialog({ request }: { request: PendingRequest }) {
  const t = useT()
  const isPrompt = request.kind === 'prompt'
  const [promptValue, setPromptValue] = useState(isPrompt ? request.options.defaultValue ?? '' : '')
  const danger = request.kind === 'confirm' && Boolean(request.options.danger)
  const confirmLabel =
    request.kind === 'confirm' ? request.options.confirmLabel ?? t('dialog.confirm') : t('dialog.confirm')

  function cancel() {
    if (request.kind === 'prompt') request.settle(null)
    else request.settle(false)
  }

  function confirmAction() {
    if (request.kind === 'prompt') request.settle(promptValue)
    else request.settle(true)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      cancel()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      event.stopPropagation()
      confirmAction()
    }
  }

  return (
    <div className="themed-dialog-overlay" onClick={cancel} onKeyDown={handleKeyDown}>
      <div
        className="themed-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={request.options.title}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="themed-dialog-title">{request.options.title}</h2>
        {request.options.message ? <p className="themed-dialog-message">{request.options.message}</p> : null}
        {isPrompt ? (
          <input
            className="themed-dialog-input"
            autoFocus
            value={promptValue}
            onChange={(event) => setPromptValue(event.currentTarget.value)}
          />
        ) : null}
        <div className="themed-dialog-actions">
          <button type="button" autoFocus={danger} onClick={cancel}>
            {t('dialog.cancel')}
          </button>
          <button
            type="button"
            className={danger ? 'themed-dialog-danger' : ''}
            autoFocus={!danger && !isPrompt}
            onClick={confirmAction}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
