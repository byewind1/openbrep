import { describe, expect, test } from 'vitest'
import { hydrateHistoryMessages } from './assistantActions'
import type { AssistantMessage, DeliveryPresentation } from '../../api/types'

const partialDelivery: DeliveryPresentation = {
  state: 'partial_change',
  status: 'incomplete',
  unlinked: false,
  headline: '未完成，存在部分修改',
  reason: '任务中断',
  show_success_badge: false,
  show_before_after: false,
  show_changed_files: true,
  can_recover: true,
  can_continue: true,
  can_view_diff: true,
  diff_target: 'working',
  recover_revision_id: 'r0001',
  before_revision_id: 'r0001',
  after_revision_id: null,
  changed_files: ['scripts/3d.gdl'],
  run_id: 'r_new',
  error_code: null,
  check_status: 'unknown',
  version_status: 'skipped',
  original_instruction: '把层板数改成 5',
  continued_from: { origin_run_id: 'r_old', original_instruction: '把层板数改成 5' },
}

describe('ST03 F2 hydrateHistoryMessages', () => {
  test('持久化后的 delivery 卡在刷新后恢复', () => {
    const restored = hydrateHistoryMessages([
      {
        role: 'assistant',
        content: '未完成，存在部分修改',
        delivery: partialDelivery,
        runId: 'r_new',
        originalInstruction: '把层板数改成 5',
        deliveryContinueFrom: { origin_run_id: 'r_old', original_instruction: '把层板数改成 5' },
      } as AssistantMessage,
    ])
    expect(restored[0].delivery?.status).toBe('incomplete')
    expect(restored[0].delivery?.continued_from?.origin_run_id).toBe('r_old')
    expect(restored[0].delivery?.show_success_badge).toBe(false)
  })

  test('snake_case 历史字段映射为 delivery 卡', () => {
    const restored = hydrateHistoryMessages([
      {
        role: 'assistant',
        content: '部分修改\n\nChanged files: scripts/3d.gdl',
        delivery_source: { schema_version: 1, run_id: 'r_x', state: 'partial_change', before_revision_id: 'r0001', after_revision_id: null, source_fingerprint: null, changed_files: ['scripts/3d.gdl'], snapshot_status: 'skipped', error_code: null },
        delivery_continue_from: { origin_run_id: 'r_old', original_instruction: '把层板数改成 5' },
        original_instruction: '把层板数改成 5',
        run_id: 'r_x',
      } as unknown as AssistantMessage,
    ])
    // normalize 读 snake_case；hydrate 因任务痕迹补 unlinked 或保留 source
    expect(restored[0].runId).toBe('r_x')
    expect(restored[0].delivery).toBeTruthy()
    expect(restored[0].delivery?.unlinked).toBe(true)
  })

  test('旧记录无 delivery meta → 明确 unlinked，无成功绿勾', () => {
    const restored = hydrateHistoryMessages([
      {
        role: 'assistant',
        content: 'Changed files: scripts/3d.gdl',
      } as AssistantMessage,
    ])
    expect(restored[0].delivery?.status).toBe('unlinked')
    expect(restored[0].delivery?.unlinked).toBe(true)
    expect(restored[0].delivery?.show_success_badge).toBe(false)
    expect(restored[0].delivery?.headline).toContain('未关联')
  })

  test('纯 explain 历史不强行插 unlinked 卡', () => {
    const restored = hydrateHistoryMessages([
      { role: 'assistant', content: '这个参数控制宽度。' } as AssistantMessage,
    ])
    expect(restored[0].delivery).toBeUndefined()
  })
})
