import Editor from '@monaco-editor/react'

interface ScriptEditorProps {
  scriptName: string
  content: string
  onChange: (value: string) => void
  isDirty: boolean
}

export function ScriptEditor({ scriptName, content, onChange, isDirty }: ScriptEditorProps) {
  return (
    <section className="script-editor">
      <div className="script-editor-tabbar">
        <span className="script-editor-tab">{scriptName}</span>
        {isDirty ? <span className="script-editor-dirty">●</span> : null}
      </div>
      <div className="script-editor-body">
        <Editor
          height="100%"
          language={scriptName.endsWith('.xml') ? 'xml' : 'plaintext'}
          value={content}
          theme="vs-dark"
          onChange={(value) => onChange(value ?? '')}
          options={{
            fontSize: 13,
            fontFamily: 'JetBrains Mono, Consolas, monospace',
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            lineNumbers: 'on',
            renderLineHighlight: 'line',
            padding: { top: 8 },
          }}
        />
      </div>
    </section>
  )
}
