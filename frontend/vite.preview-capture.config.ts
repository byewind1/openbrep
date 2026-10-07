import { defineConfig } from 'vite'

export default defineConfig({
  configFile: false,
  publicDir: false,
  build: {
    outDir: 'dist/capture',
    emptyOutDir: true,
    lib: {
      entry: 'src/preview_capture_entry.ts',
      name: 'OpenBrepPreviewCapture',
      formats: ['iife'],
      fileName: () => 'preview_capture.js',
    },
    rollupOptions: {
      output: { inlineDynamicImports: true },
    },
  },
})
