import type { MockCompileResponse } from '../api/types'

interface BottomDrawerProps {
  warnings: string[]
  compileLog: string[]
  mockCompileResult: MockCompileResponse | null
}

export function BottomDrawer({ warnings, compileLog, mockCompileResult }: BottomDrawerProps) {
  const errors = mockCompileResult?.issues.filter((issue) => issue.severity === 'error') ?? []
  const nonErrors = mockCompileResult?.issues.filter((issue) => issue.severity !== 'error') ?? []

  return (
    <section className="bottom-drawer">
      <div className="drawer-tabs">
        <button className="active">编译日志</button>
        <button>Diagnostics</button>
        <button>Preview</button>
        <button>Revision</button>
      </div>
      <div className="drawer-content">
        <div className="diagnostics-summary">
          <strong>Mock Compile</strong>
          <span>{mockCompileResult ? `${mockCompileResult.duration_ms} ms` : '未编译'}</span>
        </div>
        {!mockCompileResult ? <p>未编译</p> : null}
        {mockCompileResult?.success && mockCompileResult.issues.length === 0 ? <p className="diagnostic-pass">✓ 编译通过</p> : null}
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
        {warnings.map((warning) => (
          <p key={warning}>Preview warning: {warning}</p>
        ))}
      </div>
    </section>
  )
}

function formatIssue(issue: { script: string; line: number | null; message: string }) {
  const line = issue.line && issue.line > 0 ? `:${issue.line}` : ''
  return `${issue.script}${line} - ${issue.message}`
}
