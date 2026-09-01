/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/ask': { target: 'http://localhost:8000' },
      '/ask-stream': { target: 'http://localhost:8000' },
      '/local-models': { target: 'http://localhost:8000' },
      '/corpus-stats': { target: 'http://localhost:8000' },
      '/pdf': { target: 'http://localhost:8000' },
      '/diff-followup': { target: 'http://localhost:8000' },
      '/cross-check-regulation': { target: 'http://localhost:8000' },
      '/report-answer': { target: 'http://localhost:8000' },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
  },
})
