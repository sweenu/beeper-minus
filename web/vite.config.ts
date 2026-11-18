import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  // `npm run dev` talks to a running beeper-minus backend.
  server: { proxy: { '/api': 'http://127.0.0.1:8099' } },
})
