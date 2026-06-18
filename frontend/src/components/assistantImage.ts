export const MAX_ASSISTANT_IMAGE_BYTES = 5 * 1024 * 1024
export const SUPPORTED_ASSISTANT_IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/webp'])

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
