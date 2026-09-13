/**
 * A file's kind at a glance: a coloured sheet carrying a short mark, or a
 * folder.
 *
 * Adapted from the reference client's file-type icon set — a solid
 * category-coloured sheet with a white mark for the traditional kinds, and the
 * language's own colour for code — at the size a tab chip or a tree row can
 * afford. The colours are the kinds' own and stay fixed across themes, as the
 * reference keeps its artwork's palette. Decorative: the name beside the glyph
 * does the talking, so it carries no label.
 */

import type { ReactNode } from 'react'

export interface FileSheet {
  /** The sheet's fill. */
  fill: string
  /** The mark's colour; white unless the fill is too pale for it. */
  ink?: string
  /** Up to three characters, or a drawn mark for the kinds that have one. */
  label: string | 'image' | 'text'
}

const WHITE = '#ffffff'

const SHEETS = {
  c: { fill: '#5c6370', label: 'C' },
  cfg: { fill: '#6b7280', label: 'CFG' },
  cpp: { fill: '#f34b7d', label: 'C++' },
  csharp: { fill: '#178600', label: 'C#' },
  css: { fill: '#563d7c', label: 'CSS' },
  csv: { fill: '#1d6f42', label: 'CSV' },
  doc: { fill: '#2b579a', label: 'DOC' },
  docker: { fill: '#2496ed', label: 'DKR' },
  env: { fill: '#ecd53f', ink: '#3b3b3b', label: 'ENV' },
  git: { fill: '#f05032', label: 'GIT' },
  go: { fill: '#00add8', label: 'GO' },
  html: { fill: '#e34c26', label: '<>' },
  image: { fill: '#7c5cbf', label: 'image' },
  java: { fill: '#b07219', label: 'JAV' },
  js: { fill: '#f0db4f', ink: '#323330', label: 'JS' },
  json: { fill: '#e8a33d', label: '{ }' },
  jsx: { fill: '#f0db4f', ink: '#323330', label: 'JSX' },
  kotlin: { fill: '#7f52ff', label: 'KT' },
  license: { fill: '#8a8f98', label: 'LIC' },
  lock: { fill: '#6b7280', label: 'LCK' },
  lua: { fill: '#2c2d72', label: 'LUA' },
  make: { fill: '#6b7280', label: 'MK' },
  markdown: { fill: '#3b7dd8', label: 'MD' },
  pdf: { fill: '#d32f2f', label: 'PDF' },
  php: { fill: '#4f5d95', label: 'PHP' },
  ppt: { fill: '#d24726', label: 'PPT' },
  python: { fill: '#3572a5', label: 'PY' },
  ruby: { fill: '#701516', label: 'RB' },
  rust: { fill: '#b7410e', label: 'RS' },
  shell: { fill: '#4e9a3f', label: '$_' },
  sql: { fill: '#e38c00', label: 'SQL' },
  svelte: { fill: '#ff3e00', label: 'SV' },
  swift: { fill: '#f05138', label: 'SW' },
  text: { fill: '#9aa0a6', label: 'text' },
  toml: { fill: '#9c4221', label: 'TML' },
  ts: { fill: '#3178c6', label: 'TS' },
  tsx: { fill: '#3178c6', label: 'TSX' },
  vue: { fill: '#41b883', label: 'VUE' },
  xls: { fill: '#1d6f42', label: 'XLS' },
  yaml: { fill: '#c8312b', label: 'YML' },
  zip: { fill: '#6b7280', label: 'ZIP' },
} satisfies Record<string, FileSheet>

type SheetKey = keyof typeof SHEETS

const BY_EXTENSION: Record<string, SheetKey> = {
  '7z': 'zip',
  avif: 'image',
  bash: 'shell',
  bmp: 'image',
  c: 'c',
  cc: 'cpp',
  cfg: 'cfg',
  cjs: 'js',
  conf: 'cfg',
  cpp: 'cpp',
  cs: 'csharp',
  css: 'css',
  csv: 'csv',
  cts: 'ts',
  cxx: 'cpp',
  doc: 'doc',
  docx: 'doc',
  env: 'env',
  fish: 'shell',
  gif: 'image',
  go: 'go',
  gz: 'zip',
  h: 'c',
  hh: 'cpp',
  hpp: 'cpp',
  htm: 'html',
  html: 'html',
  ico: 'image',
  ini: 'cfg',
  java: 'java',
  jpeg: 'image',
  jpg: 'image',
  js: 'js',
  json: 'json',
  json5: 'json',
  jsonc: 'json',
  jsx: 'jsx',
  kt: 'kotlin',
  kts: 'kotlin',
  less: 'css',
  lock: 'lock',
  log: 'text',
  lua: 'lua',
  markdown: 'markdown',
  md: 'markdown',
  mdx: 'markdown',
  mjs: 'js',
  mts: 'ts',
  pdf: 'pdf',
  php: 'php',
  png: 'image',
  ppt: 'ppt',
  pptx: 'ppt',
  py: 'python',
  pyi: 'python',
  rb: 'ruby',
  rs: 'rust',
  sass: 'css',
  scss: 'css',
  sh: 'shell',
  sql: 'sql',
  svelte: 'svelte',
  svg: 'image',
  swift: 'swift',
  tar: 'zip',
  tgz: 'zip',
  toml: 'toml',
  ts: 'ts',
  tsv: 'csv',
  tsx: 'tsx',
  txt: 'text',
  vue: 'vue',
  webp: 'image',
  xls: 'xls',
  xlsx: 'xls',
  yaml: 'yaml',
  yml: 'yaml',
  zip: 'zip',
  zsh: 'shell',
}

