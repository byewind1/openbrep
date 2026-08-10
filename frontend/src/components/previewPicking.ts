import type { PreviewMesh, PreviewSourceRef } from '../api/types'

/**
 * 预览拾取 → GDL 源码跳转的纯逻辑（P1a）。
 * script_type → 脚本文件名映射、source_ref 解析、选中状态构造。
 * 视口组件只做组合；本模块不依赖 DOM/three，可直接单测。
 */

/** script_type → GDL 脚本文件名（"3d" → "3d.gdl"，与诊断跳转命名一致） */
export function sourceScriptName(scriptType: string | null | undefined): string | null {
  const type = (scriptType ?? '').trim()
  return type ? `${type}.gdl` : null
}

export interface ResolvedSource {
  scriptName: string
  line: number
  command: string
  /** 后端打点的 label（如 "3D line 42 BLOCK"）；空串归一为 null */
  label: string | null
  /** 信息条主文案：`3d.gdl:42 · BLOCK`（label 存在时追加 ` · label`） */
  summary: string
}

/**
 * 把后端 source_ref 解析为可跳转目标；不可跳转（无 source_ref、
 * script_type 缺失、line 非法）返回 null —— 调用方据此禁用跳转按钮。
 */
export function resolveSourceRef(sourceRef: PreviewSourceRef | null | undefined): ResolvedSource | null {
  if (!sourceRef) return null
  const scriptName = sourceScriptName(sourceRef.script_type)
  const line = Number(sourceRef.line)
  if (!scriptName || !Number.isFinite(line) || line <= 0) return null
  const command = (sourceRef.command ?? '').trim()
  const label = sourceRef.label && sourceRef.label.trim() ? sourceRef.label.trim() : null
  // label 不进 summary：后端打点恒为 "3D line N CMD"（gdl_previewer._source_ref_3d），
  // 与 script:line · command 完全重复；保留字段供 tooltip 等用途
  const parts = [`${scriptName}:${line}`]
  if (command) parts.push(command)
  return { scriptName, line, command, label, summary: parts.join(' · ') }
}

export interface PreviewSelection {
  /** 对应 PreviewPayload.meshes 的下标；random 模式分件也归到父 mesh */
  meshIndex: number
  meshName: string
  /** null = 该 mesh 无 source_ref（RULED 焊接合并等产物），不可跳转 */
  source: ResolvedSource | null
}

/** 由 mesh 构造选中状态（选中即解析 source，信息条直接消费） */
export function makeSelection(meshIndex: number, mesh: PreviewMesh): PreviewSelection {
  return {
    meshIndex,
    meshName: mesh.name,
    source: resolveSourceRef(mesh.source_ref),
  }
}
