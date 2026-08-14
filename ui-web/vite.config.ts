import react from '@vitejs/plugin-react'
// `vitest/config` re-exports vite's defineConfig with the `test` block typed,
// so one file configures both the build and the test run.
import { defineConfig } from 'vitest/config'

/**
 * The built app is served by the Python backend (`clawcodex web`), which
 * mounts `dist/` under the same origin as `/api/*` — so assets are referenced
 * relatively and the client never needs to know an absolute base path.
 *
 * `dev` mode targets a backend already running on 127.0.0.1: start
 * `clawcodex serve --port 8317 --token dev` and Vite proxies `/api` to it, so
 * the browser client keeps talking to one origin in both modes.
 */
const DEV_BACKEND = process.env.CLAWCODEX_WEB_DEV_BACKEND ?? 'http://127.0.0.1:8317'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: DEV_BACKEND, changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Off by default: `dist/` ships inside the Python package, and the maps
    // more than double it (12MB against 5MB) for a payload only a developer
    // with devtools open ever fetches. `CLAWCODEX_WEB_SOURCEMAP=1` turns them
    // back on for a debugging build.
    sourcemap: process.env.CLAWCODEX_WEB_SOURCEMAP === '1',
    rollupOptions: {
      output: {
        // Heavy render families that only change on dependency bumps ride
        // their own chunk, so editing shell code leaves the cached vendor
        // chunk alone. Shiki grammars stay unassigned: each keeps its own
        // on-demand chunk and is fetched only when a code block needs it.
        manualChunks(id: string): string | undefined {
          if (!id.includes('/node_modules/')) return undefined
          if (id.includes('/node_modules/katex/')) return 'katex'
          if (id.includes('/node_modules/@shikijs/langs/')) return undefined
          if (id.includes('/node_modules/shiki/') || id.includes('/node_modules/@shikijs/')) {
            return 'shiki'
          }
          return 'vendor'
        },
      },
    },
  },
  test: {
    name: 'web',
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    testTimeout: 30_000,
  },
})
