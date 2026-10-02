import { create } from 'zustand'
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  fetchAppVersion,
  isTauriDesktop,
  type UpdateChannel,
  type UpdateInfo,
} from '../api/updater'

export const UPDATE_CHANNEL_STORAGE_KEY = 'openbrep.update-channel'

function readUpdateChannel(): UpdateChannel {
  if (typeof window === 'undefined') return 'stable'
  return window.localStorage.getItem(UPDATE_CHANNEL_STORAGE_KEY) === 'development'
    ? 'development'
    : 'stable'
}

export type UpdatePhase = 'idle' | 'downloading' | 'installing' | 'error'

interface UpdateState {
  channel: UpdateChannel
  /** 当前应用版本（Tauri 环境下由 Rust 侧提供） */
  currentVersion: string | null
  /** 已完成的检查里发现的更新；null = 无更新或尚未检查 */
  info: UpdateInfo | null
  checking: boolean
  checked: boolean
  phase: UpdatePhase
  downloaded: number
  total: number | null
  error: string | null
  dialogOpen: boolean
  check: () => Promise<void>
  setChannel: (channel: UpdateChannel) => void
  startUpdate: () => Promise<void>
  openDialog: () => void
  closeDialog: () => void
}

/**
 * 桌面端自动更新状态（Hermes 式：顶栏版本 pill → 点击弹出更新对话框）。
 * 非 Tauri 环境下 check/startUpdate 全部 no-op，pill 与对话框不渲染。
 */
export const useUpdateStore = create<UpdateState>()((set, get) => ({
  channel: readUpdateChannel(),
  currentVersion: null,
  info: null,
  checking: false,
  checked: false,
  phase: 'idle',
  downloaded: 0,
  total: null,
  error: null,
  dialogOpen: false,

  check: async () => {
    if (!isTauriDesktop() || get().checking) return
    set({ checking: true, error: null })
    try {
      const channel = get().channel
      const [version, info] = await Promise.all([
        fetchAppVersion().catch(() => null),
        checkForUpdate(channel),
      ])
      set({
        info,
        checked: true,
        checking: false,
        currentVersion: info?.current_version ?? version ?? get().currentVersion,
      })
    } catch (e) {
      // 启动静默检查失败（断网/无已发布 Release/开发环境）不打扰用户
      set({ checking: false, checked: true, error: String(e) })
    }
  },

  startUpdate: async () => {
    const { info, phase } = get()
    if (!info || phase === 'downloading' || phase === 'installing') return
    set({ phase: 'downloading', downloaded: 0, total: null, error: null })
    try {
      await downloadAndInstallUpdate(get().channel, ({ downloaded, total }) => {
        set({ downloaded, total })
      })
      // 成功路径上 Rust 侧会直接重启应用，这里兜底展示安装中状态
      set({ phase: 'installing' })
    } catch (e) {
      set({ phase: 'error', error: String(e) })
    }
  },

  setChannel: (channel) => {
    window.localStorage.setItem(UPDATE_CHANNEL_STORAGE_KEY, channel)
    set({
      channel,
      info: null,
      checked: false,
      phase: 'idle',
      downloaded: 0,
      total: null,
      error: null,
    })
  },

  openDialog: () => set({ dialogOpen: true }),
  closeDialog: () => set({ dialogOpen: false }),
}))
