import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// TAURI_ENV_TARGET_TRIPLE is set by Tauri CLI during `tauri build` and `tauri dev`.
// This lets frontend code detect the environment via import.meta.env.VITE_IS_TAURI.
const isTauri = !!process.env.TAURI_ENV_TARGET_TRIPLE

export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_IS_TAURI': JSON.stringify(isTauri),
  },
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-monaco': ['@monaco-editor/react'],
          'vendor-three': ['three', '@react-three/fiber', '@react-three/drei'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
