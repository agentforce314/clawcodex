/**
 * One file, read a page at a time.
 *
 * The body draws what the tab's bucket holds and asks for the next page when
 * the reader reaches the end of it. Everything it keeps — pages, scroll offset,
 * wrap, the viewer, the navigation it has already answered — lives in the
 * store, so switching tabs unmounts this component without losing the reader's
 * place.
 *
 * A file is drawn by the viewer its path picks: Markdown rendered as prose,
 * code highlighted behind a line gutter, an HTML document in its own frame,
 * an image at its own size, a PDF through the browser's viewer, anything
 * else as bare text. The header offers the text viewers beside a file's own
 * where they still make sense, and back again. The text viewers read a page
 * at a time; the frame, the image and the PDF read the file whole.
 *
 * A changed file is *announced*, not applied: reloading under a reader loses
 * their place, and a file the agent is writing changes repeatedly. The bar
 * waits for a click.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $transcript, $workspace } from '../state/store.ts'
import { ChevronDownIcon, RefreshIcon, WrapIcon } from '../ui/icons.tsx'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import { CodeBlock } from '../ui/primitives/CodeBlock.tsx'
import { Menu } from '../ui/primitives/Menu.tsx'
import { HtmlPreview, ImagePreview, PdfPreview } from './BytePreviews.tsx'
import { changedSince } from './changed.ts'
import { fileFailureLine } from './failure-line.ts'
import { $navigation } from './store.ts'
import {
  $textTabs,
  defaultViewer,
  fileExtension,
  isByteViewer,
  lastLineLoaded,
  linesOf,
  loadBytes,
  loadedPages,
  loadPage,
  markAnswered,
  reloadBytes,
  reloadPages,
  setScroll,
  setViewer,
  toggleWrap,
  viewerChoices,
  type PreviewViewer,
} from './text-store.ts'
import css from './TextPreview.module.css'

export interface TextPreviewProps {
  path: string
  tabId: string
}

/** Pages a jump-to-line may load on its own before it gives up and stops. */
const WALK_PAGE_LIMIT = 5

const VIEWER_LABEL: Record<PreviewViewer, string> = {
  code: 'Code',
  html: 'HTML',
  image: 'Image',
  markdown: 'Markdown',
  pdf: 'PDF',
  text: 'Plain text',
}

/**
 * Put one line at the top of the body. A line not loaded leaves it alone.
 *
 * Plain text names its rows; highlighted code has shiki's `.line` spans in
 * file order, so the Nth of them is line N. Measured as the gap between the
 * two boxes rather than through `offsetTop`, which reports a distance from
 * the nearest *positioned* ancestor — the app frame, several boxes up — and
 * would land the line 76-odd pixels above the viewport, i.e. off the top of
 * it.
 */
export function scrollToLine(body: HTMLElement, line: number): void {
  const row =
    body.querySelector(`[data-preview-line="${String(line)}"]`) ??
    body.querySelectorAll('[data-preview-code] .line').item(line - 1)

  if (!(row instanceof HTMLElement)) return

  body.scrollTop += row.getBoundingClientRect().top - body.getBoundingClientRect().top
}

/** The path as the header shows it: the directory quiet, the name loud. */
function splitPath(path: string): { dir: string; name: string } {
  const at = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))

  return at === -1 ? { dir: '', name: path } : { dir: path.slice(0, at + 1), name: path.slice(at + 1) }
}

