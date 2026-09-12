import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // In development the app calls its own origin and this forwards to Django, so CORS
    // simply does not exist locally. It becomes a deploy-time concern only -- chasing
    // preflight errors while also building UI is a bad use of a short week.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
