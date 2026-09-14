import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import type { FilePage } from '../gateway/protocol.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $columnDrag } from '../state/layout.ts'
import { $sessionId, $transcript, $workspace } from '../state/store.ts'
import { emptyTranscript, type ToolNode } from '../state/transcript.ts'
import { $textTabs, reloadPages, resetTextTabs, setScroll } from './text-store.ts'
import { openFile, resetSidebar } from './store.ts'
import { scrollToLine, TextPreview } from './TextPreview.tsx'

const request = vi.fn()

function page(over: Partial<FilePage> = {}): { ok: true } & FilePage {
  return {
    absolute_path: '/repo/a.txt',
    bytes: 40,
    eof: true,
    lines: 3,
    offset: 1,
    ok: true,
    text: 'one\ntwo\nthree',
    version: 'v1',
    ...over,
  }
}

const TAB = 'text:/repo/a.txt'

function write(over: Partial<ToolNode> = {}): ToolNode {
  return {
    args: { path: '/repo/a.txt' },
    endedAt: 5_000,
    id: 'n1',
    kind: 'tool',
    name: 'write_file',
    startedAt: 4_000,
    state: 'done',
    toolId: 't1',
    ...over,
  }
}

beforeEach(() => {
  request.mockReset()
  request.mockResolvedValue(page())
  setGatewayClient({ request } as unknown as GatewayClient)
  $sessionId.set('s1')
  $workspace.set('/repo')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
})

afterEach(() => {
  cleanup()
  setGatewayClient(null)
  $sessionId.set(null)
  $workspace.set('')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
})

/**
 * Fix one element's box. jsdom lays nothing out, so a scroll test that reads
 * real geometry asserts zero against zero and passes however the code moves the
 * scroller — the measurement has to be supplied.
 */
function boxed(element: Element, top: number): void {
  element.getBoundingClientRect = () => ({ top, bottom: top + 20 }) as DOMRect
}

describe('scrollToLine', () => {
  it('scrolls by the gap between the row and the body, not by offsetTop', () => {
    // offsetTop is measured from the nearest POSITIONED ancestor — the app
    // frame, several boxes up — so using it lands the line off the top of the
    // viewport by the height of everything above the body.
    const body = document.createElement('div')

    body.innerHTML = '<div data-preview-line="7"></div>'
    body.scrollTop = 100

    const row = body.firstElementChild as HTMLElement

    Object.defineProperty(row, 'offsetTop', { value: 999 })
    boxed(body, 200)
    boxed(row, 260)

    scrollToLine(body, 7)

    // 60px below the body's top, from a scroller already 100px down.
    expect(body.scrollTop).toBe(160)
  })

  it('leaves the scroller alone for a line the pages do not hold', () => {
    const body = document.createElement('div')

    body.scrollTop = 42
    scrollToLine(body, 7)

    expect(body.scrollTop).toBe(42)
  })
})

