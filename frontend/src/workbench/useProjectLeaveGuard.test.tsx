import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useProjectLeaveGuard } from './useProjectLeaveGuard'
import { workbenchStore } from '../state/workbenchStore'

// SF1（F04）：离开项目共用确认的守卫级回归（AC21–AC26 的 UI 判定部分；
// 逐入口接线在 browser smoke 里覆盖）。

function Harness({ action }: { action: () => void }) {
  const { runLeaveAction, dialogNode } = useProjectLeaveGuard()
  return (
    <div>
      <button type="button" onClick={() => void runLeaveAction('Open project', action)}>
        leave
      </button>
      {dialogNode}
    </div>
  )
}

function resetStore(patch: Record<string, unknown> = {}) {
  workbenchStore.setState({
    sessionId: 's1',
    projectEpoch: 1,
    project: { name: 'Chair', source: 'hsf', path: '/workspace/Chair' },
    loading: false,
    assistantBusy: false,
    compiling: false,
    sourceActionBusy: false,
    dirtyScripts: {},
    scriptContents: {},
    draftParameters: {},
    ...patch,
  })
}

beforeEach(() => {
  resetStore()
})

describe('useProjectLeaveGuard', () => {
  test('AC24 无草稿：不弹确认，直接执行 action', async () => {
    const action = vi.fn()
    render(<Harness action={action} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))

    await waitFor(() => expect(action).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  test('AC21/AC25 有脚本草稿或仅参数草稿：都弹"取消 / 丢弃并继续"', async () => {
    for (const patch of [
      { dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'x' } },
      { draftParameters: { A: 2 } },
    ]) {
      resetStore(patch)
      const action = vi.fn()
      const { unmount } = render(<Harness action={action} />)

      fireEvent.click(screen.getByRole('button', { name: 'leave' }))

      const dialog = await screen.findByRole('dialog')
      // 文案明确"如需保留，请取消后保存脚本并应用参数"
      expect(dialog.textContent).toContain('如需保留，请取消后保存脚本并应用参数')
      expect(screen.getByRole('button', { name: '取消' })).toBeTruthy()
      expect(screen.getByRole('button', { name: '丢弃并继续' })).toBeTruthy()
      expect(action).not.toHaveBeenCalled()
      unmount()
    }
  })

  test('AC22 取消：目标 action 不执行，源/草稿不变', async () => {
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'draft-text' } })
    const action = vi.fn()
    render(<Harness action={action} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(action).not.toHaveBeenCalled()
    expect(workbenchStore.getState().dirtyScripts['3d.gdl']).toBe(true)
    expect(workbenchStore.getState().scriptContents['3d.gdl']).toBe('draft-text')
  })

  test('AC23 确认丢弃后 action 执行（目标打开失败由 store 保持原状态）', async () => {
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'draft-text' } })
    const action = vi.fn()
    render(<Harness action={action} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: '丢弃并继续' }))

    await waitFor(() => expect(action).toHaveBeenCalledTimes(1))
  })

  test('AC26 确认期间 projectEpoch 改变：不执行旧操作', async () => {
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'draft-text' } })
    const action = vi.fn()
    render(<Harness action={action} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await screen.findByRole('dialog')
    // 对话框打开期间项目已切换
    workbenchStore.setState({ projectEpoch: 2, sessionId: 's2' })
    fireEvent.click(screen.getByRole('button', { name: '丢弃并继续' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(action).not.toHaveBeenCalled()
  })

  test('AC27 冲突状态（busy/AI/编译/加载）下守卫不执行 action', async () => {
    for (const patch of [
      { sourceActionBusy: true },
      { assistantBusy: true },
      { compiling: true },
      { loading: true },
    ]) {
      resetStore(patch)
      const action = vi.fn()
      const { unmount } = render(<Harness action={action} />)

      fireEvent.click(screen.getByRole('button', { name: 'leave' }))
      await new Promise((resolve) => setTimeout(resolve, 20))
      expect(action).not.toHaveBeenCalled()
      expect(screen.queryByRole('dialog')).toBeNull()
      unmount()
    }
  })

  test('对话框打开期间重复点击不再叠第二个确认框', async () => {
    resetStore({ dirtyScripts: { '3d.gdl': true }, scriptContents: { '3d.gdl': 'x' } })
    const action = vi.fn()
    render(<Harness action={action} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: 'leave' }))

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(action).not.toHaveBeenCalled()
  })
})
