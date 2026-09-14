import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/**
 * Tauri 桌面端自动更新封装。
 *
 * 所有函数在非 Tauri 环境（浏览器 / vitest）下安全降级：
 * 检查返回 null，其余为 no-op。真正的检查、验签、安装、重启
 * 都在 Rust 侧（src-tauri/src/main.rs 的自定义命令）完成，
 * 前端只负责状态展示——自定义命令不受 capabilities ACL 限制，
 * 因此窗口加载 localhost 后端页面也能直接调用。
 */

export interface UpdateInfo {
  version: string
  current_version: string
  notes: string | null
}

export interface UpdateProgress {
  downloaded: number
  total: number | null
}

export function isTauriDesktop(): boolean {
  // __TAURI_INTERNALS__ 是 Tauri 始终注入的 IPC 入口（@tauri-apps/api 实际走它）；
  // __TAURI__ 全局对象只有 withGlobalTauri 开启才有，不能作为唯一判据。
  if (typeof window === 'undefined' || !import.meta.env.VITE_IS_TAURI) return false
  const w = window as unknown as Record<string, unknown>
  return '__TAURI_INTERNALS__' in w || '__TAURI__' in w
}

export async function fetchAppVersion(): Promise<string | null> {
  if (!isTauriDesktop()) return null
  return invoke<string>('app_version')
}

/** 静默检查更新；无更新或失败（断网/无 Release）均返回 null 的语义由调用方区分。 */
export async function checkForUpdate(): Promise<UpdateInfo | null> {
  if (!isTauriDesktop()) return null
  return invoke<UpdateInfo | null>('updater_check')
}

/**
 * 下载并安装更新，成功后 Rust 侧直接重启应用（Promise 通常来不及 resolve）。
 * 进度事件 listen 失败时降级为无进度模式（能力配置缺失时仍可用）。
 */
export async function downloadAndInstallUpdate(
  onProgress: (progress: UpdateProgress) => void,
): Promise<void> {
  let unlisten: UnlistenFn | undefined
  try {
    unlisten = await listen<UpdateProgress>('updater-progress', (event) =>
      onProgress(event.payload),
    )
  } catch {
    unlisten = undefined
  }
  try {
    await invoke('updater_download_and_install')
  } finally {
    unlisten?.()
  }
}

/** 在系统浏览器打开 Release 页（自动更新失败时的手动下载降级路径）。 */
export async function openReleasesPage(): Promise<void> {
  if (!isTauriDesktop()) return
  await invoke('open_releases_page')
}