export function TextPreview({ path, tabId }: TextPreviewProps) {
  const tabs = useStore($textTabs)
  const navigation = useStore($navigation)
  const transcript = useStore($transcript)
  const workspace = useStore($workspace)
  const body = useRef<HTMLDivElement | null>(null)
  const [viewerOpen, setViewerOpen] = useState(false)

  const state = tabs[tabId]
  const line = navigation[tabId]?.line
  const revision = navigation[tabId]?.revision ?? 0
  const pages = useMemo(() => loadedPages(state?.pages ?? {}), [state?.pages])
  const loadedThrough = lastLineLoaded(pages)
  const started = state !== undefined
  const hasPages = pages.length > 0
  const viewer = state?.viewer ?? 'text'
  const choices = useMemo(() => viewerChoices(path), [path])

  const bytes = state?.bytes
  const whole = isByteViewer(viewer)
  const hasContent = whole ? bytes !== undefined : hasPages

  // The first mount reads what the path's own viewer needs — the first page,
  // or the file whole; a body returning to a tab that already has it reads
  // nothing, because the store outlived it.
  useEffect(() => {
    if (started) return

    if (isByteViewer(defaultViewer(path))) void loadBytes(tabId, path)
    else void loadPage(tabId, path, 1)
  }, [started, tabId, path])

  // A viewer switched to reads what it needs if the bucket does not hold it
  // yet: the frame wants the bytes, the code wants the pages. Never after a
  // failure, which is the reader's to retry.
  useEffect(() => {
    if (state === undefined || state.loading || state.failure !== undefined) return

    if (whole) {
      if (bytes === undefined) void loadBytes(tabId, path)
    } else if (!hasPages && !state.eof) {
      void loadPage(tabId, path, 1)
    }
  }, [bytes, hasPages, path, state, tabId, whole])

  // Come back where the reader was, once there is something to scroll. Keyed on
  // content presence and the viewer alone, so recording a scroll never re-lands
  // the body; a viewer change rebuilds the content, so it lands again.
  useEffect(() => {
    const element = body.current

    if (hasContent && element !== null) element.scrollTop = $textTabs.get()[tabId]?.scrollTop ?? 0
  }, [hasContent, tabId, viewer])

  // Answer a navigation once. A line the pages do not reach yet loads the next
  // page — again, until they cover it or the file ends — because pages load in
  // order and there is no seek. Rendered Markdown has no line to land on, so a
  // navigation there is answered by doing nothing.
  //
  // Bounded, though: each page is a round trip whose backend re-reads every
  // line before it, so walking to line 200,000 would be a hundred sequential
  // reads costing quadratic work. Past the bound the reader is left at the end
  // of the loaded text with Load more, which is honest about the cost.
  useEffect(() => {
    const element = body.current

    if (state === undefined || element === null || state.answered === revision) return

    if (line === undefined || viewer === 'markdown' || whole) {
      markAnswered(tabId, revision)

      return
    }

    if (line > loadedThrough && !state.eof && pages.length < WALK_PAGE_LIMIT) {
      if (!state.loading && state.failure === undefined) {
        void loadPage(tabId, path, loadedThrough + 1)
      }

      return
    }

    // A line past the loaded text — the walk gave up, or the file ended before
    // it — lands on the last line there is. Doing nothing at all would make the
    // click look broken.
    scrollToLine(element, Math.min(line, loadedThrough))
    markAnswered(tabId, revision)
    // Recorded here as well as by the scroll event, so the bucket holds the
    // landing before anything else reads it.
    setScroll(tabId, element.scrollTop)
  }, [
    line,
    loadedThrough,
    pages.length,
    path,
    revision,
    state?.answered,
    state?.eof,
    state?.failure,
    state?.loading,
    tabId,
    started,
    viewer,
    whole,
  ])

  const text = useMemo(() => pages.map(page => page.text).join('\n'), [pages])

  const rows = useMemo(
    () =>
      pages.map(page => (
        <pre className={css.page} key={page.offset}>
          {linesOf(page).map((content, index) => {
            const number = page.offset + index

            return (
              <div
                className={[css.line, number === line ? css.lineTarget : '']
                  .filter(Boolean)
                  .join(' ')}
                data-preview-line={number}
                key={number}
              >
                {content}
                {'\n'}
              </div>
            )
          })}
        </pre>
      )),
    [pages, line],
  )

  if (state === undefined) {
    return (
      <div className={css.status} data-preview-state="loading">
        Reading…
      </div>
    )
  }

  const next = loadedThrough + 1
  const reload = () => {
    void (whole ? reloadBytes(tabId, path) : reloadPages(tabId, path))
  }
  // Deliberately not memoised: the transcript rebuilds its node array on every
  // streamed token, so any memo keyed on it re-runs anyway. `changedSince`
  // scans backwards from the newest node and stops at the first write it
  // wants, which is the bound that actually holds.
  const changed = changedSince(transcript.nodes, path, state.readAt, workspace)
  const shown = splitPath(state.path)

  return (
    <div className={css.root} data-preview-state="text">
      {changed && (
        <p className={css.notice} data-preview-changed>
          <span>The file has changed; this is the older text.</span>
          <button className={css.action} onClick={reload} type="button">
            Reload
          </button>
        </p>
      )}
      <div className={css.header}>
        <div className={css.path} data-preview-path title={state.path}>
          <span className={css.pathDir}>
            <span>{shown.dir}</span>
          </span>
          <span className={css.pathName}>{shown.name}</span>
        </div>
        {choices.length > 1 && (
          <Menu
            align="end"
            anchor={
              <button
                aria-expanded={viewerOpen}
                aria-haspopup="menu"
                aria-label="Viewer"
                className={css.viewer}
                onClick={() => {
                  setViewerOpen(open => !open)
                }}
                title="How the file is drawn"
                type="button"
              >
                <span>{VIEWER_LABEL[viewer]}</span>
                <ChevronDownIcon size={12} />
              </button>
            }
            items={choices.map(choice => ({ id: choice, label: VIEWER_LABEL[choice] }))}
            onClose={() => {
              setViewerOpen(false)
            }}
            onSelect={id => {
              setViewer(tabId, id as PreviewViewer)
              setViewerOpen(false)
            }}
            open={viewerOpen}
            selectedId={viewer}
            side="bottom"
          />
        )}
        {(viewer === 'code' || viewer === 'text') && (
          <button
            aria-label="Wrap lines"
            aria-pressed={state.wrap}
            className={[css.tool, state.wrap ? css.toolOn : ''].filter(Boolean).join(' ')}
            onClick={() => {
              toggleWrap(tabId)
            }}
            title="Wrap lines"
            type="button"
          >
            <WrapIcon size={14} />
          </button>
        )}
        <button
          aria-label="Read the file again"
          className={css.tool}
          onClick={reload}
          title="Read the file again"
          type="button"
        >
          <RefreshIcon size={14} />
        </button>
      </div>
      <div
        className={[
          css.body,
          state.wrap ? css.wrap : '',
          viewer === 'text' ? css.bodyText : '',
          viewer === 'html' || viewer === 'pdf' ? css.bodyFrame : '',
        ]
          .filter(Boolean)
          .join(' ')}
        data-preview-body
        data-preview-viewer={viewer}
        onScroll={event => {
          // Only while there is something to scroll. A reload empties the
          // pages before its first page lands, and an empty body collapses to
          // scrollTop 0 and fires this — which would write the reader's place
          // away a frame before it is restored.
          if (hasPages) setScroll(tabId, event.currentTarget.scrollTop)
        }}
        ref={body}
      >
        {viewer === 'markdown' && hasPages && (
          <div className={css.prose} data-preview-markdown>
            <Markdown text={text} />
          </div>
        )}
        {viewer === 'code' && hasPages && (
          <div className={css.code} data-preview-code data-wrap={state.wrap}>
            <CodeBlock
              className={css.codeBlock}
              code={text}
              language={fileExtension(state.path)}
              lineNumbers
            />
          </div>
        )}
        {viewer === 'text' && rows}
        {whole && bytes === undefined && state.failure === undefined && (
          <p className={css.viewerStatus} data-preview-state="reading">
            Reading…
          </p>
        )}
        {viewer === 'html' && bytes !== undefined && <HtmlPreview data={bytes} path={state.path} />}
        {viewer === 'image' && bytes !== undefined && <ImagePreview data={bytes} path={state.path} />}
        {viewer === 'pdf' && bytes !== undefined && <PdfPreview data={bytes} path={state.path} />}
        {state.failure !== undefined && (
          <p className={css.statusLine} data-preview-failed={state.failure.code}>
            <span>{fileFailureLine(state.failure, whole)}</span>
            <button
              className={css.action}
              onClick={() => {
                void (whole ? loadBytes(tabId, path) : loadPage(tabId, path, next))
              }}
              type="button"
            >
              Retry
            </button>
          </p>
        )}
        {!whole && !state.eof && state.failure === undefined && (
          <button
            className={css.more}
            data-preview-more
            disabled={state.loading}
            onClick={() => {
              void loadPage(tabId, path, next)
            }}
            type="button"
          >
            {state.loading ? 'Reading…' : 'Load more'}
          </button>
        )}
      </div>
    </div>
  )
}
