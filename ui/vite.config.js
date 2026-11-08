import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` proxies the API to a server started with `python -m papyrus` (PAPYRUS_PORT=8080).
const backend = process.env.PAPYRUS_BACKEND || 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: 'dist', assetsDir: 'immutable', sourcemap: false },
  server: { proxy: { '/api': { target: backend, changeOrigin: false }, '/health': { target: backend } } },
});
