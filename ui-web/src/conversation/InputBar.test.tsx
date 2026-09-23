import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { attachFile, attachImage } from '../state/actions.ts'
import { $commands } from '../state/store.ts'
import { InputBar } from './InputBar.tsx'

vi.mock('../state/actions.ts', async importOriginal => {
  const actual = await importOriginal<typeof import('../state/actions.ts')>()

  return {
    ...actual,
    attachFile: vi.fn(async () => ({ id: 7, name: 'notes.txt' })),
    attachImage: vi.fn(async () => 3),
  }
})

afterEach(cleanup)

beforeEach(() => {
  $commands.set([
    { description: 'Show available commands', name: '/help' },
    { description: 'Clear the conversation', name: '/clear' },
    { description: 'Compact the conversation to save context', name: '/compact' },
    { description: 'Enable plan mode', hint: '[<description>]', name: '/plan' },
  ])
})

function renderBar(approvalMode?: 'manual' | 'off' | 'smart', draft = '', onSubmit = vi.fn()) {
  return render(
    <InputBar
      approvalMode={approvalMode}
      draft={draft}
      effort={{ supported: false }}
      models={{}}
      onApprovalModeChange={vi.fn()}
      onDraftChange={vi.fn()}
      onEffortChange={vi.fn()}
      onModelChange={vi.fn()}
      onStop={vi.fn()}
      onSubmit={onSubmit}
      running={false}
      usage={null}
    />,
  )
}

describe('InputBar approval mode', () => {
  it('defaults to Full access before session.info reports a mode', () => {
    // Sessions spawn in Full Access (the backend's implicit interactive
    // default, same as the TUI), so the pre-session picker must not display
    // a stricter mode than the session will actually start in.
    renderBar()

    expect(
      screen.getByRole('button', { name: 'Approval mode: Full access' }),
    ).toBeTruthy()
  })

  it('shows the session-reported mode once known', () => {
    renderBar('manual')

    expect(
      screen.getByRole('button', { name: 'Approval mode: Ask every time' }),
    ).toBeTruthy()
  })
})

