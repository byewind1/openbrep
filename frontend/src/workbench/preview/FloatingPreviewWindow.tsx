import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { PreviewPayload } from '../../api/types'
import { useWorkbenchStore } from '../../state/useWorkbenchStore'

const PreviewViewport = lazy(() => import('../../components/PreviewViewport').then((m) => ({ default: m.PreviewViewport })))

interface FloatingPreviewWindowProps {
  open: boolean
  preview: PreviewPayload | null
  warnings: string[]
  hasDirtyScripts: boolean
  onClose: () => void
  /** 3D 预览选中 mesh 后跳转 GDL 源码段（scriptName 如 "3d.gdl"；endLine 相关段末行） */
  onRevealSource?: (scriptName: string, lineNumber: number, endLine?: number | null) => void
}

export function FloatingPreviewWindow({ open, preview, warnings, hasDirtyScripts, onClose, onRevealSource }: FloatingPreviewWindowProps) {
  // P1b：质量档从 store 取，不经过 props 倒灌
  const previewQuality = useWorkbenchStore((state) => state.previewQuality)
  const setPreviewQuality = useWorkbenchStore((state) => state.setPreviewQuality)
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
        <div className="floating-preview-title">
          <strong>3D Preview</strong>
          <span>{fullscreen ? 'Fullscreen preview' : 'Drag header to move, resize from the corner'}</span>
        </div>
        <div className="floating-preview-actions">
          <button type="button" onClick={() => setFullscreen((value) => !value)}>
            {fullscreen ? 'Restore' : 'Fullscreen'}
          </button>
          <button type="button" onClick={onClose} aria-label="Close floating preview">
            Close
          </button>
        </div>
      </header>
      <div className="floating-preview-body">
        <Suspense fallback={<div className="viewport-loading" />}>
          <PreviewViewport
            preview={preview}
            warnings={warnings}
            variant="floating"
            hasDirtyScripts={hasDirtyScripts}
            onRevealSource={onRevealSource}
            quality={previewQuality}
            onQualityChange={(quality) => void setPreviewQuality(quality)}
          />
        </Suspense>
      </div>
    </aside>
  )
}
