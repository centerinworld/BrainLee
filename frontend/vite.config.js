import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  preview: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/hs':  { target: 'http://localhost:8000', changeOrigin: true },
      '/semiconductor-lab': { target: 'http://localhost:8000', changeOrigin: true },
    }
  },
  server: {
    host: true,
    port: 5173,
    allowedHosts: ['stock.leanguy.cloud'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/hs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/semiconductor-lab': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
