import { useState } from 'react'
import type { ReactNode } from 'react'
import type { MockCompileResponse } from '../api/types'

interface BottomDrawerProps {
  warnings: string[]
  compileLog: string[]
  mockCompileResult: MockCompileResponse | null
  revisionPanel?: ReactNode
}

export function BottomDrawer({ warnings, compileLog, mockCompileResult, revisionPanel }: BottomDrawerProps) {
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
            success={mockCompileResult?.success ?? null}
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
  success,
}: {
  compileLog: string[]
  duration: number | null
  errors: Array<{ script: string; line: number | null; message: string }>
  nonErrors: Array<{ script: string; line: number | null; message: string }>
  success: boolean | null
}) {
  return (
    <>
      <div className="diagnostics-summary">
        <strong>Compile</strong>
        <span>{duration !== null ? `${duration} ms` : '未编译'}</span>
      </div>
      {success === null ? <p>未编译</p> : null}
      {success && errors.length === 0 && nonErrors.length === 0 ? <p className="diagnostic-pass">✓ 编译通过</p> : null}
      {errors.map((issue, index) => (
        <p className="diagnostic-error" key={`${issue.script}-${issue.line}-${index}`}>
          {formatIssue(issue)}
        </p>
      ))}
      {nonErrors.map((issue, index) => (
        <p className="diagnostic-warning" key={`${issue.script}-${issue.line}-${index}`}>
          {formatIssue(issue)}
        </p>
      ))}
      {compileLog.length ? compileLog.map((entry) => <p key={entry}>{entry}</p>) : null}
    </>
  )
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
