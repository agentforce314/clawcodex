/**
 * The text preview's reading state, one bucket per open file tab.
 *
 * It lives here rather than in the body so that switching tabs unmounts a
 * preview without dropping what it read: a tab comes back to the page it was
 * on, at the offset the reader left it, wrapped the way they set it.
 *
 * Two rules about versions, both enforced in `applyPage` so they are testable
 * without a socket:
 *
 * - a **first** page from a newer version replaces everything read before it —
 *   the reader is now looking at the new file, whole;
 * - a **later** page from a newer version is not merged. Two versions stitched
 *   together would read as one file that never existed, so the walk restarts
 *   from page one instead.
 */

import { map } from 'nanostores'

import type { FileBytes, FilePage, WorkspaceFileFailure } from '../gateway/protocol.ts'
import { readWorkspaceBytes, readWorkspaceFile } from '../state/actions.ts'

/** One page's lines, as the backend counted them. */
export interface TextPage {
  /**
   * Zero means "past the end of the file"; one with an empty `text` means one
   * empty line. The count is the backend's, never derived from the text.
   */
  lines: number
  text: string
}

/**
 * How a file is drawn: rendered Markdown, highlighted code with a line gutter,
 * the bare text, or — for the kinds a page of lines cannot feed — an HTML
 * document in its own frame, an image, a PDF. The path picks the default; the
 * reader can switch to plain text, and back, from the header.
 */
export type PreviewViewer = 'code' | 'html' | 'image' | 'markdown' | 'pdf' | 'text'

/** The viewers that read the file whole, as bytes, rather than a page at a time. */
const BYTE_VIEWERS: ReadonlySet<PreviewViewer> = new Set<PreviewViewer>(['html', 'image', 'pdf'])

export function isByteViewer(viewer: PreviewViewer): boolean {
  return BYTE_VIEWERS.has(viewer)
}

const HTML_EXTENSIONS = new Set(['htm', 'html'])
const IMAGE_EXTENSIONS = new Set(['avif', 'bmp', 'gif', 'ico', 'jpeg', 'jpg', 'png', 'svg', 'webp'])
const PDF_EXTENSIONS = new Set(['pdf'])

/** Extensions the code viewer claims; anything else opens as plain text. */
const CODE_EXTENSIONS = new Set([
  'bash', 'bat', 'c', 'cc', 'cfg', 'cjs', 'clj', 'cmd', 'conf', 'cpp', 'cs', 'css', 'cts', 'cxx',
  'dart', 'diff', 'dockerfile', 'env', 'erl', 'ex', 'exs', 'fish', 'go', 'graphql', 'h', 'hcl',
  'hh', 'hpp', 'hs', 'htm', 'html', 'ini', 'java', 'js', 'json', 'json5', 'jsonc', 'jsx', 'kt',
  'kts', 'less', 'lua', 'makefile', 'mjs', 'ml', 'mts', 'nim', 'patch', 'php', 'pl', 'proto',
  'ps1', 'py', 'pyi', 'r', 'rb', 'rs', 'sass', 'scala', 'scss', 'sh', 'sql', 'svelte', 'svg',
  'swift', 'tf', 'toml', 'ts', 'tsx', 'vb', 'vue', 'xml', 'yaml', 'yml', 'zig', 'zsh',
])

const MARKDOWN_EXTENSIONS = new Set(['markdown', 'md', 'mdx'])

/**
 * The extension a viewer is chosen by — the part after the last dot, or the
 * whole lowercased name for `Dockerfile` and `Makefile`, which are their own
 * kind without one.
 */
export function fileExtension(path: string): string {
  const name = (path.split(/[/\\]/).filter(Boolean).at(-1) ?? path).toLowerCase()
  const dot = name.lastIndexOf('.')

  return dot > 0 ? name.slice(dot + 1) : name
}

/** The viewer a path opens in. */
export function defaultViewer(path: string): PreviewViewer {
  const extension = fileExtension(path)

  if (HTML_EXTENSIONS.has(extension)) return 'html'
  if (IMAGE_EXTENSIONS.has(extension)) return 'image'
  if (PDF_EXTENSIONS.has(extension)) return 'pdf'
  if (MARKDOWN_EXTENSIONS.has(extension)) return 'markdown'

  return CODE_EXTENSIONS.has(extension) ? 'code' : 'text'
}

/**
 * The viewers a path can be read with: its own, then the text ones that still
 * make sense — an HTML document or an SVG is also its source, so code and
 * plain text stand beside the frame; a raster image or a PDF is only itself.
 * A path with one viewer gets no chooser in the header.
 */
