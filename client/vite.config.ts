import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  cacheDir: '/tmp/vite-cache',
  build: {
    // NiiVue is intentionally isolated as one lazy viewer chunk. Its exact size
    // is enforced separately by scripts/checkBundleSize.mjs.
    chunkSizeWarningLimit: 1_600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/@niivue/')) return 'niivue'
          return undefined
        },
      },
    },
  },
  server: {
    allowedHosts: ['kronecker.dzne.ds', 'hopper.dzne.de', 'hopper.dzne.ds', 'germain.dzne.de'],
    proxy: {
      '/api/app': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