describe('InputBar command menu', () => {
  it('opens from the launcher with its two sections in usage order, and closes on Escape', () => {
    renderBar()

    fireEvent.click(screen.getByLabelText('Add files or run commands'))

    // Each row: the glyph, then the title. The image row comes first while the
    // model can read one; Plan follows it under Add, then the built-ins in
    // usage order, then the rest as the catalog lists them.
    const titles = screen
      .getAllByRole('option')
      .map(option => option.querySelectorAll('span')[1]?.textContent)

    expect(titles).toEqual(['Image', 'File', 'Plan', 'Compact', 'Clear', 'Help'])
    expect(screen.getByText('Add')).toBeTruthy()
    expect(screen.getByText('Commands')).toBeTruthy()

    fireEvent.keyDown(screen.getByLabelText('Message ClawCodex'), { key: 'Escape' })

    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('filters by what is typed after the slash and runs a bare command on Enter', () => {
    const onSubmit = vi.fn()

    renderBar(undefined, '/co', onSubmit)

    expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual([
      'CompactCompact the conversation to save context',
    ])

    fireEvent.keyDown(screen.getByLabelText('Message ClawCodex'), { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledWith('/compact')
  })
})

describe('InputBar file attachments', () => {
  /** The composer under an owner that keeps the draft, as ConversationRoot does. */
  function Harness({ onDraftChange, vision }: { onDraftChange: (text: string) => void; vision: boolean }) {
    const [draft, setDraft] = useState('')

    return (
      <InputBar
        draft={draft}
        effort={{ supported: false }}
        models={{}}
        onApprovalModeChange={vi.fn()}
        onDraftChange={text => {
          setDraft(text)
          onDraftChange(text)
        }}
        onEffortChange={vi.fn()}
        onModelChange={vi.fn()}
        onStop={vi.fn()}
        onSubmit={vi.fn()}
        running={false}
        usage={null}
        vision={vision}
      />
    )
  }

  function renderWithDraft(onDraftChange = vi.fn(), vision = true) {
    render(<Harness onDraftChange={onDraftChange} vision={vision} />)

    return onDraftChange
  }

  beforeEach(() => {
    vi.mocked(attachFile).mockClear()
    vi.mocked(attachImage).mockClear()
  })

  it('opens the file picker from the File row, and a pick lands a [File #N] chip in the draft', async () => {
    const onDraftChange = renderWithDraft()
    const input = screen.getByLabelText('Attach a file') as HTMLInputElement
    const open = vi.spyOn(input, 'click')

    fireEvent.click(screen.getByLabelText('Add files or run commands'))
    const row = screen
      .getAllByRole('option')
      .find(option => option.querySelectorAll('span')[1]?.textContent === 'File')
    if (row === undefined) throw new Error('no File row')
    // A row is picked on mousedown, so the textarea keeps its focus.
    fireEvent.mouseDown(row)

    expect(open).toHaveBeenCalled()

    const file = new File(['alpha'], 'notes.txt', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(onDraftChange).toHaveBeenCalledWith('[File #7] ')
    })
    expect(attachFile).toHaveBeenCalledWith(file, 'notes.txt')
    // The card follows the chip in the draft; removing it removes both.
    expect(screen.getByText('notes.txt')).toBeTruthy()
    expect(screen.getByText('TXT · 5 B')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Remove notes.txt'))
    expect(onDraftChange).toHaveBeenLastCalledWith('')
    expect(screen.queryByText('notes.txt')).toBeNull()
  })

  it('is offered even when the model cannot read images', () => {
    renderWithDraft(vi.fn(), false)

    fireEvent.click(screen.getByLabelText('Add files or run commands'))
    const titles = screen.getAllByRole('option').map(option => option.querySelectorAll('span')[1]?.textContent)

    expect(titles).not.toContain('Image')
    expect(titles).toContain('File')
    expect(screen.getByLabelText('Attach a file')).toBeTruthy()
  })

  it('sorts a drop: images the image way, everything else as a file', async () => {
    const onDraftChange = renderWithDraft()
    const textarea = screen.getByLabelText('Message ClawCodex')
    const sheet = new File(['a,b'], 'data.csv', { type: 'text/csv' })
    const shot = new File(['png'], 'shot.png', { type: 'image/png' })

    fireEvent.drop(textarea, { dataTransfer: { files: [sheet, shot], types: ['Files'] } })

    await waitFor(() => {
      expect(attachFile).toHaveBeenCalledWith(sheet, 'data.csv')
      expect(attachImage).toHaveBeenCalledWith(shot, 'shot.png')
    })
    await waitFor(() => {
      expect(onDraftChange).toHaveBeenCalled()
    })
  })

  it('refuses a dropped image on a model without vision but still takes the file beside it', async () => {
    renderWithDraft(vi.fn(), false)
    const textarea = screen.getByLabelText('Message ClawCodex')
    const doc = new File(['x'], 'brief.docx')
    const shot = new File(['png'], 'shot.png', { type: 'image/png' })

    fireEvent.drop(textarea, { dataTransfer: { files: [shot, doc], types: ['Files'] } })

    await waitFor(() => {
      expect(attachFile).toHaveBeenCalledWith(doc, 'brief.docx')
    })
    expect(attachImage).not.toHaveBeenCalled()
    expect(screen.getByRole('status').textContent).toContain('cannot read images')
  })
})

describe('InputBar files from the clipboard and folders', () => {
  function Harness({ onDraftChange }: { onDraftChange: (text: string) => void }) {
    const [draft, setDraft] = useState('')

    return (
      <InputBar
        draft={draft}
        effort={{ supported: false }}
        models={{}}
        onApprovalModeChange={vi.fn()}
        onDraftChange={text => {
          setDraft(text)
          onDraftChange(text)
        }}
        onEffortChange={vi.fn()}
        onModelChange={vi.fn()}
        onStop={vi.fn()}
        onSubmit={vi.fn()}
        running={false}
        usage={null}
      />
    )
  }

  beforeEach(() => {
    vi.mocked(attachFile).mockClear()
    vi.mocked(attachImage).mockClear()
  })

  it('attaches a file pasted from the file manager, and leaves a text paste alone', async () => {
    const onDraftChange = vi.fn()
    render(<Harness onDraftChange={onDraftChange} />)
    const textarea = screen.getByLabelText('Message ClawCodex')
    const doc = new File(['x'], 'brief.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })

    fireEvent.paste(textarea, { clipboardData: { files: [doc], items: [], getData: () => '' } })

    await waitFor(() => {
      expect(attachFile).toHaveBeenCalledWith(doc, 'brief.docx')
    })

    fireEvent.paste(textarea, { clipboardData: { files: [], items: [], getData: () => 'plain words' } })
    expect(attachFile).toHaveBeenCalledTimes(1)
  })

  it('skips a dropped folder with a notice instead of uploading an empty file', async () => {
    render(<Harness onDraftChange={vi.fn()} />)
    const textarea = screen.getByLabelText('Message ClawCodex')
    const folder = new File([], 'Documents')
    const note = new File(['n'], 'note.txt', { type: 'text/plain' })

    fireEvent.drop(textarea, { dataTransfer: { files: [folder, note], types: ['Files'] } })

    await waitFor(() => {
      expect(attachFile).toHaveBeenCalledWith(note, 'note.txt')
    })
    expect(attachFile).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status').textContent).toContain('Folders cannot be attached')
  })
})
