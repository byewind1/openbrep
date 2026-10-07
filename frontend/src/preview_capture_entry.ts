import { AgXToneMapping, AmbientLight, Color, DirectionalLight, DoubleSide, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, Scene, SRGBColorSpace, WebGLRenderer, Group, Mesh, PMREMGenerator, Vector3 } from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { PreviewPayload } from './api/types'
import { buildMeshGeometry } from './components/previewGhost'
import { computePreviewBounds, perspectiveDistanceForBounds, PREVIEW_CAMERA_FOV_DEGREES, viewDirectionForPreset, viewUpForPreset } from './components/previewCamera'
import { semanticMaterialStyle } from './components/previewMaterials'
import { PREVIEW_RENDER_SETTINGS } from './components/previewRenderContract'

type CapturePreset = 'material_iso' | 'front' | 'side' | 'neutral'
type CaptureInput = { payload: PreviewPayload; preset: CapturePreset }

declare global {
  interface Window {
    __OPENBREP_CAPTURE__?: CaptureInput
    __OPENBREP_CAPTURE_READY__?: boolean
    __OPENBREP_CAPTURE_CAMERA__?: {
      preset: CapturePreset
      fov_degrees: number
      target: number[]
      direction: number[]
      distance: number
      viewport: { width: number; height: number }
    }
  }
}

function capture(): void {
  const input = window.__OPENBREP_CAPTURE__
  if (!input) return
  const { payload, preset } = input
  const bounds = computePreviewBounds(payload)
  const width = window.innerWidth
  const height = window.innerHeight
  const renderer = new WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, logarithmicDepthBuffer: true })
  renderer.setSize(width, height)
  renderer.toneMapping = AgXToneMapping
  renderer.toneMappingExposure = PREVIEW_RENDER_SETTINGS.toneMappingExposure
  renderer.outputColorSpace = SRGBColorSpace
  document.body.appendChild(renderer.domElement)

  const scene = new Scene()
  scene.background = new Color(PREVIEW_RENDER_SETTINGS.background)
  const pmrem = new PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), PREVIEW_RENDER_SETTINGS.environmentBlur).texture
  scene.add(new AmbientLight(0xffffff, PREVIEW_RENDER_SETTINGS.ambientIntensity))
  const keyLight = PREVIEW_RENDER_SETTINGS.keyLight
  const key = new DirectionalLight(keyLight.color, keyLight.intensity)
  key.position.set(...keyLight.position)
  scene.add(key)
  const fillLight = PREVIEW_RENDER_SETTINGS.fillLight
  const fill = new DirectionalLight(fillLight.color, fillLight.intensity)
  fill.position.set(...fillLight.position)
  scene.add(fill)

  const root = new Group()
  root.position.set(...bounds.center)
  scene.add(root)
  for (const mesh of payload.meshes) {
    if (!mesh.vertices.length || !mesh.faces.length) continue
    const geometry = buildMeshGeometry(mesh, bounds.center)
    const resolved = semanticMaterialStyle(
      payload.materials
        ? payload.materials[mesh.material_id || '']
          ?? payload.materials[(mesh.material_id || '').toLowerCase()]
          ?? payload.materials[(mesh.material_id || '').toUpperCase()]
          ?? Object.entries(payload.materials).find(([id]) => id.toLowerCase() === (mesh.material_id || '').toLowerCase())?.[1]
        : null,
      preset === 'neutral',
    )
    const { transmission, ior, ...baseProperties } = resolved.properties
    const material = resolved.kind === 'physical'
      ? new MeshPhysicalMaterial({ ...baseProperties, transmission, ior, side: DoubleSide })
      : new MeshStandardMaterial({ ...baseProperties, side: DoubleSide })
    material.envMapIntensity = PREVIEW_RENDER_SETTINGS.environmentMapIntensity
    root.add(new Mesh(geometry, material))
  }

  const camera = new PerspectiveCamera(PREVIEW_CAMERA_FOV_DEGREES, width / height, 0.001, 100000)
  const cameraPreset = preset === 'front' ? 'front' : preset === 'side' ? 'right' : 'iso'
  const direction = viewDirectionForPreset(cameraPreset)
  const up = viewUpForPreset(cameraPreset)
  const distance = perspectiveDistanceForBounds(bounds, width, height)
  const directionVector = new Vector3(...direction).normalize()
  camera.up.set(...up)
  camera.position.set(...bounds.center).addScaledVector(directionVector, distance)
  camera.lookAt(...bounds.center)
  camera.near = Math.max(distance / 1000, 0.001)
  camera.far = Math.max(distance * 20, 100)
  camera.updateProjectionMatrix()
  renderer.render(scene, camera)
  window.__OPENBREP_CAPTURE_CAMERA__ = {
    preset,
    fov_degrees: PREVIEW_CAMERA_FOV_DEGREES,
    target: [...bounds.center],
    direction: directionVector.toArray(),
    distance,
    viewport: { width, height },
  }
  window.__OPENBREP_CAPTURE_READY__ = true
}

capture()
