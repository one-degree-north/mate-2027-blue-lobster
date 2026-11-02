import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// The hub's CORS-style origin allowlist expects the dev server on this exact port.
export default defineConfig({
  plugins: [svelte()],
  server: { port: 5173, strictPort: true },
})
