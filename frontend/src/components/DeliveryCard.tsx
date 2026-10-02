import { useState } from 'react'
import type { DeliveryPresentation } from '../api/types'
import { useT } from '../i18n'
import { useThemedDialog } from './ThemedDialog'

export interface DeliveryCardProps {
  delivery: DeliveryPresentation
  originalInstruction?: string | null
  busy?: boolean
  /** 恢复 before（draftPolicy: discard=丢弃草稿 / keep=保留编辑器草稿） */
  onRecover?: (policy: 'discard' | 'keep') => void | Promise<void>
  /** 查看差异：返回 diff 文本 */
  onViewDiff?: () => Promise<string | null>
  onContinue?: () => void
}

/**
 * ST03 交付状态卡：
 * - verified_change：before→after，可查看差异
 * - partial_change：未完成提示 + 已改文件 + 恢复/继续/差异；无成功总绿勾
 * - snapshot_failed：区分「检查通过/版本失败」
 * - unchanged：明确未产生源码变化，不显示成功绿勾
 * - 旧记录：unlinked
 */
export function DeliveryCard({
  delivery,
  originalInstruction,
  busy = false,
  onRecover,
  onViewDiff,
  onContinue,
}: DeliveryCardProps) {
  const t = useT()
  const { confirm, dialogNode } = useThemedDialog()
  const [diffOpen, setDiffOpen] = useState(false)
  const [diffText, setDiffText] = useState('')
  const [diffError, setDiffError] = useState<string | null>(null)

  const statusClass =
    delivery.status === 'completed'
      ? 'is-pass'
      : delivery.status === 'unlinked' || delivery.status === 'no_change'
        ? 'is-neutral'
        : 'is-incomplete'

  async function handleRecover() {
    if (!onRecover) return
    // ST03：恢复 before 前走离开/草稿保护（取消 = 所有草稿不变）
    const ok = await confirm({
      title: t('delivery.recover.confirmTitle'),
      message: t('delivery.recover.confirmMessage', {
        revision: delivery.recover_revision_id ?? '',
      }),
      confirmLabel: t('delivery.recover.confirmOk'),
      danger: true,
    })
    if (!ok) return
    onRecover('discard')
  }

  async function handleKeepDraftsAndRecover() {
    if (!onRecover) return
    const ok = await confirm({
      title: t('delivery.recover.keepTitle'),
      message: t('delivery.recover.keepMessage', {
        revision: delivery.recover_revision_id ?? '',
      }),
      confirmLabel: t('delivery.recover.keepOk'),
    })
    if (!ok) return
    onRecover('keep')
  }

  async function handleViewDiff() {
    if (!onViewDiff) return
    setDiffOpen(true)
    setDiffError(null)
    setDiffText(t('delivery.diff.loading'))
    try {
      const text = await onViewDiff()
      if (text === null) {
        setDiffError(t('delivery.diff.failed'))
        return
      }
      setDiffText(text || t('delivery.diff.empty'))
    } catch {
      setDiffError(t('delivery.diff.failed'))
    }
  }

  const instruction = originalInstruction || delivery.original_instruction || ''

  return (
    <div
      className={`assistant-delivery ${statusClass}`}
      data-delivery-status={delivery.status}
      data-delivery-state={delivery.state ?? 'unlinked'}
    >
      <div className="assistant-delivery-header">
        <strong className="assistant-delivery-title">{t('delivery.title')}</strong>
        {delivery.run_id ? (
          <em className="assistant-delivery-run" title={delivery.run_id} data-testid="delivery-run">
            run {delivery.run_id.slice(-8)}
          </em>
        ) : null}
        {delivery.show_success_badge ? (
          <span className="assistant-delivery-badge is-pass" data-testid="delivery-success-badge">
            ✓
          </span>
        ) : null}
      </div>

      <p className="assistant-delivery-headline" data-testid="delivery-headline">
        {delivery.headline}
      </p>
      {delivery.reason ? (
        <p className="assistant-delivery-reason" data-testid="delivery-reason">
          {delivery.reason}
        </p>
      ) : null}

      {delivery.show_before_after && (delivery.before_revision_id || delivery.after_revision_id) ? (
        <div className="assistant-delivery-before-after" data-testid="delivery-before-after">
          <span>
            {t('delivery.before')}: <code>{delivery.before_revision_id ?? '—'}</code>
          </span>
          <span className="assistant-delivery-arrow">→</span>
          <span>
            {t('delivery.after')}: <code>{delivery.after_revision_id ?? '—'}</code>
          </span>
        </div>
      ) : null}

      {delivery.status === 'snapshot_failed' ? (
        <div className="assistant-delivery-snapshot-split">
          <span data-testid="delivery-check-status">
            {t('delivery.check')}:{' '}
            {delivery.check_status === 'passed'
              ? t('delivery.checkPassed')
              : delivery.check_status === 'failed'
                ? t('delivery.checkFailed')
                : t('delivery.checkUnknown')}
          </span>
          <span data-testid="delivery-version-status">
            {t('delivery.version')}:{' '}
            {delivery.version_status === 'saved' ? t('delivery.versionSaved') : t('delivery.versionFailed')}
          </span>
        </div>
      ) : null}

      {delivery.show_changed_files && delivery.changed_files?.length ? (
        <div className="assistant-delivery-files" data-testid="delivery-changed-files">
          <strong>{t('delivery.changedFiles')}</strong>
          <ul>
            {delivery.changed_files.map((file) => (
              <li key={file}>{file}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {delivery.unlinked ? (
        <p className="assistant-delivery-unlinked" data-testid="delivery-unlinked">
          {t('delivery.unlinked')}
        </p>
      ) : null}

      {instruction ? (
        <details className="assistant-delivery-instruction">
          <summary>{t('delivery.originalInstruction')}</summary>
          <p data-testid="delivery-original-instruction">{instruction}</p>
        </details>
      ) : null}

      {delivery.continued_from?.origin_run_id ? (
        <p className="assistant-delivery-continued" data-testid="delivery-continued-from">
          {t('delivery.continuedFrom')}: <code>{delivery.continued_from.origin_run_id}</code>
        </p>
      ) : null}

      <div className="assistant-delivery-actions">
        {delivery.can_recover && onRecover ? (
          <>
            <button type="button" data-testid="delivery-recover" disabled={busy} onClick={() => void handleRecover()}>
              {t('delivery.recover')}
            </button>
            <button
              type="button"
              data-testid="delivery-recover-keep"
              disabled={busy}
              onClick={() => void handleKeepDraftsAndRecover()}
            >
              {t('delivery.recoverKeep')}
            </button>
          </>
        ) : null}
        {delivery.can_view_diff && onViewDiff ? (
          <button
            type="button"
            data-testid="delivery-view-diff"
            disabled={busy}
            onClick={() => void handleViewDiff()}
          >
            {t('delivery.viewDiff')}
          </button>
        ) : null}
        {delivery.can_continue && onContinue ? (
          <button
            type="button"
            data-testid="delivery-continue"
            disabled={busy}
            onClick={onContinue}
            title={instruction || t('delivery.continueHint')}
          >
            {t('delivery.continue')}
          </button>
        ) : null}
      </div>

      {diffOpen ? (
        <div className="assistant-delivery-diff" data-testid="delivery-diff-panel">
          <div className="assistant-delivery-diff-header">
            <strong>{t('delivery.diff.title')}</strong>
            <button type="button" onClick={() => setDiffOpen(false)}>
              {t('delivery.diff.close')}
            </button>
          </div>
          {diffError ? (
            <p data-testid="delivery-diff-error">{diffError}</p>
          ) : (
            <pre className="assistant-delivery-diff-body" data-testid="delivery-diff-body">
              {diffText}
            </pre>
          )}
        </div>
      ) : null}
      {dialogNode}
    </div>
  )
}

/** 供 AssistantPanel：delivery 指示未变化/未完成时抑制「已修复」误报 */
export function shouldSuppressAutoFixLabel(delivery?: DeliveryPresentation | null): boolean {
  if (!delivery) return false
  return (
    delivery.status === 'no_change' ||
    delivery.status === 'incomplete' ||
    delivery.status === 'unlinked' ||
    delivery.show_success_badge === false
  )
}