/** Names that mean something whole, before any extension is read. */
const BY_NAME: Record<string, SheetKey> = {
  '.env': 'env',
  '.gitattributes': 'git',
  '.gitignore': 'git',
  '.gitmodules': 'git',
  containerfile: 'docker',
  dockerfile: 'docker',
  gnumakefile: 'make',
  license: 'license',
  makefile: 'make',
  readme: 'markdown',
}

/** The sheet a path draws, or `null` for a kind this set does not know. */
export function classifyFile(path: string): FileSheet | null {
  const name = (path.split(/[/\\]/).filter(Boolean).at(-1) ?? path).toLowerCase()
  const byName = BY_NAME[name]

  if (byName !== undefined) return SHEETS[byName]

  // `.env.local` and `Dockerfile.dev` keep their family; an extension is
  // otherwise what follows the last dot.
  if (name.startsWith('.env')) return SHEETS.env
  if (name.startsWith('dockerfile')) return SHEETS.docker

  const dot = name.lastIndexOf('.')

  if (dot <= 0) return null

  const key = BY_EXTENSION[name.slice(dot + 1)]

  return key === undefined ? null : SHEETS[key]
}

export interface FileTypeIconProps {
  className?: string
  /** A folder, or a file named by `path`. */
  kind?: 'file' | 'folder'
  path?: string
  size?: number
}

function Mark({ sheet }: { sheet: FileSheet }): ReactNode {
  const ink = sheet.ink ?? WHITE

  if (sheet.label === 'image') {
    return (
      <>
        <circle cx="6" cy="6.2" fill={ink} r="1.3" />
        <path d="M3.6 12.2 6.6 9l2 2 1.6-1.6 2.3 2.8z" fill={ink} />
      </>
    )
  }

  if (sheet.label === 'text') {
    return (
      <path
        d="M4.5 5.5h7M4.5 8h7M4.5 10.5h4.5"
        stroke={ink}
        strokeLinecap="round"
        strokeWidth="1.2"
      />
    )
  }

  return (
    <text
      dominantBaseline="central"
      fill={ink}
      fontFamily="system-ui, -apple-system, 'Segoe UI', sans-serif"
      fontSize={sheet.label.length > 2 ? 5.4 : 6.6}
      fontWeight="700"
      textAnchor="middle"
      x="8"
      y="8.2"
    >
      {sheet.label}
    </text>
  )
}

export function FileTypeIcon({ className, kind = 'file', path = '', size = 16 }: FileTypeIconProps) {
  const sheet = kind === 'folder' ? null : classifyFile(path)

  return (
    <svg
      aria-hidden="true"
      className={className}
      data-file-type={kind === 'folder' ? 'folder' : sheet === null ? 'generic' : sheet.label}
      fill="none"
      focusable="false"
      height={size}
      viewBox="0 0 16 16"
      width={size}
    >
      {kind === 'folder' ? (
        <>
          <path
            d="M1.5 4.5A1.5 1.5 0 0 1 3 3h3.2l1.6 1.5H13a1.5 1.5 0 0 1 1.5 1.5v6.5A1.5 1.5 0 0 1 13 14H3a1.5 1.5 0 0 1-1.5-1.5z"
            fill="#e9a23b"
          />
          <path d="M1.5 6.5h13" stroke="rgba(255, 255, 255, 0.45)" />
        </>
      ) : sheet === null ? (
        <>
          {/* The generic sheet: a page with a folded corner, on grey. */}
          <path
            d="M3.5 1.5h6l3.5 3.5v8a1.5 1.5 0 0 1-1.5 1.5H3.5A1.5 1.5 0 0 1 2 13V3a1.5 1.5 0 0 1 1.5-1.5z"
            fill="#c7cbd1"
          />
          <path d="M9.5 1.5V5h3.5z" fill="#eef0f2" />
        </>
      ) : (
        <>
          <rect fill={sheet.fill} height="13" rx="3" width="13" x="1.5" y="1.5" />
          <Mark sheet={sheet} />
        </>
      )}
    </svg>
  )
}
