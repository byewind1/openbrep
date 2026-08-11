export const MAX_ASSISTANT_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_ASSISTANT_IMAGES = 4
export const SUPPORTED_ASSISTANT_IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/webp'])
const IMAGE_PATH_EXTENSIONS = /\.(png|jpe?g|webp)$/i

export function validateAssistantImageFile(file: File): string | null {
  if (!SUPPORTED_ASSISTANT_IMAGE_MIMES.has(file.type)) {
    return 'Use a PNG, JPEG, or WebP image.'
  }
  if (file.size > MAX_ASSISTANT_IMAGE_BYTES) {
    const sizeMb = file.size / (1024 * 1024)
    return `Image is too large (${sizeMb.toFixed(1)} MB). Use 5 MB or less.`
  }
  return null
}

/** 路径贴图判定：绝对路径形态（/ 或 ~ 或盘符开头）且以图片扩展名结尾。 */
export function isImagePathText(text: string): boolean {
  const trimmed = (text || '').trim()
  if (!trimmed || !IMAGE_PATH_EXTENSIONS.test(trimmed)) return false
  if (trimmed.startsWith('/') || trimmed.startsWith('~')) return true
  // 盘符：C:\Users\... 或 C:/Users/...
  return /^[A-Za-z]:[\\/]/.test(trimmed)
}

/** chip 标签：路径贴图显示路径，文件贴图显示文件名。 */
export function attachmentLabel(attachment: { name: string; path?: string }): string {
  return attachment.path || attachment.name
}
