import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { CompileIssue, MockCompileResponse } from '../api/types'
import {
  countGroupedStackedIssues,
  groupCompileIssuesByScript,
  stackGroupedIssues,
} from '../state/diagnostics'

interface BottomDrawerProps {
  warnings: string[]
  compileLog: string[]
  mockCompileResult: MockCompileResponse | null
  compiling?: boolean
  revisionPanel?: ReactNode
  onIssueSelect?: (issue: CompileIssue) => void
  onRevealOutput?: (path: string) => void
}

export function BottomDrawer({
  warnings,
  compileLog,
  mockCompileResult,
  compiling = false,
  revisionPanel,
  onIssueSelect,
  onRevealOutput,
}: BottomDrawerProps) {
  const [activeTab, setActiveTab] = useState<'compile' | 'preview' | 'revision'>('compile')
  // Track the last result we already reacted to by reference, so a stale or
  // repeated result never drags the user off the Revision/Preview tab.
  const lastResultRef = useRef<MockCompileResponse | null>(null)
  if (mockCompileResult !== lastResultRef.current) {
    lastResultRef.current = mockCompileResult
    if (mockCompileResult && mockCompileResult.success === false) {
      setActiveTab('compile')
    }
  }
  const issueGroups = groupCompileIssuesByScript(mockCompileResult?.issues ?? [])

  return (
    <section className="bottom-drawer">
      <div className="drawer-tabs">
        <button className={activeTab === 'compile' ? 'active' : ''} onClick={() => setActiveTab('compile')}>
          Compile
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
        {activeTab === 'compile' ? (
          <CompileDiagnostics
            compileLog={compileLog}
            compiling={compiling}
            duration={mockCompileResult?.duration_ms ?? null}
            error={mockCompileResult?.error ?? null}
            issueGroups={issueGroups}
            outputPath={mockCompileResult?.output_path ?? null}
            parameterCount={mockCompileResult?.parameter_count ?? null}
            sizeBytes={mockCompileResult?.gsm_size_bytes ?? null}
            success={mockCompileResult?.success ?? null}
            onIssueSelect={onIssueSelect}
            onRevealOutput={onRevealOutput}
          />
        ) : null}
      </div>
    </section>
  )
}

function CompileDiagnostics({
  compileLog,
  compiling,
  duration,
  error,
  issueGroups,
  outputPath,
  parameterCount,
  sizeBytes,
  success,
  onIssueSelect,
  onRevealOutput,
}: {
  compileLog: string[]
  compiling: boolean
  duration: number | null
  error: string | null
  issueGroups: ReturnType<typeof groupCompileIssuesByScript>
  outputPath: string | null
  parameterCount: number | null
  sizeBytes: number | null
  success: boolean | null
  onIssueSelect?: (issue: CompileIssue) => void
  onRevealOutput?: (path: string) => void
}) {
  const stackedGroups = stackGroupedIssues(issueGroups)
  const errorCount = countGroupedStackedIssues(stackedGroups, 'error')
  const warningCount = countGroupedStackedIssues(stackedGroups, 'warning')
  const infoCount = countGroupedStackedIssues(stackedGroups, 'info')
  const statusBadge = compiling
    ? { className: 'compile-status running', label: '● Compiling…' }
    : success === true
      ? { className: 'compile-status passed', label: '✓ Passed' }
      : success === false
        ? { className: 'compile-status failed', label: '✗ Failed' }
        : null
  return (
    <>
      <div className="diagnostics-summary">
        <strong>Compile</strong>
        {statusBadge ? <span className={statusBadge.className}>{statusBadge.label}</span> : null}
        <span>{duration !== null ? `${duration} ms` : 'Not compiled'}</span>
      </div>
      {!compiling && error ? <p className="diagnostic-line diagnostic-error">{error}</p> : null}
      {success && stackedGroups.length === 0 ? <p className="diagnostic-pass">✓ 编译通过</p> : null}
      {stackedGroups.length ? (
        <div className="diagnostic-pills">
          {errorCount ? <span className="diagnostic-pill error">{plural(errorCount, 'error')}</span> : null}
          {warningCount ? <span className="diagnostic-pill warning">{plural(warningCount, 'warning')}</span> : null}
          {infoCount ? <span className="diagnostic-pill info">{plural(infoCount, 'info')}</span> : null}
        </div>
      ) : null}
      {outputPath ? (
        <div className="diagnostic-output-row">
          <span title={outputPath}>Output: {outputPath}</span>
          <button type="button" onClick={() => onRevealOutput?.(outputPath)}>
            Reveal
          </button>
        </div>
      ) : null}
      {sizeBytes !== null || parameterCount !== null ? (
        <p>
          {sizeBytes !== null ? `Size: ${formatBytes(sizeBytes)}` : ''}
          {sizeBytes !== null && parameterCount !== null ? ' · ' : ''}
          {parameterCount !== null ? `Parameters: ${parameterCount}` : ''}
        </p>
      ) : null}
      {stackedGroups.map((group) => (
        <div className="diagnostic-group" key={group.script}>
          <div className="diagnostic-group-heading">
            <strong>{group.script}</strong>
            <span>
              {plural(group.errors.length, 'error')} · {plural(group.warnings.length, 'warning')}
            </span>
          </div>
          {[...group.errors, ...group.warnings, ...group.infos].map((entry) => (
            <button
              type="button"
              className={`diagnostic-line ${severityClass(entry.issue.severity)}`}
              key={`${entry.issue.script}-${entry.issue.line}-${entry.issue.message}`}
              onClick={() => onIssueSelect?.(entry.issue)}
            >
              {formatIssue(entry.issue)}
              {entry.count > 1 ? <span className="diagnostic-stack-count">×{entry.count}</span> : null}
            </button>
          ))}
        </div>
      ))}
      {compileLog.length ? compileLog.map((entry) => <p key={entry}>{entry}</p>) : null}
    </>
  )
}

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

function severityClass(severity: string) {
  if (severity === 'error') return 'diagnostic-error'
  if (severity === 'warning') return 'diagnostic-warning'
  return 'diagnostic-info'
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
        <p key={warning}>⚠ {warning}</p>
      ))}
    </>
  )
}

function formatIssue(issue: { script: string; line: number | null; message: string }) {
  const line = issue.line && issue.line > 0 ? `:${issue.line}` : ''
  return `${issue.script}${line} - ${issue.message}`
}
