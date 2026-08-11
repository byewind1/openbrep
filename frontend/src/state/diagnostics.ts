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

export interface StackedIssue {
  issue: CompileIssue
  count: number
}

export interface StackedIssueGroup {
  script: string
  errors: StackedIssue[]
  warnings: StackedIssue[]
  infos: StackedIssue[]
}

const stackedIssueKey = (issue: CompileIssue) =>
  `${issue.script || 'project'}\u0000${issue.line ?? ''}\u0000${issue.message}`

/**
 * Merges issues that are identical within the same script on (line, message).
 * The first occurrence is retained; `count` carries the multiplicity.
 */
export function stackDuplicateIssues(issues: CompileIssue[]): StackedIssue[] {
  const first = new Map<string, CompileIssue>()
  const counts = new Map<string, number>()

  for (const issue of issues) {
    const key = stackedIssueKey(issue)
    if (!first.has(key)) first.set(key, issue)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }

  return [...first].map(([key, issue]) => ({ issue, count: counts.get(key) ?? 1 }))
}

/** Applies duplicate stacking within each severity bucket of every script group. */
export function stackGroupedIssues(groups: CompileIssueGroup[]): StackedIssueGroup[] {
  return groups.map((group) => ({
    script: group.script,
    errors: stackDuplicateIssues(group.errors),
    warnings: stackDuplicateIssues(group.warnings),
    infos: stackDuplicateIssues(group.infos),
  }))
}

export function countGroupedStackedIssues(
  stackedGroups: StackedIssueGroup[],
  severity: 'error' | 'warning' | 'info',
) {
  const key = severity === 'error' ? 'errors' : severity === 'warning' ? 'warnings' : 'infos'
  return stackedGroups.reduce((total, group) => total + group[key].length, 0)
}
