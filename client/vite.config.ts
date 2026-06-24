import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  cacheDir: '/tmp/vite-cache',
  server: {
    allowedHosts: ['kronecker.dzne.ds', 'hopper.dzne.de', 'hopper.dzne.ds', 'germain.dzne.de', 'gateway', 'client'],
    proxy: {
      '/api/app': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://api-service:8000',
        changeOrigin: true,
      },
    },
  },
})
