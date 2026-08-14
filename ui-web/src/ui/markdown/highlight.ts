/**
 * Syntax highlighting, lazily.
 *
 * Nothing about shiki is on the boot path: the core, the WASM regex engine,
 * the two themes and every grammar arrive as their own chunks, fetched the
 * first time a code block of that language is actually rendered. A transcript
 * with no code costs nothing.
 *
 * Both themes are highlighted at once (`themes: {light, dark}`), so shiki
 * writes the dark colors into `--shiki-dark-*` custom properties and the theme
 * switch is a CSS variable swap — no re-highlight, no flash. `styles/shiki.css`
 * is the other half of that contract.
 */

import type { HighlighterCore } from 'shiki/core'

/**
 * Grammars we ship, keyed by the infostring a fence can carry. Each entry is
 * its own dynamic import so the bundler can split it: a variable specifier
 * would defeat that and pull every grammar into one chunk.
 */
const GRAMMARS: Record<string, () => Promise<unknown>> = {
  bash: () => import('@shikijs/langs/bash'),
  c: () => import('@shikijs/langs/c'),
  cpp: () => import('@shikijs/langs/cpp'),
  csharp: () => import('@shikijs/langs/csharp'),
  css: () => import('@shikijs/langs/css'),
  diff: () => import('@shikijs/langs/diff'),
  docker: () => import('@shikijs/langs/docker'),
  elixir: () => import('@shikijs/langs/elixir'),
  go: () => import('@shikijs/langs/go'),
  graphql: () => import('@shikijs/langs/graphql'),
  groovy: () => import('@shikijs/langs/groovy'),
  haskell: () => import('@shikijs/langs/haskell'),
  html: () => import('@shikijs/langs/html'),
  ini: () => import('@shikijs/langs/ini'),
  java: () => import('@shikijs/langs/java'),
  javascript: () => import('@shikijs/langs/javascript'),
  json: () => import('@shikijs/langs/json'),
  jsx: () => import('@shikijs/langs/jsx'),
  kotlin: () => import('@shikijs/langs/kotlin'),
  lua: () => import('@shikijs/langs/lua'),
  makefile: () => import('@shikijs/langs/make'),
  markdown: () => import('@shikijs/langs/markdown'),
  perl: () => import('@shikijs/langs/perl'),
  php: () => import('@shikijs/langs/php'),
  powershell: () => import('@shikijs/langs/powershell'),
  python: () => import('@shikijs/langs/python'),
  r: () => import('@shikijs/langs/r'),
  ruby: () => import('@shikijs/langs/ruby'),
  rust: () => import('@shikijs/langs/rust'),
  scala: () => import('@shikijs/langs/scala'),
  scss: () => import('@shikijs/langs/scss'),
  shellscript: () => import('@shikijs/langs/shellscript'),
  sql: () => import('@shikijs/langs/sql'),
  svelte: () => import('@shikijs/langs/svelte'),
  swift: () => import('@shikijs/langs/swift'),
  toml: () => import('@shikijs/langs/toml'),
  tsx: () => import('@shikijs/langs/tsx'),
  typescript: () => import('@shikijs/langs/typescript'),
  vue: () => import('@shikijs/langs/vue'),
  xml: () => import('@shikijs/langs/xml'),
  yaml: () => import('@shikijs/langs/yaml'),
  zig: () => import('@shikijs/langs/zig'),
}

/** Fence infostrings people actually type → the grammar that handles them. */
const ALIASES: Record<string, string> = {
  'c++': 'cpp',
  'objective-c': 'c',
  cjs: 'javascript',
  cs: 'csharp',
  dockerfile: 'docker',
  ex: 'elixir',
  golang: 'go',
  hs: 'haskell',
  htm: 'html',
  js: 'javascript',
  jsonc: 'json',
  kt: 'kotlin',
  md: 'markdown',
  mjs: 'javascript',
  ps1: 'powershell',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  sh: 'shellscript',
  shell: 'shellscript',
  ts: 'typescript',
  yml: 'yaml',
  zsh: 'shellscript',
}

export function normalizeLanguage(raw: string | undefined): string | undefined {
  if (raw === undefined) return undefined

  const key = raw.trim().toLowerCase().split(/[\s:]/)[0] ?? ''

  if (key === '') return undefined

  const resolved = ALIASES[key] ?? key

  return resolved in GRAMMARS ? resolved : undefined
}

let highlighterPromise: Promise<HighlighterCore> | null = null
const loaded = new Set<string>()

async function highlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= (async () => {
    const [{ createHighlighterCore }, { createOnigurumaEngine }, light, dark] = await Promise.all([
      import('shiki/core'),
      import('shiki/engine/oniguruma'),
      import('@shikijs/themes/github-light'),
      import('@shikijs/themes/github-dark'),
    ])

    return createHighlighterCore({
      engine: createOnigurumaEngine(() => import('shiki/wasm')),
      langs: [],
      themes: [light.default, dark.default],
    })
  })()

  return highlighterPromise
}

/**
 * Highlight `code` to HTML, or return null when the language is unknown, the
 * grammar fails to load, or highlighting is not worth it. Callers render the
 * plain text in that case — a code block must never fail to display.
 */
export async function highlightToHtml(code: string, language: string): Promise<string | null> {
  const lang = normalizeLanguage(language)

  if (lang === undefined) return null

  try {
    const shiki = await highlighter()

    if (!loaded.has(lang)) {
      const load = GRAMMARS[lang]

      if (load === undefined) return null

      await shiki.loadLanguage((await load()) as never)
      loaded.add(lang)
    }

    return shiki.codeToHtml(code, {
      lang,
      themes: { dark: 'github-dark', light: 'github-light' },
      defaultColor: false,
    })
  } catch {
    return null
  }
}
