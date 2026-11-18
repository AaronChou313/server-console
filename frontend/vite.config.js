import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: Object.fromEntries(['/api', '/ws', '/dsh'].map(path => [
      path, {target: 'http://127.0.0.1:9786', ws: true},
    ])),
  },
})
