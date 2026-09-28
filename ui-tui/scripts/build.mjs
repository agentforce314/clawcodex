#!/usr/bin/env node
// Bundles src/entry.tsx into a single self-contained dist/entry.js.
// No runtime node_modules needed.
import { build } from 'esbuild'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
// `--outfile=<path>` lets a test build a throwaway bundle without touching dist/.
const outArg = process.argv.find(a => a.startsWith('--outfile='))
const out = outArg ? resolve(outArg.slice('--outfile='.length)) : resolve(root, 'dist/entry.js')

// `react-devtools-core` is only imported when DEV=true at runtime (Ink dev
// mode). Stub it out so the bundle doesn't carry the dep.
const stubDevtools = {
  name: 'stub-react-devtools-core',
  setup(b) {
    b.onResolve({ filter: /^react-devtools-core$/ }, args => ({
      path: args.path,
      namespace: 'stub-devtools'
    }))
    b.onLoad({ filter: /.*/, namespace: 'stub-devtools' }, () => ({
      contents: 'export default { initialize() {}, connectToDevTools() {} }',
      loader: 'js'
    }))
  }
}

await build({
  entryPoints: [resolve(root, 'src/entry.tsx')],
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node20',
  outfile: out,
  jsx: 'automatic',
  jsxImportSource: 'react',
  // React picks its build from `process.env.NODE_ENV` at require time, and the
  // launcher runs `node dist/entry.js` with NODE_ENV unset — so without this
  // every user ran the development React. Its performance tracks call
  // `performance.measure()` as components re-render, and Node keeps each entry
  // in its global timeline forever, so a long agent-team session's heap grew
  // by gigabytes. Folding NODE_ENV here (not setting it at runtime, which
  // would leak into the agent-server's child commands) drops the dev build.
  define: { 'process.env.NODE_ENV': '"production"' },
  // Skip the prebuilt @clawcodex/ink bundle — esbuild's __esm helper doesn't
  // await nested async init, which breaks lazy-initialized exports like
  // `render`. Bundling from source sidesteps that.
  alias: { '@clawcodex/ink': resolve(root, 'packages/clawcodex-ink/src/entry-exports.ts') },
  plugins: [stubDevtools],
  // Some transitive deps use CommonJS `require(...)` at runtime. ESM bundles
  // don't get a `require` binding automatically, so we inject one.
  banner: {
    js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);"
  },
  logLevel: 'info'
})

// esbuild preserves the shebang from src/entry.tsx into the bundle, but Nix's
// patchShebangs phase mangles `/usr/bin/env -S node --foo --bar` (it strips
// the `node` token, leaving a broken interpreter). The clawcodex_cli launcher
// always invokes this file as `node dist/entry.js` anyway, so the shebang is
// redundant — strip it.
const body = readFileSync(out, 'utf8')
if (body.startsWith('#!')) {
  writeFileSync(out, body.slice(body.indexOf('\n') + 1))
}

console.log(`built ${out}`)
