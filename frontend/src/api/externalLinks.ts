/**
 * P0-B：助手消息内外链的统一安全打开入口。
 *
 * 桌面端（Tauri）走 open_external_url 自定义命令（Rust 侧校验协议白名单后
 * 交系统浏览器）；浏览器环境 window.open。无论哪条路，http/https 之外的
 * 协议（javascript:、file: 等）在这里先被拒绝——聊天里的链接是模型产物，
 * 不允许触发本地处理器。
 */

export function isSafeExternalUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

export async function openExternalUrl(url: string): Promise<boolean> {
  if (!isSafeExternalUrl(url)) return false
  const { isTauriDesktop } = await import('./updater')
  if (isTauriDesktop()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('open_external_url', { url })
      return true
    } catch {
      return false
    }
  }
  window.open(url, '_blank', 'noopener,noreferrer')
  return true
}