describe('TextPreview', () => {
  it('reads the first page on its first mount', async () => {
    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('reads nothing when it comes back to a tab that already has pages', async () => {
    const { unmount } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
    unmount()
    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    expect(request).toHaveBeenCalledTimes(1)
  })

  it('shows one row per line, so an empty line is one line tall', async () => {
    request.mockResolvedValue(page({ lines: 3, text: 'one\n\nthree' }))

    const { container } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-preview-line]')).toHaveLength(3)
    })
  })

  it('offers the next page until the file ends', async () => {
    request.mockResolvedValueOnce(page({ eof: false }))

    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    const more = await screen.findByText('Load more')

    request.mockResolvedValueOnce(page({ lines: 1, offset: 4, text: 'four' }))
    fireEvent.click(more)

    await waitFor(() => {
      expect(screen.getByText('four')).toBeTruthy()
    })
    expect(request).toHaveBeenLastCalledWith('fs.read_file', {
      offset: 4,
      path: '/repo/a.txt',
      session_id: 's1',
    })
    expect(screen.queryByText('Load more')).toBeNull()
  })

  it('says why a page is missing and offers the same page again', async () => {
    request.mockResolvedValueOnce({
      error: { code: 'workspace-file/not-found', message: 'gone' },
      ok: false,
    })

    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    expect(await screen.findByText('That file is gone. It may have been moved or deleted.'))
      .toBeTruthy()

    request.mockResolvedValueOnce(page())
    fireEvent.click(screen.getByText('Retry'))

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
  })

  it('wraps by default and unwraps on the toggle', async () => {
    const { container } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await screen.findByText('three')

    const toggle = screen.getByLabelText('Wrap lines')

    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('[data-preview-body]')?.className).toMatch(/wrap/)

    fireEvent.click(toggle)

    expect($textTabs.get()[TAB]?.wrap).toBe(false)
  })

  it('announces a file the agent wrote after the page was read, without applying it', async () => {
    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)
    await screen.findByText('three')

    $transcript.set({ ...emptyTranscript(), nodes: [write({ endedAt: Date.now() + 1000 })] })

    const notice = await screen.findByText('The file has changed; this is the older text.')

    // Announced, not applied: the pages under the reader are untouched until
    // the click.
    expect(notice).toBeTruthy()
    expect(request).toHaveBeenCalledTimes(1)
    expect(screen.getByText('three')).toBeTruthy()

    request.mockResolvedValueOnce(page({ text: 'after', lines: 1, version: 'v2' }))
    fireEvent.click(screen.getByText('Reload'))

    await waitFor(() => {
      expect(screen.getByText('after')).toBeTruthy()
    })
  })

  it('says nothing about a write that happened before the read', async () => {
    $transcript.set({ ...emptyTranscript(), nodes: [write({ endedAt: 1 })] })

    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)
    await screen.findByText('three')

    expect(screen.queryByText('The file has changed; this is the older text.')).toBeNull()
  })

  it('keeps the reader\'s place across a reload', async () => {
    // A reload empties the pages before its first page lands. In a browser the
    // body then collapses, scrollTop is clamped to 0 and a scroll event fires —
    // which used to write that 0 over the offset the restore was about to read,
    // returning the reader to the top of every file they reloaded. jsdom does
    // no layout, so the clamp is supplied here; the guard is what is under test.
    const { container } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await screen.findByText('three')
    setScroll(TAB, 420)

    let land = (): void => {}

    request.mockImplementationOnce(
      async () =>
        new Promise(resolve => {
          land = () => {
            resolve(page({ text: 'after the edit' }))
          }
        }),
    )

    const reloading = reloadPages(TAB, '/repo/a.txt')
    const body = container.querySelector('[data-preview-body]') as HTMLElement

    await waitFor(() => {
      expect(body.querySelector('[data-preview-line]')).toBeNull()
    })

    body.scrollTop = 0
    fireEvent.scroll(body)

    expect($textTabs.get()[TAB]?.scrollTop).toBe(420)

    land()
    await reloading
    await waitFor(() => {
      expect(screen.getByText('after the edit')).toBeTruthy()
    })
    expect(body.scrollTop).toBe(420)
  })

  it('lands on the last loaded line when the walk gave up', async () => {
    // Silence would make the click look broken; the reader arrives at the end
    // of what is loaded, beside Load more.
    request.mockImplementation(async (_method: string, params: { offset: number }) =>
      page({ eof: false, lines: 3, offset: params.offset, text: 'a\nb\nc' }),
    )
    openFile('/repo/a.txt', 100_000)

    const { container } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await screen.findByText('Load more')
    await waitFor(() => {
      expect($textTabs.get()[TAB]?.answered).toBe(1)
    })
    // Answered against a real line rather than abandoned.
    expect(container.querySelectorAll('[data-preview-line]').length).toBeGreaterThan(0)
  })

  it('marks the line a navigation asked for, once', async () => {
    openFile('/repo/a.txt', 2)

    const { container } = render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await waitFor(() => {
      expect(container.querySelector('[data-preview-line="2"]')?.className).toMatch(/lineTarget/)
    })
    await waitFor(() => {
      expect($textTabs.get()[TAB]?.answered).toBe(1)
    })
  })

  it('walks pages until they reach the line it was sent to', async () => {
    // Pages load in order; there is no seek, so a deep line reads forward.
    request.mockResolvedValueOnce(page({ eof: false }))
    openFile('/repo/a.txt', 5)

    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    request.mockResolvedValueOnce(page({ lines: 2, offset: 4, text: 'four\nfive' }))

    await waitFor(() => {
      expect(screen.getByText('five')).toBeTruthy()
    })
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('stops walking rather than paging its way to a very deep line', async () => {
    // Every page is a round trip whose backend re-reads the lines before it, so
    // an offset deep into a large file would be a hundred sequential reads.
    request.mockImplementation(async (_method: string, params: { offset: number }) =>
      page({ eof: false, lines: 3, offset: params.offset, text: 'a\nb\nc' }),
    )
    openFile('/repo/a.txt', 100_000)

    render(<TextPreview path="/repo/a.txt" tabId={TAB} />)

    await screen.findByText('Load more')
    await waitFor(() => {
      expect(request.mock.calls.length).toBeGreaterThan(1)
    })
    // Bounded: the reader is left at the end of the loaded text with Load more.
    await new Promise(resolve => {
      setTimeout(resolve, 50)
    })
    expect(request.mock.calls.length).toBeLessThanOrEqual(5)
  })
})