export function viewerChoices(path: string): PreviewViewer[] {
  const own = defaultViewer(path)

  if (own === 'html') return ['html', 'code', 'text']
  if (own === 'image') return fileExtension(path) === 'svg' ? ['image', 'code', 'text'] : ['image']
  if (own === 'pdf') return ['pdf']

  return own === 'text' ? ['text'] : [own, 'text']
}

/** The bytes a base64 window carries. Malformed base64 throws. */
export function decodeBase64(data: string): Uint8Array {
  const binary = atob(data)
  const bytes = new Uint8Array(binary.length)

  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)

  return bytes
}

export interface TextTabState {
  /** The navigation revision this bucket has already jumped for. */
  answered: number
  /** The whole file, for the byte viewers; absent until one of them asked. */
  bytes?: Uint8Array
  eof: boolean
  failure?: WorkspaceFileFailure
  loading: boolean
  /** The path the backend resolved, which is what the header shows. */
  path: string
  /** Keyed by the 1-based line the page starts at. */
  pages: Record<number, TextPage>
  /**
   * When the *first* page of the loaded version landed — the clock the change
   * notice reads.
   *
   * Not the newest page: paging forward would then refresh it and clear a
   * notice through a gesture that is not Reload, leaving the reader with a
   * stale top and nothing saying so. A later page comes from the same version
   * by construction — a page from a newer one restarts the walk — so the first
   * page's clock is the right one for the whole body.
   */
  readAt: number
  scrollTop: number
  /** The file version every loaded page came from. */
  version: string
  viewer: PreviewViewer
  wrap: boolean
}

export const $textTabs = map<Record<string, TextTabState>>({})

/** Wrap is on until the reader turns it off: a preview column is narrow. */
export function emptyTextTab(path: string): TextTabState {
  return {
    answered: 0,
    eof: false,
    loading: false,
    pages: {},
    path,
    readAt: 0,
    scrollTop: 0,
    version: '',
    viewer: defaultViewer(path),
    wrap: true,
  }
}

/**
 * A read's generation, per tab. A reload bumps it, and a page that settles from
 * an older generation writes nothing — which is what keeps a slow first page
 * from landing on top of the reload that replaced it.
 *
 * The ticker is global and never resets, so a number is used once for the life
 * of the page: forgetting a tab and opening it again cannot hand the new read
 * the generation an old one is still holding.
 */
const generations = new Map<string, number>()
let ticker = 0

/** Fold one settled page into a bucket. Pure: the whole version rule is here. */
export function applyPage(
  state: TextTabState,
  offset: number,
  page: FilePage,
): { restart: boolean; state: TextTabState } {
  const moved = state.version !== '' && state.version !== page.version

  if (moved && offset !== 1) {
    // The file changed under the walk. Drop what was read and start again from
    // the first page rather than stitch two versions into one file.
    return {
      restart: true,
      state: { ...state, eof: false, failure: undefined, loading: false, pages: {}, version: '' },
    }
  }

  // A first page replaces everything: it is either the start of the walk or a
  // reload, and in both cases what came before it is gone — the bytes too,
  // when the version moved, so no viewer shows an older file than another.
  const base = offset === 1 ? {} : state.pages

  return {
    restart: false,
    state: {
      ...state,
      ...(moved ? { bytes: undefined } : {}),
      eof: page.eof,
      failure: undefined,
      loading: false,
      path: page.absolute_path,
      pages: { ...base, [offset]: { lines: page.lines, text: page.text } },
      readAt: offset === 1 ? Date.now() : state.readAt,
      version: page.version,
    },
  }
}

/** The loaded pages in file order, each with the line it starts at. */
export function loadedPages(
  pages: Record<number, TextPage>,
): { lines: number; offset: number; text: string }[] {
  return Object.entries(pages)
    .map(([offset, page]) => ({ ...page, offset: Number(offset) }))
    .sort((left, right) => left.offset - right.offset)
}

/** A page's lines. `lines: 0` is no lines, not one empty one. */
export function linesOf(page: TextPage): string[] {
  return page.lines === 0 ? [] : page.text.split('\n')
}

/** The last line the loaded pages reach; 0 before the first page. */
export function lastLineLoaded(pages: { lines: number; offset: number }[]): number {
  const last = pages.at(-1)

  return last === undefined ? 0 : last.offset + last.lines - 1
}

function patch(tabId: string, next: Partial<TextTabState>): void {
  const current = $textTabs.get()[tabId]

  if (current === undefined) return

  $textTabs.setKey(tabId, { ...current, ...next })
}

/**
 * Read one page into a tab's bucket, seeding the bucket on the first call.
 *
 * Failures are shown, not thrown: the pages already read stay on screen with
 * one line at the end saying why the next one is not there.
 */
