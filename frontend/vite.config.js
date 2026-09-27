import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  cacheDir: './.vite-cache-new',
  plugins: [react()],
  build: {
    // 청크를 라이브러리별로 분리 → 앱 코드 변경 시 대형 라이브러리 재다운로드 방지
    rollupOptions: {
      output: {
        // Vite 8 (rolldown) requires manualChunks as a function
        manualChunks(id) {
          if (id.includes('node_modules/recharts') || id.includes('node_modules/d3') || id.includes('node_modules/victory')) {
            return 'vendor-recharts';
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'vendor-lucide';
          }
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/')) {
            return 'vendor-react';
          }
        },
      },
    },
    // 청크 경고 임계값을 800KB로 완화 (모노리식 SPA 특성)
    chunkSizeWarningLimit: 800,
  },
  preview: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/hs':  { target: 'http://localhost:8000', changeOrigin: true },
      '/semiconductor-lab': { target: 'http://localhost:8000', changeOrigin: true },
      // 2026-09-27: Stock LLM(Brian_RAG :8888)은 백엔드(:8000)의 /llm 프록시가 관리자 로그인을 검사한 뒤 중계한다(routes/llm_proxy.py)
      '/llm': { target: 'http://localhost:8000', changeOrigin: true },
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
      },
      '/llm': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
