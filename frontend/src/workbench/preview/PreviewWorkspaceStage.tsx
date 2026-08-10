import { lazy, Suspense } from 'react'
import type { PreviewPayload } from '../../api/types'

const ScriptEditor = lazy(() => import('../../components/ScriptEditor').then((m) => ({ default: m.ScriptEditor })))
const PreviewViewport = lazy(() => import('../../components/PreviewViewport').then((m) => ({ default: m.PreviewViewport })))

interface PreviewWorkspaceStageProps {
  previewWorkspaceOpen: boolean
  preview: PreviewPayload | null
  warnings: string[]
  activeScriptName: string | null
  activeScriptContent: string
  hasDirtyScript: boolean
  hasDirtyScripts: boolean
  activeFocusLine: number | null
  /** P1e：相关代码段末行（整段亮显用），单行定位为 null */
  activeFocusEndLine: number | null
  activeFocusKey: number | null
  onCollapsePreview: () => void
  onFloatPreview: () => void
  onChangeScript: (content: string) => void
  onRefreshPreview?: () => void
  /** 3D 预览选中 mesh 后跳转 GDL 源码段（scriptName 如 "3d.gdl"；endLine 相关段末行） */
  onRevealSource?: (scriptName: string, lineNumber: number, endLine?: number | null) => void
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
  activeFocusEndLine,
  activeFocusKey,
  onCollapsePreview,
  onFloatPreview,
  onChangeScript,
  onRefreshPreview,
  onRevealSource,
}: PreviewWorkspaceStageProps) {
  // 两个舞台常驻 DOM、用 display 切换：保证来回切换不丢 3D 相机视角和编辑器滚动位置
  return (
    <>
      <section className={`workbench-main-stage preview-workspace-stage${previewWorkspaceOpen ? '' : ' stage-hidden'}`}>
        <Suspense fallback={<div className="viewport-loading" />}>
          <PreviewViewport
            preview={preview}
            warnings={warnings}
            variant="workspace"
            expanded
            hasDirtyScripts={hasDirtyScripts}
            onCollapse={onCollapsePreview}
            onFloat={onFloatPreview}
            onRevealSource={onRevealSource}
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
        </Suspense>
      </section>
      <section className={`workbench-main-stage editor-stage${previewWorkspaceOpen ? ' stage-hidden' : ''}`}>
        {activeScriptName ? (
          <Suspense fallback={<div className="editor-loading" />}>
            <ScriptEditor
              scriptName={activeScriptName}
              content={activeScriptContent}
              onChange={onChangeScript}
              isDirty={hasDirtyScript}
              focusLine={activeFocusLine}
              focusEndLine={activeFocusEndLine}
              focusKey={activeFocusKey}
            />
          </Suspense>
        ) : (
          <div className="editor-empty">No script loaded</div>
        )}
      </section>
    </>
  )
}
