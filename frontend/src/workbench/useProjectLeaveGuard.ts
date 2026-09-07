import { useCallback, useRef } from 'react'
import type { ReactNode } from 'react'
import { useThemedDialog } from '../components/ThemedDialog'
import { useT } from '../i18n'
import { workbenchStore } from '../state/workbenchStore'
import { captureProjectIdentity, hasAnyDraft, sameProjectIdentity } from '../state/actions/sourceActionHelpers'

// SF1（F04）：所有"离开当前项目"入口（New / Close·Reset / Open / Recent / Browse /
// Import GDL·GSM·Blender / 工作区项目打开）共用的确认守卫。
// 只有"取消 / 丢弃并继续"两个选择；不自动保存、不自动 Apply。
// 目标打开失败或原生文件框取消时保持原草稿（由 store action 自身的失败语义保证，
// 守卫不手动清状态）。

export interface ProjectLeaveGuard {
  runLeaveAction: (label: string, action: () => Promise<unknown> | void) => Promise<void>
  dialogNode: ReactNode
}

function conflictsNow(): boolean {
  const state = workbenchStore.getState()
  return (
    state.sourceActionBusy ||
    state.assistantBusy ||
    state.compiling ||
    state.loading
  )
}

export function useProjectLeaveGuard(): ProjectLeaveGuard {
  const { confirm, dialogNode } = useThemedDialog()
  const t = useT()
  // 守卫自身的 pending 标志：重复点击不会叠第二个确认框
  const pendingRef = useRef(false)

  const runLeaveAction = useCallback(
    async (label: string, action: () => Promise<unknown> | void) => {
      if (pendingRef.current) return
      if (conflictsNow()) return
      const identity = captureProjectIdentity(workbenchStore.getState())
      if (hasAnyDraft(workbenchStore.getState())) {
        pendingRef.current = true
        let discard = false
        try {
          discard = await confirm({
            title: label,
            message: t('leave.discardMessage', { label }),
            danger: true,
            confirmLabel: t('leave.discardAndContinue'),
          })
        } finally {
          pendingRef.current = false
        }
        if (!discard) return
      }
      // 确认对话框返回后重新检查项目身份与冲突状态：变了就不执行旧操作
      const state = workbenchStore.getState()
      if (!sameProjectIdentity(state, identity)) return
      if (conflictsNow()) return
      await action()
      // action 成功后沿用现有 hydrate 清理；失败/文件框取消由 store 保持原状态
    },
    [confirm, t],
  )

  return { runLeaveAction, dialogNode }
}
