import type { ApiHealthEvent } from '../api/client'
import { probeBackend, setApiHealthListener } from '../api/client'
import type { WorkbenchGet, WorkbenchSet } from './workbenchStoreTypes'

/** 后端健康看门狗：失败分型展示 + 3s 恢复轮询 + 恢复后自动清横幅。 */

const BACKEND_DOWN_MESSAGE = [
  '❌ 后端服务未运行',
  '',
  'OpenBrep 的 Python 后端已停止（可能是终端被关闭）。',
  '→ 在终端运行：obr7 --daemon',
  '→ 或检查状态：obr7 --status',
].join('\n')
const BACKEND_STARTING_MESSAGE = '⏳ 后端无响应（HTTP 502/504），可能正在启动中…'
const BACKEND_TIMEOUT_MESSAGE = '⏳ 后端响应超时'
export const BACKEND_RECOVERED_MESSAGE = '✅ 已恢复连接'

const RECOVERY_POLL_MS = 3000
const NOTICE_DISMISS_MS = 4000
const PROBE_TIMEOUT_MS = 5000

export function createBackendHealth({
  get,
  set,
}: {
  get: WorkbenchGet
  set: WorkbenchSet
}) {
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let noticeTimer: ReturnType<typeof setTimeout> | null = null

  function stopPoll() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPoll() {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      void probeBackend(PROBE_TIMEOUT_MS)
    }, RECOVERY_POLL_MS)
  }

  function dismissNoticeSoon() {
    if (noticeTimer !== null) clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => set({ backendNotice: null }), NOTICE_DISMISS_MS)
  }

  function handleEvent(event: ApiHealthEvent) {
    if (event.kind === 'ok') {
      // 任何请求成功都是恢复信号：清错误、给一次性"已恢复"提示
      if (get().backendError !== null) {
        stopPoll()
        set({ backendError: null, lastError: null, backendNotice: BACKEND_RECOVERED_MESSAGE })
        dismissNoticeSoon()
      }
      return
    }
    const message =
      event.kind === 'down'
        ? BACKEND_DOWN_MESSAGE
        : event.kind === 'starting'
          ? BACKEND_STARTING_MESSAGE
          : BACKEND_TIMEOUT_MESSAGE
    set({
      backendError: { kind: event.kind, at: Date.now() },
      lastError: message,
      backendNotice: null,
    })
    startPoll()
  }

  function install() {
    setApiHealthListener(handleEvent)
  }

  return { install, handleEvent, _internals: { stopPoll, startPoll } }
}