export async function loadPage(tabId: string, path: string, offset: number): Promise<void> {
  const existing = $textTabs.get()[tabId]

  if (existing === undefined) $textTabs.setKey(tabId, { ...emptyTextTab(path), loading: true })
  else if (existing.loading) return
  else patch(tabId, { failure: undefined, loading: true })

  ticker += 1

  const generation = ticker

  generations.set(tabId, generation)

  const result = await readWorkspaceFile(path, offset)

  if (generations.get(tabId) !== generation) return

  const current = $textTabs.get()[tabId]

  if (current === undefined) return

  if (!result.ok) {
    $textTabs.setKey(tabId, { ...current, failure: result.error, loading: false })

    return
  }

  const applied = applyPage(current, offset, result)

  $textTabs.setKey(tabId, applied.state)

  if (applied.restart) await loadPage(tabId, path, 1)
}

/**
 * Read the file again from its first page, keeping the reader where they are.
 *
 * Deliberately not "re-fetch every page that was loaded": that is several
 * sequential reads before anything can be shown, and after an edit the loaded
 * range no longer describes the same lines anyway. The scroll offset is kept,
 * which can land the reader in empty space when they were deep in a long file.
 */
export async function reloadPages(tabId: string, path: string): Promise<void> {
  const current = $textTabs.get()[tabId]

  ticker += 1
  generations.set(tabId, ticker)
  $textTabs.setKey(tabId, {
    ...(current ?? emptyTextTab(path)),
    bytes: undefined,
    eof: false,
    failure: undefined,
    loading: false,
    pages: {},
    version: '',
  })

  await loadPage(tabId, path, 1)
}

/**
 * Read a file whole, for the viewers that need it so. Seeds the bucket on the
 * first call, as `loadPage` does, and shares its failure line: the pages and
 * the bytes are two readings of one file, kept in one bucket.
 */
export async function loadBytes(tabId: string, path: string): Promise<void> {
  const existing = $textTabs.get()[tabId]

  if (existing === undefined) $textTabs.setKey(tabId, { ...emptyTextTab(path), loading: true })
  else if (existing.loading) return
  else patch(tabId, { failure: undefined, loading: true })

  ticker += 1

  const generation = ticker

  generations.set(tabId, generation)

  const result = await readWorkspaceBytes(path)

  if (generations.get(tabId) !== generation) return

  const current = $textTabs.get()[tabId]

  if (current === undefined) return

  if (!result.ok) {
    $textTabs.setKey(tabId, { ...current, failure: result.error, loading: false })

    return
  }

  $textTabs.setKey(tabId, applyBytes(current, result))
}

/** Fold a settled whole-file read into a bucket. Pure, like `applyPage`. */
export function applyBytes(state: TextTabState, file: FileBytes): TextTabState {
  const moved = state.version !== '' && state.version !== file.version

  return {
    ...state,
    bytes: decodeBase64(file.data),
    eof: true,
    failure: undefined,
    loading: false,
    // Pages read from an older version would show another file than the
    // frame does; a reload of that viewer reads them again.
    ...(moved ? { pages: {} } : {}),
    path: file.absolute_path,
    readAt: Date.now(),
    version: file.version,
  }
}

/** Read the whole file again, dropping the bytes and the pages alike. */
export async function reloadBytes(tabId: string, path: string): Promise<void> {
  const current = $textTabs.get()[tabId]

  ticker += 1
  generations.set(tabId, ticker)
  $textTabs.setKey(tabId, {
    ...(current ?? emptyTextTab(path)),
    bytes: undefined,
    eof: false,
    failure: undefined,
    loading: false,
    pages: {},
    version: '',
  })

  await loadBytes(tabId, path)
}

export function setScroll(tabId: string, scrollTop: number): void {
  patch(tabId, { scrollTop })
}

export function toggleWrap(tabId: string): void {
  const current = $textTabs.get()[tabId]

  if (current !== undefined) patch(tabId, { wrap: !current.wrap })
}

export function setViewer(tabId: string, viewer: PreviewViewer): void {
  patch(tabId, { viewer })
}

/** Record that a navigation has been jumped for, so a remount does not re-jump. */
export function markAnswered(tabId: string, revision: number): void {
  patch(tabId, { answered: revision })
}

/** Drop a closed tab's bucket; a reopened tab reads its first page again. */
export function forgetTextTab(tabId: string): void {
  const next = { ...$textTabs.get() }

  delete next[tabId]
  generations.delete(tabId)
  $textTabs.set(next)
}

export function resetTextTabs(): void {
  generations.clear()
  $textTabs.set({})
}
