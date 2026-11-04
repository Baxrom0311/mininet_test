import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Yig'ilgan UI lab/web-react ga tushadi — FastAPI uni "/" da beradi (Node.js foydalanuvchiga shart emas).
// Ishlab chiqish: `npm run dev` (5173) — /api va /ws backendga (8080) proksi qilinadi.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  build: { outDir: '../web-react', emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/ws': { target: 'ws://127.0.0.1:8080', ws: true },
    },
  },
})
