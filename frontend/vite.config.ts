import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const target = process.env.QWENTTS_BACKEND || 'https://127.0.0.1:8443';

const obf = process.env.OBF === '1';

export default defineConfig({
  plugins: [react()],
  // 混淆版：构建时去掉 console / debugger，减小体积并少暴露调试信息
  esbuild: obf ? { drop: ['console', 'debugger'] } : undefined,
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target, changeOrigin: true, secure: false },
      '/v1': { target, changeOrigin: true, secure: false },
      '/ws': { target: target.replace(/^http/, 'ws'), ws: true, secure: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
