/**
 * The viewers that show a file whole: an HTML document in its own frame, an
 * image at its own size, a PDF through the browser's viewer.
 *
 * Each turns the bytes into a Blob URL that lives exactly as long as the
 * element showing it. Ported from the reference's HTML, image and PDF bodies;
 * the PDF one differs — the reference bundles PDF.js and draws pages itself,
 * where this hands the document to the browser's own viewer, which every
 * Chromium and Firefox has and which costs no bundle.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { readWorkspaceRelated } from '../state/actions.ts'
import { $columnDrag } from '../state/layout.ts'
import { createHtmlDocument, packHtml, referencePath, type ReadRelative } from './html-pack.ts'
import { decodeBase64, fileExtension } from './text-store.ts'
import css from './TextPreview.module.css'

/**
 * Hold an embedded document at its width for the length of a column drag.
 *
 * A frame follows its column's width, and an embedded document lays itself
 * out again at every pixel of it — a page of wide tables can take longer per
 * pixel than a frame lasts, which is what made a drag over one feel glued to
 * the pointer. While a handle is held the element keeps the width it had;
 * the column clips or leaves a gap beside it, and the release lays it out
 * once, at the final width.
 */
function useFrozenWidth<T extends HTMLElement>(): { current: T | null } {
  const ref = useRef<T | null>(null)
  const dragging = useStore($columnDrag)

  useLayoutEffect(() => {
    const element = ref.current

    if (element === null) return

    if (dragging) {
      element.style.width = `${String(element.getBoundingClientRect().width)}px`

      return
    }

    // Thawed one frame after the release, not with it: the release's own
    // paint — the cursor back to normal, the handle at rest — goes out first,
    // and the document's one re-layout at the final width follows it.
    const thaw = requestAnimationFrame(() => {
      element.style.width = ''
    })

    return () => {
      cancelAnimationFrame(thaw)
    }
  }, [dragging])

  return ref
}

/** A Blob URL for `data`, revoked when the data changes or the owner unmounts. */
function useObjectUrl(data: Uint8Array, type: string): string | null {
  const [url, setUrl] = useState<{ data: Uint8Array; url: string } | null>(null)

  useEffect(() => {
    const created = URL.createObjectURL(new Blob([data as BlobPart], { type }))

    setUrl({ data, url: created })

    return () => {
      URL.revokeObjectURL(created)
    }
  }, [data, type])

  return url?.data === data ? url.url : null
}

const IMAGE_MEDIA_TYPES: Record<string, string> = {
  avif: 'image/avif',
  bmp: 'image/bmp',
  gif: 'image/gif',
  ico: 'image/x-icon',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  png: 'image/png',
  svg: 'image/svg+xml',
  webp: 'image/webp',
}

/** The media type an image path's Blob is given, or undefined for a kind this set does not draw. */
export function imageMediaType(path: string): string | undefined {
  return IMAGE_MEDIA_TYPES[fileExtension(path)]
}

/** The image at its intrinsic size; the body around it scrolls. */
export function ImagePreview({ data, path }: { data: Uint8Array; path: string }) {
  const mediaType = imageMediaType(path) ?? 'application/octet-stream'
  const url = useObjectUrl(data, mediaType)
  const [state, setState] = useState<'failed' | 'loading' | 'ready'>('loading')

  useEffect(() => {
    setState('loading')
  }, [url])

  if (url === null) return <p className={css.viewerStatus}>Preparing the preview…</p>

  const name = path.split(/[/\\]/).filter(Boolean).at(-1) ?? path

  return (
    <div className={css.imageFrame} data-preview-image="">
      {state === 'loading' && <p className={css.viewerStatus}>Preparing the preview…</p>}
      {state === 'failed' && (
        <p className={css.viewerStatus} role="alert">
          This image could not be decoded.
        </p>
      )}
      {/* SVG arrives through an img, which keeps the browser in its static
          image mode: no scripts, no external loads. */}
      <img
        alt={`Preview of ${name}`}
        className={css.image}
        decoding="async"
        draggable={false}
        hidden={state !== 'ready'}
        onError={() => {
          setState('failed')
        }}
        onLoad={() => {
          setState('ready')
        }}
        referrerPolicy="no-referrer"
        src={url}
      />
    </div>
  )
}

/** The PDF, through the browser's own viewer. */
export function PdfPreview({ data, path }: { data: Uint8Array; path: string }) {
  const url = useObjectUrl(data, 'application/pdf')
  const frame = useFrozenWidth<HTMLObjectElement>()

  if (url === null) return <p className={css.viewerStatus}>Preparing the preview…</p>

  const name = path.split(/[/\\]/).filter(Boolean).at(-1) ?? path

  return (
    <object
      aria-label={`PDF preview of ${name}`}
      className={css.frame}
      data={url}
      data-preview-pdf=""
      ref={frame}
      type="application/pdf"
    >
      <p className={css.viewerStatus}>The browser could not display this PDF.</p>
    </object>
  )
}

type FrameState = { data: Uint8Array; url: string | null }

/**
 * The HTML document in a frame of its own. Packing reads the document's
 * relative stylesheets and scripts through the gateway, under the document's
 * path; replacing the content replaces the whole browsing context.
 */
export function HtmlPreview({ data, path }: { data: Uint8Array; path: string }) {
  const [frame, setFrame] = useState<FrameState | null>(null)
  const element = useFrozenWidth<HTMLIFrameElement>()

  useEffect(() => {
    const controller = new AbortController()
    let url: string | undefined

    const readRelative: ReadRelative = async (reference, signal) => {
      const relative = referencePath(reference)
      const result = await readWorkspaceRelated(path, relative)

      signal.throwIfAborted()

      if (!result.ok) throw new Error(result.error.message)

      return decodeBase64(result.data)
    }

    void (async () => {
      try {
        const bundle = await packHtml(data, readRelative, controller.signal)

        controller.signal.throwIfAborted()

        url = URL.createObjectURL(new Blob([createHtmlDocument(bundle)], { type: 'text/html' }))
        setFrame({ data, url })
      } catch {
        if (!controller.signal.aborted) setFrame({ data, url: null })
      }
    })()

    return () => {
      controller.abort()

      if (url !== undefined) URL.revokeObjectURL(url)
    }
  }, [data, path])

  if (frame?.data !== data) return <p className={css.viewerStatus}>Preparing the preview…</p>

  if (frame.url === null) {
    return (
      <p className={css.viewerStatus} role="alert">
        This HTML document could not be previewed.
      </p>
    )
  }

  return (
    <iframe
      className={css.frame}
      data-preview-html=""
      key={frame.url}
      ref={element}
      // Scripts run; the origin stays opaque, so they reach neither this app
      // nor the gateway. Never `allow-same-origin`.
      sandbox="allow-scripts"
      src={frame.url}
      title="HTML document preview"
    />
  )
}
