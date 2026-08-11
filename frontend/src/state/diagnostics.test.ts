import { expect, test } from 'vitest'
import {
  countGroupedIssues,
  countGroupedStackedIssues,
  groupCompileIssuesByScript,
  stackDuplicateIssues,
  stackGroupedIssues,
} from './diagnostics'

test('groups compile issues by script with errors before warnings', () => {
  const groups = groupCompileIssuesByScript([
    { severity: 'warning', script: 'scripts/2d.gdl', line: 2, message: 'unused symbol' },
    { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' },
    { severity: 'warning', script: 'scripts/3d.gdl', line: 8, message: 'implicit DEL' },
    { severity: 'info', script: '', line: null, message: 'project note' },
  ])

  expect(groups.map((group) => group.script)).toEqual(['scripts/3d.gdl', 'scripts/2d.gdl', 'project'])
  expect(groups[0]?.errors.map((issue) => issue.message)).toEqual(['FOR/NEXT mismatch'])
  expect(groups[0]?.warnings.map((issue) => issue.message)).toEqual(['implicit DEL'])
  expect(groups[2]?.infos.map((issue) => issue.message)).toEqual(['project note'])
})

test('counts grouped error and warning severities', () => {
  const groups = groupCompileIssuesByScript([
    { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'bad transform' },
    { severity: 'warning', script: 'scripts/3d.gdl', line: 8, message: 'implicit DEL' },
    { severity: 'warning', script: 'scripts/2d.gdl', line: 4, message: 'fallback symbol' },
  ])

  expect(countGroupedIssues(groups, 'error')).toBe(1)
  expect(countGroupedIssues(groups, 'warning')).toBe(2)
})

test('stacks duplicate issues by script, line and message regardless of severity', () => {
  const stacked = stackDuplicateIssues([
    { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' },
    { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' },
    { severity: 'warning', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' },
    { severity: 'error', script: 'scripts/3d.gdl', line: 8, message: 'implicit DEL' },
  ])

  expect(stacked).toHaveLength(2)
  // first occurrence is retained, count carries the multiplicity
  expect(stacked[0]).toEqual({
    issue: { severity: 'error', script: 'scripts/3d.gdl', line: 12, message: 'FOR/NEXT mismatch' },
    count: 3,
  })
  expect(stacked[1]).toEqual({
    issue: { severity: 'error', script: 'scripts/3d.gdl', line: 8, message: 'implicit DEL' },
    count: 1,
  })
})

test('does not stack issues that differ on script or line', () => {
  const stacked = stackDuplicateIssues([
    { severity: 'error', script: 'a.gdl', line: 1, message: 'boom' },
    { severity: 'error', script: 'b.gdl', line: 1, message: 'boom' },
    { severity: 'error', script: 'a.gdl', line: 2, message: 'boom' },
  ])

  expect(stacked.map((entry) => entry.count)).toEqual([1, 1, 1])
})

test('stacks duplicates per severity bucket and counts stacked summaries', () => {
  const groups = groupCompileIssuesByScript([
    { severity: 'error', script: 'a.gdl', line: 3, message: 'bad' },
    { severity: 'error', script: 'a.gdl', line: 3, message: 'bad' },
    { severity: 'warning', script: 'a.gdl', line: 5, message: 'warn' },
    { severity: 'warning', script: 'a.gdl', line: 5, message: 'warn' },
    { severity: 'info', script: 'a.gdl', line: 7, message: 'note' },
  ])
  const stackedGroups = stackGroupedIssues(groups)

  expect(stackedGroups).toHaveLength(1)
  expect(stackedGroups[0]?.errors).toEqual([
    { issue: { severity: 'error', script: 'a.gdl', line: 3, message: 'bad' }, count: 2 },
  ])
  expect(stackedGroups[0]?.warnings).toEqual([
    { issue: { severity: 'warning', script: 'a.gdl', line: 5, message: 'warn' }, count: 2 },
  ])
  expect(stackedGroups[0]?.infos).toEqual([
    { issue: { severity: 'info', script: 'a.gdl', line: 7, message: 'note' }, count: 1 },
  ])

  expect(countGroupedStackedIssues(stackedGroups, 'error')).toBe(1)
  expect(countGroupedStackedIssues(stackedGroups, 'warning')).toBe(1)
  expect(countGroupedStackedIssues(stackedGroups, 'info')).toBe(1)
})
