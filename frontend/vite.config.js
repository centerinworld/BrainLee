import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/')) return 'vendor-react';
          if (id.includes('node_modules/recharts')) return 'vendor-recharts';
          if (id.includes('node_modules/lucide-react')) return 'vendor-lucide';
        },
      },
    },
    chunkSizeWarningLimit: 600,
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
