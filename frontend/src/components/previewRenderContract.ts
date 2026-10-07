/** Stable renderer settings shared by the live viewport and evidence capture. */
export const PREVIEW_RENDER_SETTINGS = {
  toneMappingExposure: 0.9,
  background: '#0a0e14',
  environmentBlur: 0.04,
  ambientIntensity: 0.08,
  keyLight: { position: [3, -4, 5] as [number, number, number], intensity: 1.1, color: '#ffffff' },
  fillLight: { position: [-4, 2, 3] as [number, number, number], intensity: 0.5, color: '#9fb4cc' },
  environmentMapIntensity: 0.75,
} as const
