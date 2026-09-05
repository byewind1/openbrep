import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import type { DistilledLesson } from '../../api/types'
import { useUiPrefsStore } from '../../state/uiPrefsStore'
import { DistilledLessonsPanel } from './DistilledLessonsPanel'

function resetUiPrefs() {
  useUiPrefsStore.setState({ locale: 'zh' })
}

const PROPOSED: DistilledLesson = {
  fingerprint: 'quality:abc123',
  pattern: '值越界前先校验参数范围',
  guidance: '在 FOR 循环内用数组下标前先做 RANGE 边界检查，避免越界读取。',
  status: 'proposed',
  count: 2,
  first_seen: '2026-09-05T10:00:00Z',
  last_seen: '2026-09-05T11:00:00Z',
  evidence_refs: [
    {
      run_id: 'r_20260905_100000_001',
      check_type: 'gate_fail',
      before_revision: 'rev_before_1',
      after_revision: 'rev_after_1',
    },
    {
      run_id: 'r_20260905_100000_002',
      check_type: 'artifact_quality.script_complexity',
      before_revision: null,
      after_revision: null,
    },
  ],
  raw_excerpt: '越界读取',
}

const ACTIVE: DistilledLesson = {
  ...PROPOSED,
  fingerprint: 'quality:def456',
  pattern: '字符串参数优先用 CDATA 引用',
  status: 'active',
  count: 1,
}

const REJECTED: DistilledLesson = {
  ...PROPOSED,
  fingerprint: 'quality:ghi789',
  pattern: '已忽略的教训',
  status: 'rejected',
  count: 1,
}

const NO_EVIDENCE: DistilledLesson = {
  fingerprint: 'quality:legacy',
  pattern: '旧格式教训（无 evidence_refs）',
  guidance: '仍可读可渲染',
  status: 'proposed',
  count: 1,
  first_seen: null,
  last_seen: null,
  evidence_refs: null,
  raw_excerpt: null,
}

function renderPanel(
  options: {
    lessons?: DistilledLesson[]
    busy?: boolean
    projectName?: string | null
    message?: { kind: 'error' | 'info'; text: string } | null
    onRefresh?: () => void
    onDistill?: () => void
    onSetStatus?: (fingerprint: string, decision: 'promote' | 'reject' | 'demote') => void
  } = {},
) {
  const {
    lessons = [PROPOSED],
    busy = false,
    projectName = '测试项目',
    message = null,
    onRefresh = vi.fn(),
    onDistill = vi.fn(),
    onSetStatus = vi.fn(),
  } = options
  return {
    onRefresh,
    onDistill,
    onSetStatus,
    ...render(
      <DistilledLessonsPanel
        lessons={lessons}
        busy={busy}
        projectName={projectName}
        message={message}
        onRefresh={onRefresh}
        onDistill={onDistill}
        onSetStatus={onSetStatus}
      />,
    ),
  }
}

describe('DistilledLessonsPanel', () => {
  beforeEach(resetUiPrefs)
  afterEach(resetUiPrefs)

  test('renders a proposed lesson card with pattern, guidance and evidence refs', () => {
    renderPanel()

    expect(screen.getByText(PROPOSED.pattern)).toBeTruthy()
    expect(screen.getByText(PROPOSED.guidance as string)).toBeTruthy()
    expect(screen.getByText('待审')).toBeTruthy()
    expect(screen.getByText('2 次运行')).toBeTruthy()
    // evidence：check_type chips + run refs + revision ids（不含脚本内容）
    expect(screen.getByText('证据')).toBeTruthy()
    expect(screen.getByText('gate_fail')).toBeTruthy()
    expect(screen.getByText('r_20260905_100000_001')).toBeTruthy()
    expect(screen.getByText('rev_before_1 → rev_after_1')).toBeTruthy()
    expect(screen.getByText('artifact_quality.script_complexity')).toBeTruthy()
    expect(screen.queryByText(/越界读取/)).not.toBeNull()
  })

  test('approve promotes and ignore rejects via onSetStatus', () => {
    const { onSetStatus } = renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '采纳' }))
    expect(onSetStatus).toHaveBeenCalledWith(PROPOSED.fingerprint, 'promote')

    fireEvent.click(screen.getByRole('button', { name: '忽略' }))
    expect(onSetStatus).toHaveBeenCalledWith(PROPOSED.fingerprint, 'reject')
  })

  test('active lessons show a demote action only; rejected lessons have no actions', () => {
    renderPanel({ lessons: [ACTIVE, REJECTED] })

    expect(screen.getByText('已采纳')).toBeTruthy()
    expect(screen.getByRole('button', { name: '撤回待审' })).toBeTruthy()
    expect(screen.getByText('已忽略')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '采纳' })).toBeNull()
    expect(screen.queryByRole('button', { name: '忽略' })).toBeNull()
  })

  test('demote on an active lesson calls onSetStatus with demote', () => {
    const { onSetStatus } = renderPanel({ lessons: [ACTIVE] })

    fireEvent.click(screen.getByRole('button', { name: '撤回待审' }))
    expect(onSetStatus).toHaveBeenCalledWith(ACTIVE.fingerprint, 'demote')
  })

  test('buttons are disabled while busy and the distill button shows the distilling label', () => {
    renderPanel({ busy: true })

    expect(screen.getByRole('button', { name: '刷新' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '提炼中…' })).toHaveProperty('disabled', true)
  })

  test('refresh and distill call their handlers', () => {
    const { onRefresh, onDistill } = renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '立即提炼' }))
    expect(onDistill).toHaveBeenCalledTimes(1)
  })

  test('shows project context and empty text when no lessons', () => {
    renderPanel({ lessons: [], projectName: '我的书架' })

    expect(screen.getByText('来源项目：我的书架')).toBeTruthy()
    expect(screen.getByText(/暂无蒸馏教训/)).toBeTruthy()
  })

  test('renders the operation message with error or info style', () => {
    const { container } = renderPanel({ message: { kind: 'error', text: 'Distillation LLM call failed.' } })
    const messageBox = container.querySelector('.distilled-lessons-message.error')
    expect(messageBox?.textContent).toBe('Distillation LLM call failed.')
  })

  test('legacy lessons without evidence stay readable and actionable', () => {
    renderPanel({ lessons: [NO_EVIDENCE] })

    expect(screen.getByText('旧格式教训（无 evidence_refs）')).toBeTruthy()
    expect(screen.queryByText('证据')).toBeNull()
    expect(screen.getByRole('button', { name: '采纳' })).toBeTruthy()
  })

  test('renders English labels when the locale is en', () => {
    useUiPrefsStore.setState({ locale: 'en' })
    renderPanel({ lessons: [PROPOSED], projectName: 'Shelf' })

    expect(screen.getByText('Source project: Shelf')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Ignore' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Distill now' })).toBeTruthy()
    expect(screen.getByText('Proposed')).toBeTruthy()
  })
})
