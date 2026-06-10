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
  hasDirtyScripts: boolean
  activeFocusLine: number | null
  activeFocusKey: number | null
  onCollapsePreview: () => void
  onFloatPreview: () => void
  onChangeScript: (content: string) => void
  onRefreshPreview?: () => void
}

export function PreviewWorkspaceStage({
  previewWorkspaceOpen,
  preview,
  warnings,
  activeScriptName,
  activeScriptContent,
  hasDirtyScript,
  hasDirtyScripts,
  activeFocusLine,
  activeFocusKey,
  onCollapsePreview,
  onFloatPreview,
  onChangeScript,
  onRefreshPreview,
}: PreviewWorkspaceStageProps) {
  // 两个舞台常驻 DOM、用 display 切换：保证来回切换不丢 3D 相机视角和编辑器滚动位置
  return (
    <>
      <section className={`workbench-main-stage preview-workspace-stage${previewWorkspaceOpen ? '' : ' stage-hidden'}`}>
        <PreviewViewport
          preview={preview}
          warnings={warnings}
          variant="workspace"
          expanded
          hasDirtyScripts={hasDirtyScripts}
          onCollapse={onCollapsePreview}
          onFloat={onFloatPreview}
          actions={
            onRefreshPreview ? (
              <button
                type="button"
                className="viewport-action-button"
                onClick={onRefreshPreview}
                title="Update preview from current editor buffer"
              >
                Update
              </button>
            ) : null
          }
        />
      </section>
      <section className={`workbench-main-stage editor-stage${previewWorkspaceOpen ? ' stage-hidden' : ''}`}>
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
    </>
  )
}
