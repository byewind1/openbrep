import Editor from '@monaco-editor/react'
import type { OnMount } from '@monaco-editor/react'
import { useEffect, useRef } from 'react'
import { GDL_THEME_ID, registerGdlLanguage, scriptLanguageForName } from '../workbench/editor/gdlLanguage'

interface ScriptEditorProps {
  scriptName: string
  content: string
  onChange: (value: string) => void
  isDirty: boolean
  focusLine?: number | null
  focusKey?: number | null
}

export function ScriptEditor({ scriptName, content, onChange, isDirty, focusLine, focusKey }: ScriptEditorProps) {
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)

  useEffect(() => {
    if (!focusLine || focusLine < 1 || !editorRef.current) {
      return
    }
    editorRef.current.revealLineInCenter(focusLine)
    editorRef.current.setPosition({ lineNumber: focusLine, column: 1 })
    editorRef.current.focus()
  }, [focusLine, focusKey, scriptName])

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
          onMount={(editor) => {
            editorRef.current = editor
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
