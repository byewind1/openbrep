import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { DeliveryCard, shouldSuppressAutoFixLabel } from './DeliveryCard'
import type { DeliveryPresentation } from '../api/types'

function partialPresentation(overrides: Partial<DeliveryPresentation> = {}): DeliveryPresentation {
  return {
    state: 'partial_change',
    status: 'incomplete',
    unlinked: false,
    headline: '未完成，存在部分修改',
    reason: '任务中断或验证未完成，存在部分修改；after 版本未创建',
    show_success_badge: false,
    show_before_after: false,
    show_changed_files: true,
    can_recover: true,
    can_continue: true,
    can_view_diff: true,
    recover_revision_id: 'r0001',
    before_revision_id: 'r0001',
    after_revision_id: null,
    changed_files: ['scripts/3d.gdl', 'paramlist.xml'],
    run_id: 'r_20260918_120000_abc123',
    error_code: null,
    check_status: 'unknown',
    version_status: 'skipped',
    original_instruction: '把层板数改成 5',
    continued_from: null,
    ...overrides,
  }
}

describe('DeliveryCard ST03', () => {
  test('U01 partial：未完成提示、已改文件、恢复/差异/继续可见，无成功总绿勾', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation()}
        originalInstruction="把层板数改成 5"
        onRecover={vi.fn()}
        onViewDiff={vi.fn()}
        onContinue={vi.fn()}
      />,
    )
    expect(screen.getByTestId('delivery-headline').textContent).toContain('未完成')
    expect(screen.getByTestId('delivery-changed-files').textContent).toContain('scripts/3d.gdl')
    expect(screen.getByTestId('delivery-recover')).toBeTruthy()
    expect(screen.getByTestId('delivery-view-diff')).toBeTruthy()
    expect(screen.getByTestId('delivery-continue')).toBeTruthy()
    expect(screen.queryByTestId('delivery-success-badge')).toBeNull()
  })

  test('U05 unchanged：明确未产生源码变化，无已修复/绿勾', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({
          state: 'unchanged',
          status: 'no_change',
          headline: '未产生源码变化',
          reason: '请求了修改，但未检测到源码差异',
          changed_files: [],
          show_changed_files: false,
          can_recover: false,
          can_view_diff: false,
          can_continue: true,
          recover_revision_id: null,
        })}
        originalInstruction="把层板数改成 5"
        onContinue={vi.fn()}
      />,
    )
    expect(screen.getByTestId('delivery-headline').textContent).toContain('未产生源码变化')
    expect(screen.queryByTestId('delivery-success-badge')).toBeNull()
    expect(screen.queryByText(/已修复/)).toBeNull()
  })

  test('snapshot_failed 区分检查通过/版本失败', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({
          state: 'snapshot_failed',
          status: 'snapshot_failed',
          headline: '检查通过，版本快照失败',
          reason: 'after 版本快照写盘失败',
          check_status: 'passed',
          version_status: 'failed',
          can_continue: false,
        })}
      />,
    )
    expect(screen.getByTestId('delivery-check-status').textContent).toContain('通过')
    expect(screen.getByTestId('delivery-version-status').textContent).toContain('失败')
  })

  test('verified_change：before→after 与成功徽章', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({
          state: 'verified_change',
          status: 'completed',
          headline: '已交付修改',
          reason: '验证通过并绑定 after 版本',
          show_success_badge: true,
          show_before_after: true,
          after_revision_id: 'r0002',
          can_continue: false,
        })}
      />,
    )
    expect(screen.getByTestId('delivery-success-badge')).toBeTruthy()
    const ba = screen.getByTestId('delivery-before-after').textContent ?? ''
    expect(ba).toContain('r0001')
    expect(ba).toContain('r0002')
  })

  test('旧记录 unlinked', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({
          state: null,
          status: 'unlinked',
          unlinked: true,
          headline: '旧记录，未关联交付版本',
          reason: '该记录产生于 delivery_source 契约之前，无法定位 before/after',
          show_changed_files: false,
          can_recover: false,
          can_continue: false,
          can_view_diff: false,
          changed_files: [],
          run_id: null,
          recover_revision_id: null,
          before_revision_id: null,
          original_instruction: null,
        })}
      />,
    )
    expect(screen.getByTestId('delivery-unlinked').textContent).toContain('未关联')
  })

  test('U02 取消恢复确认 → onRecover 不调用', async () => {
    const onRecover = vi.fn()
    render(
      <DeliveryCard delivery={partialPresentation()} onRecover={onRecover} onViewDiff={vi.fn()} onContinue={vi.fn()} />,
    )
    fireEvent.click(screen.getByTestId('delivery-recover'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(onRecover).not.toHaveBeenCalled())
  })

  test('U03 保留草稿并恢复 → onRecover("keep")', async () => {
    const onRecover = vi.fn()
    render(
      <DeliveryCard delivery={partialPresentation()} onRecover={onRecover} onViewDiff={vi.fn()} onContinue={vi.fn()} />,
    )
    fireEvent.click(screen.getByTestId('delivery-recover-keep'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '保留草稿并恢复' }))
    await waitFor(() => expect(onRecover).toHaveBeenCalledWith('keep'))
  })

  test('U06 继续：调用 onContinue 并展示原指令', () => {
    const onContinue = vi.fn()
    render(
      <DeliveryCard
        delivery={partialPresentation()}
        originalInstruction="把层板数改成 5"
        onContinue={onContinue}
        onRecover={vi.fn()}
        onViewDiff={vi.fn()}
      />,
    )
    expect(screen.getByTestId('delivery-original-instruction').textContent).toContain('把层板数改成 5')
    fireEvent.click(screen.getByTestId('delivery-continue'))
    expect(onContinue).toHaveBeenCalled()
  })

  test('继续后展示 continued_from', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({
          continued_from: { origin_run_id: 'r_old', original_instruction: '把层板数改成 5' },
        })}
      />,
    )
    expect(screen.getByTestId('delivery-continued-from').textContent).toContain('r_old')
  })

  test('F1 partial：diff_target=working，before 有 after=null', () => {
    render(
      <DeliveryCard
        delivery={partialPresentation({ diff_target: 'working' })}
        onViewDiff={vi.fn()}
        onRecover={vi.fn()}
        onContinue={vi.fn()}
      />,
    )
    const card = document.querySelector('[data-delivery-state="partial_change"]')
    expect(card).toBeTruthy()
    expect(screen.getByTestId('delivery-view-diff')).toBeTruthy()
    expect(screen.queryByTestId('delivery-before-after')).toBeNull()
  })

  test('shouldSuppressAutoFixLabel：未变化/未完成抑制已修复', () => {
    expect(shouldSuppressAutoFixLabel(partialPresentation({ status: 'no_change' }))).toBe(true)
    expect(shouldSuppressAutoFixLabel(partialPresentation({ status: 'incomplete' }))).toBe(true)
    expect(
      shouldSuppressAutoFixLabel(partialPresentation({ status: 'completed', show_success_badge: true })),
    ).toBe(false)
  })
})
