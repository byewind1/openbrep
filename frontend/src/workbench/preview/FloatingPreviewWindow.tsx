import { useEffect, useRef, useState } from 'react'
import type { PreviewPayload } from '../../api/types'
import { PreviewViewport } from '../../components/PreviewViewport'

interface FloatingPreviewWindowProps {
  open: boolean
  preview: PreviewPayload | null
  warnings: string[]
  onClose: () => void
}

export function FloatingPreviewWindow({ open, preview, warnings, onClose }: FloatingPreviewWindowProps) {
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [fullscreen, setFullscreen] = useState(false)
  const dragOffsetRef = useRef<{ x: number; y: number } | null>(null)

  useEffect(() => {
    if (!open) {
      dragOffsetRef.current = null
      setFullscreen(false)
      return
    }

    const width = Math.min(760, window.innerWidth - 56)
    setPosition({
      x: Math.max(16, window.innerWidth - width - 28),
      y: 72,
    })
  }, [open])

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      const dragOffset = dragOffsetRef.current
      if (!dragOffset || fullscreen) {
        return
      }

      setPosition({
        x: Math.max(0, Math.min(window.innerWidth - 80, event.clientX - dragOffset.x)),
        y: Math.max(0, Math.min(window.innerHeight - 48, event.clientY - dragOffset.y)),
      })
    }

    function handlePointerUp() {
      dragOffsetRef.current = null
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
    }
  }, [fullscreen])

  if (!open) {
    return null
  }

  return (
    <aside
      className={`floating-preview-window${fullscreen ? ' fullscreen' : ''}`}
      style={fullscreen ? undefined : { left: position.x, top: position.y }}
      aria-label="Floating 3D preview"
    >
      <header
        className="floating-preview-header"
        onPointerDown={(event) => {
          if (fullscreen || event.button !== 0 || event.target instanceof HTMLButtonElement) {
            return
          }
          dragOffsetRef.current = {
            x: event.clientX - position.x,
            y: event.clientY - position.y,
          }
        }}
      >
        <div>
          <strong>3D Preview</strong>
          <span>{fullscreen ? '全屏预览' : '拖动标题栏移动，右下角调大小'}</span>
        </div>
        <div className="floating-preview-actions">
          <button type="button" onClick={() => setFullscreen((value) => !value)}>
            {fullscreen ? '还原' : '全屏'}
          </button>
          <button type="button" onClick={onClose} aria-label="Close floating preview">
            关闭
          </button>
        </div>
      </header>
      <div className="floating-preview-body">
        <PreviewViewport preview={preview} warnings={warnings} variant="floating" />
      </div>
    </aside>
  )
}
