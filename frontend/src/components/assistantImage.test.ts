import { describe, expect, test } from 'vitest'
import { validateAssistantImageFile } from './assistantImage'

describe('validateAssistantImageFile', () => {
  test('accepts supported image files under 5 MB', () => {
    const file = new File(['small'], 'shelf.png', { type: 'image/png' })

    expect(validateAssistantImageFile(file)).toBeNull()
  })

  test('rejects unsupported image files', () => {
    const file = new File(['gif'], 'animated.gif', { type: 'image/gif' })

    expect(validateAssistantImageFile(file)).toContain('PNG, JPEG, or WebP')
  })

  test('rejects files over 5 MB', () => {
    const file = new File(['x'], 'large.jpg', { type: 'image/jpeg' })
    Object.defineProperty(file, 'size', { value: 5 * 1024 * 1024 + 1 })

    expect(validateAssistantImageFile(file)).toContain('5 MB')
  })
})
