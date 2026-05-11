import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const proxy = {
  '/agent': { target: 'http://localhost:7000', changeOrigin: true },
  '/health': { target: 'http://localhost:7000', changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 5173, proxy },
});
