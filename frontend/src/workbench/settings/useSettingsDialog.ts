import { useEffect, useRef } from 'react'

function visible(element: HTMLElement) {
  if (element.closest('[hidden], [inert]')) return false
  const style = getComputedStyle(element)
  return style.display !== 'none' && style.visibility !== 'hidden'
}

function layer(element: HTMLElement) {
  const layers: number[] = []
  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    const value = Number.parseInt(getComputedStyle(ancestor).zIndex, 10)
    if (Number.isFinite(value)) layers.unshift(value)
  }
  return layers
}

function topDialog() {
  return Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]'))
    .filter(visible).sort((a, b) => {
      const left = layer(a)
      const right = layer(b)
      for (let i = 0; i < Math.max(left.length, right.length); i++) {
        const difference = (left[i] ?? 0) - (right[i] ?? 0)
        if (difference) return difference
      }
      return 0
    }).at(-1)
}

/** Settings-owned dialogs share focus/keyboard behavior without capturing higher layers. */
export function useSettingsDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = ref.current
    if (!dialog) return
    const frame = requestAnimationFrame(() => {
      if (topDialog() === dialog && !dialog.contains(document.activeElement)) dialog.focus()
    })

    function handleKeyDown(event: KeyboardEvent) {
      if (!dialog || event.defaultPrevented || topDialog() !== dialog) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeRef.current()
      } else if (event.key === 'Tab') {
        const candidates = Array.from(dialog.querySelectorAll<HTMLElement>(
          'button, input, select, textarea, a[href], [tabindex], summary',
        )).filter((element) => visible(element) && element.tabIndex >= 0 && !element.matches(':disabled'))
        // A radio group contributes one native Tab stop: its checked item.
        const items = candidates.filter((element) => {
          if (!(element instanceof HTMLInputElement) || element.type !== 'radio' || !element.name) return true
          const group = candidates.filter((item): item is HTMLInputElement =>
            item instanceof HTMLInputElement && item.type === 'radio' &&
            item.name === element.name && item.form === element.form)
          return element === (group.find((item) => item.checked) ?? group[0])
        })
        const first = items[0]
        const last = items.at(-1)
        const active = document.activeElement
        if (!first) {
          event.preventDefault()
          dialog.focus()
        } else if (event.shiftKey && (active === first || active === dialog || !dialog.contains(active))) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && (active === last || active === dialog || !dialog.contains(active))) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', handleKeyDown)
      if (trigger?.isConnected) trigger.focus()
    }
  }, [open])
  return ref
}
