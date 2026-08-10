import { describe, expect, test } from 'vitest'
import type { PreviewMesh } from '../api/types'
import { makeSelection, resolveSourceRef, sourceScriptName } from './previewPicking'

describe('sourceScriptName (script_type → 脚本文件名)', () => {
  test('maps script types to .gdl filenames like diagnostics jump', () => {
    expect(sourceScriptName('3d')).toBe('3d.gdl')
    expect(sourceScriptName('2d')).toBe('2d.gdl')
    expect(sourceScriptName('master')).toBe('master.gdl')
  })

  test('returns null for empty/whitespace/undefined script type', () => {
    expect(sourceScriptName('')).toBeNull()
    expect(sourceScriptName('   ')).toBeNull()
    expect(sourceScriptName(null)).toBeNull()
    expect(sourceScriptName(undefined)).toBeNull()
  })
})

describe('resolveSourceRef (source_ref → 跳转目标)', () => {
  test('resolves a full source_ref to scriptName/line/command/label', () => {
    const resolved = resolveSourceRef({ script_type: '3d', line: 42, command: 'BLOCK', label: '3D line 42 BLOCK' })
    expect(resolved).not.toBeNull()
    expect(resolved?.scriptName).toBe('3d.gdl')
    expect(resolved?.line).toBe(42)
    expect(resolved?.command).toBe('BLOCK')
    expect(resolved?.label).toBe('3D line 42 BLOCK')
    // label 冗余于 script:line · command（恒为 "3D line N CMD"），不进 summary
    expect(resolved?.summary).toBe('3d.gdl:42 · BLOCK')
  })

  test('builds summary as "script:line · command" without label when label is empty', () => {
    const resolved = resolveSourceRef({ script_type: '3d', line: 7, command: 'BLOCK', label: '' })
    expect(resolved?.summary).toBe('3d.gdl:7 · BLOCK')
    expect(resolved?.label).toBeNull()
  })

  test('trims whitespace-only label down to null', () => {
    const resolved = resolveSourceRef({ script_type: '3d', line: 7, command: 'BLOCK', label: '   ' })
    expect(resolved?.label).toBeNull()
  })

  test('omits command and label when both are absent', () => {
    const resolved = resolveSourceRef({ script_type: '3d', line: 9, command: '', label: '' })
    expect(resolved?.summary).toBe('3d.gdl:9')
  })

  test('returns null for null/undefined source_ref (RULED 焊接合并等产物)', () => {
    expect(resolveSourceRef(null)).toBeNull()
    expect(resolveSourceRef(undefined)).toBeNull()
  })

  test('returns null when script_type is missing (no script file to jump to)', () => {
    expect(resolveSourceRef({ script_type: '', line: 5, command: 'BLOCK', label: '' })).toBeNull()
    expect(resolveSourceRef({ script_type: '  ', line: 5, command: 'BLOCK', label: '' })).toBeNull()
  })

  test('returns null for non-positive or non-finite line numbers', () => {
    const base = { script_type: '3d', command: 'BLOCK', label: '' }
    expect(resolveSourceRef({ ...base, line: 0 })).toBeNull()
    expect(resolveSourceRef({ ...base, line: -1 })).toBeNull()
    expect(resolveSourceRef({ ...base, line: Number.NaN })).toBeNull()
    expect(resolveSourceRef({ ...base, line: Number.POSITIVE_INFINITY })).toBeNull()
  })
})

describe('makeSelection (选中状态构造)', () => {
  test('resolves source for a mesh that carries source_ref', () => {
    const mesh: PreviewMesh = {
      name: 'BLOCK',
      vertices: [],
      faces: [],
      source_ref: { script_type: '3d', line: 42, command: 'BLOCK', label: '' },
    }
    const selection = makeSelection(3, mesh)
    expect(selection.meshIndex).toBe(3)
    expect(selection.meshName).toBe('BLOCK')
    expect(selection.source?.scriptName).toBe('3d.gdl')
    expect(selection.source?.line).toBe(42)
  })

  test('reports source=null for meshes without source_ref → jump disabled', () => {
    const selection = makeSelection(1, { name: 'RULED_0', vertices: [], faces: [] })
    expect(selection.meshName).toBe('RULED_0')
    expect(selection.source).toBeNull()
  })
})
