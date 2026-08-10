import { BufferAttribute, BufferGeometry } from 'three'
import type { PreviewMesh } from '../api/types'

/** 面级连通域编号（并查集）：共享顶点的面归一部件，按出现顺序编 0..K-1。
 *  P2b 从 PreviewViewport 提级至此：random 分色与爆炸分件共用同一份。 */
export function computeFaceComponents(faces: number[][], vertCount: number): number[] {
  const parent = new Int32Array(vertCount)
  for (let i = 0; i < vertCount; i++) parent[i] = i
  const find = (x: number): number => {
    let r = x
    while (parent[r] !== r) r = parent[r]
    while (parent[x] !== r) {
      const next = parent[x]
      parent[x] = r
      x = next
    }
    return r
  }
  const unite = (a: number, b: number) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent[rb] = ra
  }
  for (const tri of faces) {
    for (let k = 1; k < tri.length; k++) unite(tri[0], tri[k])
  }
  const remap = new Map<number, number>()
  return faces.map((tri) => {
    const root = find(tri[0])
    if (!remap.has(root)) remap.set(root, remap.size)
    return remap.get(root) as number
  })
}

export interface PartCentroid {
  compId: number
  /** 部件质心（centered 坐标：顶点已减过 offset） */
  centroid: [number, number, number]
}

export interface ExplodedPart extends PartCentroid {
  geometry: BufferGeometry
}

/**
 * 按连通域分件并求每部件顶点均值质心（P2b）。centroid 为 centered 坐标
 * （减过 offset），与渲染时的居中模型同一空间。
 */
export function partCentroids(mesh: PreviewMesh, offset: [number, number, number]): PartCentroid[] {
  return centroidOfComponents(mesh, offset, computeFaceComponents(mesh.faces, mesh.vertices.length))
}

/** 给定分件编号，按"部件去重顶点均值"求质心（centered 坐标，已减 offset） */
function centroidOfComponents(
  mesh: PreviewMesh,
  offset: [number, number, number],
  comp: number[],
): PartCentroid[] {
  const sums = new Map<number, { count: number; x: number; y: number; z: number; seen: Set<number> }>()
  mesh.faces.forEach((tri, fi) => {
    const c = comp[fi]
    let acc = sums.get(c)
    if (!acc) {
      acc = { count: 0, x: 0, y: 0, z: 0, seen: new Set<number>() }
      sums.set(c, acc)
    }
    for (const vi of tri) {
      if (acc.seen.has(vi)) continue
      acc.seen.add(vi)
      const [x, y, z] = mesh.vertices[vi]
      acc.x += x
      acc.y += y
      acc.z += z
      acc.count++
    }
  })
  return [...sums.entries()].map(([compId, acc]) => ({
    compId,
    centroid: [
      acc.x / acc.count - offset[0],
      acc.y / acc.count - offset[1],
      acc.z / acc.count - offset[2],
    ],
  }))
}

/** 整体质心（P2b）：可见 mesh 的全部顶点均值，不按体积加权。centered 坐标。 */
export function overallCentroid(meshes: PreviewMesh[], offset: [number, number, number]): [number, number, number] {
  let count = 0
  let x = 0
  let y = 0
  let z = 0
  for (const mesh of meshes) {
    for (const [vx, vy, vz] of mesh.vertices) {
      x += vx
      y += vy
      z += vz
      count++
    }
  }
  if (count === 0) return [0, 0, 0]
  return [x / count - offset[0], y / count - offset[1], z / count - offset[2]]
}

/** 爆炸因子 clamp：[0,1]；NaN/±Infinity 兜底 0（spec：非有限值一律 0） */
export function clampFactor(factor: number): number {
  if (!Number.isFinite(factor)) return 0
  return Math.min(1, Math.max(0, factor))
}

/**
 * 部件位移：`(partCentroid − overallCentroid) × factor`（centered 空间，直接
 * 用作 group position）。
 * 退化防护：factor=0 / 质心重合（含整体只有 1 个部件）→ 零向量，不散架不出 NaN。
 */
export function explodeOffset(
  partCentroid: [number, number, number],
  overall: [number, number, number],
  factor: number,
): [number, number, number] {
  const f = clampFactor(factor)
  if (f === 0) return [0, 0, 0]
  const dx = partCentroid[0] - overall[0]
  const dy = partCentroid[1] - overall[1]
  const dz = partCentroid[2] - overall[2]
  if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9 && Math.abs(dz) < 1e-9) return [0, 0, 0]
  return [dx * f, dy * f, dz * f]
}

/** 是否按连通域拆件渲染：爆炸开启或 random 模式（random 本来就要逐件取色） */
export function shouldSplitParts(explodeFactor: number, displayMode: string): boolean {
  return explodeFactor > 0 || displayMode === 'random'
}

/**
 * 拆件几何（P2b）：每部件独立 BufferGeometry，共享 wholeGeometry 的 position
 * attribute（与旧 random 分件同款，省内存），各自 setIndex + 法线。
 * 配合 partCentroids 一次 computeFaceComponents 算出部件与质心。
 */
export function buildExplodedParts(
  mesh: PreviewMesh,
  offset: [number, number, number],
  wholeGeometry: BufferGeometry,
): ExplodedPart[] {
  // computeFaceComponents 只算一次：byComp 收集索引、centroidOfComponents 求质心
  const comp = computeFaceComponents(mesh.faces, mesh.vertices.length)
  const byComp = new Map<number, number[]>()
  mesh.faces.forEach((tri, fi) => {
    const arr = byComp.get(comp[fi]) ?? []
    arr.push(...tri)
    byComp.set(comp[fi], arr)
  })
  return centroidOfComponents(mesh, offset, comp).map(({ compId, centroid }) => {
    const g = new BufferGeometry()
    g.setAttribute('position', wholeGeometry.getAttribute('position'))
    g.setIndex(new BufferAttribute(new Uint32Array(byComp.get(compId) ?? []), 1))
    g.computeVertexNormals()
    return { compId, centroid, geometry: g }
  })
}
