import type { CompileIssue } from '../api/types'

export interface CompileIssueGroup {
  script: string
  errors: CompileIssue[]
  warnings: CompileIssue[]
  infos: CompileIssue[]
}

export function groupCompileIssuesByScript(issues: CompileIssue[]): CompileIssueGroup[] {
  const groups = new Map<string, CompileIssueGroup>()

  for (const issue of issues) {
    const script = issue.script || 'project'
    const group = groups.get(script) ?? { script, errors: [], warnings: [], infos: [] }
    if (issue.severity === 'error') {
      group.errors.push(issue)
    } else if (issue.severity === 'warning') {
      group.warnings.push(issue)
    } else {
      group.infos.push(issue)
    }
    groups.set(script, group)
  }

  return [...groups.values()].sort((a, b) => {
    const severityDelta = b.errors.length - a.errors.length || b.warnings.length - a.warnings.length
    if (severityDelta !== 0) return severityDelta
    return a.script.localeCompare(b.script)
  })
}

export function countGroupedIssues(groups: CompileIssueGroup[], severity: 'error' | 'warning') {
  const key = severity === 'error' ? 'errors' : 'warnings'
  return groups.reduce((total, group) => total + group[key].length, 0)
}
