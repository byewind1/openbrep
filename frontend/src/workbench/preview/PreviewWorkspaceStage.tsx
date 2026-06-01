import { ScriptEditor } from '../../components/ScriptEditor'
import type { PreviewPayload } from '../../api/types'
import { PreviewViewport } from '../../components/PreviewViewport'

interface PreviewWorkspaceStageProps {
  previewWorkspaceOpen: boolean
  preview: PreviewPayload | null
  warnings: string[]
  activeScriptName: string | null
  activeScriptContent: string
  hasDirtyScript: boolean
  activeFocusLine: number | null
  activeFocusKey: number | null
  onCollapsePreview: () => void
  onFloatPreview: () => void
  onChangeScript: (content: string) => void
}

export function PreviewWorkspaceStage({
  previewWorkspaceOpen,
  preview,
  warnings,
  activeScriptName,
  activeScriptContent,
  hasDirtyScript,
  activeFocusLine,
  activeFocusKey,
  onCollapsePreview,
  onFloatPreview,
  onChangeScript,
}: PreviewWorkspaceStageProps) {
  if (previewWorkspaceOpen) {
    return (
      <section className="workbench-main-stage preview-workspace-stage">
        <PreviewViewport
          preview={preview}
          warnings={warnings}
          variant="workspace"
          expanded
          onCollapse={onCollapsePreview}
          onFloat={onFloatPreview}
        />
      </section>
    )
  }

  return (
    <section className="workbench-main-stage editor-stage">
      {activeScriptName ? (
        <ScriptEditor
          scriptName={activeScriptName}
          content={activeScriptContent}
          onChange={onChangeScript}
          isDirty={hasDirtyScript}
          focusLine={activeFocusLine}
          focusKey={activeFocusKey}
        />
      ) : (
        <div className="editor-empty">No script loaded</div>
      )}
    </section>
  )
}
