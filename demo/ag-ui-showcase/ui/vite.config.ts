import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/agent': {
        target: 'http://localhost:7000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:7000',
        changeOrigin: true,
      },
    },
  },
});
