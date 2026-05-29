import { useState } from 'react'
import type { ReactNode } from 'react'
import type { CompileIssue, MockCompileResponse } from '../api/types'

interface BottomDrawerProps {
  warnings: string[]
  compileLog: string[]
  mockCompileResult: MockCompileResponse | null
  revisionPanel?: ReactNode
  onIssueSelect?: (issue: CompileIssue) => void
}

export function BottomDrawer({ warnings, compileLog, mockCompileResult, revisionPanel, onIssueSelect }: BottomDrawerProps) {
  const [activeTab, setActiveTab] = useState<'compile' | 'diagnostics' | 'preview' | 'revision'>('compile')
  const errors = mockCompileResult?.issues.filter((issue) => issue.severity === 'error') ?? []
  const nonErrors = mockCompileResult?.issues.filter((issue) => issue.severity !== 'error') ?? []

  return (
    <section className="bottom-drawer">
      <div className="drawer-tabs">
        <button className={activeTab === 'compile' ? 'active' : ''} onClick={() => setActiveTab('compile')}>
          编译日志
        </button>
        <button className={activeTab === 'diagnostics' ? 'active' : ''} onClick={() => setActiveTab('diagnostics')}>
          Diagnostics
        </button>
        <button className={activeTab === 'preview' ? 'active' : ''} onClick={() => setActiveTab('preview')}>
          Preview
        </button>
        <button className={activeTab === 'revision' ? 'active' : ''} onClick={() => setActiveTab('revision')}>
          Revision
        </button>
      </div>
      <div className="drawer-content">
        {activeTab === 'revision' ? revisionPanel : null}
        {activeTab === 'preview' ? <PreviewLog warnings={warnings} /> : null}
        {activeTab === 'compile' || activeTab === 'diagnostics' ? (
          <CompileDiagnostics
            compileLog={compileLog}
            duration={mockCompileResult?.duration_ms ?? null}
            errors={errors}
            nonErrors={nonErrors}
            outputPath={mockCompileResult?.output_path ?? null}
            parameterCount={mockCompileResult?.parameter_count ?? null}
            sizeBytes={mockCompileResult?.gsm_size_bytes ?? null}
            success={mockCompileResult?.success ?? null}
            onIssueSelect={onIssueSelect}
          />
        ) : null}
      </div>
    </section>
  )
}

function CompileDiagnostics({
  compileLog,
  duration,
  errors,
  nonErrors,
  outputPath,
  parameterCount,
  sizeBytes,
  success,
  onIssueSelect,
}: {
  compileLog: string[]
  duration: number | null
  errors: CompileIssue[]
  nonErrors: CompileIssue[]
  outputPath: string | null
  parameterCount: number | null
  sizeBytes: number | null
  success: boolean | null
  onIssueSelect?: (issue: CompileIssue) => void
}) {
  return (
    <>
      <div className="diagnostics-summary">
        <strong>Compile</strong>
        <span>{duration !== null ? `${duration} ms` : '未编译'}</span>
      </div>
      {success === null ? <p>未编译</p> : null}
      {success && errors.length === 0 && nonErrors.length === 0 ? <p className="diagnostic-pass">✓ 编译通过</p> : null}
      {outputPath ? <p>Output: {outputPath}</p> : null}
      {sizeBytes !== null || parameterCount !== null ? (
        <p>
          {sizeBytes !== null ? `Size: ${formatBytes(sizeBytes)}` : ''}
          {sizeBytes !== null && parameterCount !== null ? ' · ' : ''}
          {parameterCount !== null ? `Parameters: ${parameterCount}` : ''}
        </p>
      ) : null}
      {errors.map((issue, index) => (
        <button
          type="button"
          className="diagnostic-line diagnostic-error"
          key={`${issue.script}-${issue.line}-${index}`}
          onClick={() => onIssueSelect?.(issue)}
        >
          {formatIssue(issue)}
        </button>
      ))}
      {nonErrors.map((issue, index) => (
        <button
          type="button"
          className="diagnostic-line diagnostic-warning"
          key={`${issue.script}-${issue.line}-${index}`}
          onClick={() => onIssueSelect?.(issue)}
        >
          {formatIssue(issue)}
        </button>
      ))}
      {compileLog.length ? compileLog.map((entry) => <p key={entry}>{entry}</p>) : null}
    </>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function PreviewLog({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return <p>No preview warnings</p>
  return (
    <>
      {warnings.map((warning) => (
        <p key={warning}>Preview warning: {warning}</p>
      ))}
    </>
  )
}

function formatIssue(issue: { script: string; line: number | null; message: string }) {
  const line = issue.line && issue.line > 0 ? `:${issue.line}` : ''
  return `${issue.script}${line} - ${issue.message}`
}
