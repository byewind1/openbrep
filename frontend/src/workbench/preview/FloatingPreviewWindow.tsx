import type { PreviewPayload } from '../../api/types'
import { PreviewViewport } from '../../components/PreviewViewport'

interface FloatingPreviewWindowProps {
  open: boolean
  preview: PreviewPayload | null
  warnings: string[]
  onClose: () => void
}

export function FloatingPreviewWindow({ open, preview, warnings, onClose }: FloatingPreviewWindowProps) {
  if (!open) {
    return null
  }

  return (
    <aside className="floating-preview-window" aria-label="Floating 3D preview">
      <header className="floating-preview-header">
        <div>
          <strong>3D Preview</strong>
          <span>可调大小</span>
        </div>
        <button type="button" onClick={onClose} aria-label="Close floating preview">
          关闭
        </button>
      </header>
      <div className="floating-preview-body">
        <PreviewViewport preview={preview} warnings={warnings} variant="floating" />
      </div>
    </aside>
  )
}