describe('viewers', () => {
  it('opens Markdown as prose, with plain text one pick away', async () => {
    openFile('/repo/notes.md')
    request.mockImplementation(async () => ({
      absolute_path: '/repo/notes.md',
      bytes: 8,
      eof: true,
      lines: 1,
      offset: 1,
      ok: true,
      text: '# Title',
      version: 'v1',
    }))

    const { container } = render(<TextPreview path="/repo/notes.md" tabId="text:/repo/notes.md" />)

    await waitFor(() => {
      expect(container.querySelector('[data-preview-markdown] h1')?.textContent).toBe('Title')
    })
    expect(screen.queryByLabelText('Wrap lines')).toBeNull()

    fireEvent.click(screen.getByLabelText('Viewer'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Plain text' }))

    await waitFor(() => {
      expect(container.querySelector('[data-preview-line="1"]')?.textContent).toBe('# Title\n')
    })
  })

  it('opens code behind a line gutter and names the file over its directory', async () => {
    openFile('/repo/src/app.ts')
    request.mockImplementation(async () => ({
      absolute_path: '/repo/src/app.ts',
      bytes: 20,
      eof: true,
      lines: 2,
      offset: 1,
      ok: true,
      text: 'const a = 1\nconst b = 2',
      version: 'v1',
    }))

    const { container } = render(<TextPreview path="/repo/src/app.ts" tabId="text:/repo/src/app.ts" />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-preview-code] .line')).toHaveLength(2)
    })
    expect(container.querySelector('[data-preview-path]')?.textContent).toBe('/repo/src/app.ts')
    expect(screen.getByLabelText('Viewer').textContent).toContain('Code')
  })
})

describe('whole-file viewers', () => {
  const objectUrls: string[] = []

  beforeEach(() => {
    // jsdom has no Blob URLs; the viewers only need one that is a string.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => {
        const url = `blob:test/${String(objectUrls.length)}`

        objectUrls.push(url)

        return url
      }),
    })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  })

  it('shows an HTML document in a script-only sandboxed frame', async () => {
    openFile('/repo/index.html')
    request.mockImplementation(async (method: string) => {
      if (method !== 'fs.read_bytes') throw new Error(`unexpected ${method}`)

      return {
        absolute_path: '/repo/index.html',
        bytes: 9,
        data: btoa('<p>hi</p>'),
        eof: true,
        offset: 0,
        ok: true,
        version: 'v1',
      }
    })

    const { container } = render(<TextPreview path="/repo/index.html" tabId="text:/repo/index.html" />)

    await waitFor(() => {
      expect(container.querySelector('iframe[data-preview-html]')).not.toBeNull()
    })

    const frame = container.querySelector('iframe[data-preview-html]')

    expect(frame?.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame?.getAttribute('src')).toMatch(/^blob:/)
    expect(screen.queryByLabelText('Wrap lines')).toBeNull()
    expect(screen.queryByText('Load more')).toBeNull()
    expect(screen.getByLabelText('Viewer').textContent).toContain('HTML')

    // While a column handle is held the frame keeps the width it had (jsdom
    // measures it as zero), and takes the column's again on the release.
    act(() => {
      $columnDrag.set(true)
    })
    expect((frame as HTMLElement).style.width).toBe('0px')
    act(() => {
      $columnDrag.set(false)
    })
    expect((frame as HTMLElement).style.width).toBe('')
  })

  it('shows an image at its own size and offers no other viewer for it', async () => {
    openFile('/repo/logo.png')
    request.mockImplementation(async () => ({
      absolute_path: '/repo/logo.png',
      bytes: 4,
      data: btoa('\x89PNG'),
      eof: true,
      offset: 0,
      ok: true,
      version: 'v1',
    }))

    const { container } = render(<TextPreview path="/repo/logo.png" tabId="text:/repo/logo.png" />)

    await waitFor(() => {
      expect(container.querySelector('[data-preview-image] img')).not.toBeNull()
    })
    expect(container.querySelector('[data-preview-image] img')?.getAttribute('src')).toMatch(/^blob:/)
    expect(screen.queryByLabelText('Viewer')).toBeNull()
  })

  it('says a whole file is too large in terms of the file', async () => {
    openFile('/repo/big.pdf')
    request.mockImplementation(async () => ({
      error: { code: 'workspace-file/too-large', details: { limit: 32 * 1024 * 1024 }, message: 'x' },
      ok: false,
    }))

    render(<TextPreview path="/repo/big.pdf" tabId="text:/repo/big.pdf" />)

    await waitFor(() => {
      expect(screen.getByText(/That file is too large; the sidebar does not read files above 32 MB\./)).toBeTruthy()
    })
  })
})
