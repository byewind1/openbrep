import Editor from '@monaco-editor/react'
import type { Monaco, OnMount } from '@monaco-editor/react'
import { useEffect, useRef } from 'react'
import { focusRangeEnd } from './previewPicking'
import { GDL_THEME_ID, registerGdlLanguage, scriptLanguageForName } from '../workbench/editor/gdlLanguage'

interface ScriptEditorProps {
  scriptName: string
  content: string
  onChange: (value: string) => void
  isDirty: boolean
  focusLine?: number | null
  /** P1e：相关代码段末行，整段亮显；无/非法时退化为单行 */
  focusEndLine?: number | null
  focusKey?: number | null
}

export function ScriptEditor({
  scriptName,
  content,
  onChange,
  isDirty,
  focusLine,
  focusEndLine,
  focusKey,
}: ScriptEditorProps) {
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const monacoRef = useRef<Monaco | null>(null)
  const decorationIdsRef = useRef<string[]>([])

  // 聚焦跳转（P1a + P1e）：滚动定位 + 整段相关代码亮显。
  // 亮显清除时机：下一次跳转（focusKey 变化）/ 切换脚本（focusLine 归 null）
  // / 编辑内容变化（下方 content effect，行号可能已移位）。
  useEffect(() => {
    const editor = editorRef.current
    const monaco = monacoRef.current
    if (!editor || !monaco) return
    if (!focusLine || focusLine < 1) {
      decorationIdsRef.current = editor.deltaDecorations(decorationIdsRef.current, [])
      return
    }
    const endLine = focusRangeEnd(focusLine, focusEndLine)
    decorationIdsRef.current = editor.deltaDecorations(decorationIdsRef.current, [
      {
        range: new monaco.Range(focusLine, 1, endLine, 1),
        options: { isWholeLine: true, className: 'preview-source-segment' },
      },
    ])
    editor.revealLineInCenter(focusLine)
    editor.setPosition({ lineNumber: focusLine, column: 1 })
    editor.focus()
  }, [focusLine, focusEndLine, focusKey, scriptName])

  // 编辑内容变化 → 旧亮显行号可能已移位：清除且不再重放
  const lastContentRef = useRef(content)
  useEffect(() => {
    if (lastContentRef.current === content) return
    lastContentRef.current = content
    const editor = editorRef.current
    if (editor) {
      decorationIdsRef.current = editor.deltaDecorations(decorationIdsRef.current, [])
    }
  }, [content])

  return (
    <section className="script-editor">
      <div className="script-editor-tabbar">
        <span className="script-editor-tab">{scriptName}</span>
        {isDirty ? <span className="script-editor-dirty">●</span> : null}
      </div>
      <div className="script-editor-body">
        <Editor
          height="100%"
          language={scriptLanguageForName(scriptName)}
          value={content}
          theme={scriptName.endsWith('.gdl') ? GDL_THEME_ID : 'vs-dark'}
          beforeMount={registerGdlLanguage}
          onChange={(value) => onChange(value ?? '')}
          onMount={(editor, monaco) => {
            editorRef.current = editor
            monacoRef.current = monaco
          }}
          options={{
            fontSize: 13,
            fontFamily: 'JetBrains Mono, Consolas, monospace',
            automaticLayout: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            lineNumbers: 'on',
            renderLineHighlight: 'line',
            padding: { top: 8 },
            tabSize: 2,
            insertSpaces: true,
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: true, indentation: true },
            quickSuggestions: { other: true, comments: false, strings: false },
            suggestOnTriggerCharacters: true,
            wordBasedSuggestions: 'off',
          }}
        />
      </div>
    </section>
  )
}
