import { expect, test } from 'vitest'
import { countGroupedIssues, groupCompileIssuesByScript } from './diagnostics'

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
