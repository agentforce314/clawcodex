import { describe, expect, it, vi } from 'vitest'

// The REAL helper from the composer, not a reimplementation. An earlier version
// of this file tested a local copy of the branch, which meant reordering the
// production code left every test green — i.e. the ordering contract these tests
// exist to protect was not actually protected.
import { resolveHotkeyPaste } from '../app/useComposerState.js'
import { attachedImageNotice } from '../domain/messages.js'

// A real U+0000 and a control-character-heavy string: both rejected by the
// REAL isUsableClipboardText, and both accepted by a naive `t.trim()` check --
// which is exactly why this suite must call the production helper.
const NUL = String.fromCharCode(0)
const CONTROL_HEAVY = Array.from({ length: 12 }, (_, i) => String.fromCharCode(i + 1)).join('')

const probing = (result: unknown) =>
  vi.fn(async () => result as never)

describe('resolveHotkeyPaste — probe ordering', () => {
  it('does NOT probe when the clipboard has usable text', async () => {
    // The performance contract: the probe shells out to osascript/xclip (~1.5 s
    // for a large clipboard image), so an ordinary Ctrl+V must never pay for it.
    const probe = probing({ name: 'clipboard image' })

    const decision = await resolveHotkeyPaste({
      probeClipboardImage: probe,
      text: 'some copied text'
    })

    expect(probe).not.toHaveBeenCalled()
    expect(decision).toEqual({ kind: 'text', text: 'some copied text' })
  })

  it('probes when the clipboard text is unusable', async () => {
    // Uses the real isUsableClipboardText, which rejects more than "empty":
    // whitespace-only, NUL-bearing, and control-character-heavy text.
    const unusable: (null | string)[] = ['', '   \n  ', null, `has${NUL}nul`, CONTROL_HEAVY]

    for (const text of unusable) {
      const probe = probing({ name: 'clipboard image' })
      const decision = await resolveHotkeyPaste({ probeClipboardImage: probe, text })

      expect(probe, `text=${JSON.stringify(text)}`).toHaveBeenCalledTimes(1)
      expect(decision.kind, `text=${JSON.stringify(text)}`).toBe('image')
    }
  })

  it('ordinary text is usable and never probes', async () => {
    // The counterpart: these must NOT be mistaken for "no usable text", or
    // every paste of them would shell out. Note 'has nul in the words' --
    // plain text that merely mentions nul is usable; only a real U+0000 is not.
    for (const text of ['hello', 'has nul in the words', 'multi\nline\ntext', '  padded  ']) {
      const probe = probing({ name: 'clipboard image' })
      const decision = await resolveHotkeyPaste({ probeClipboardImage: probe, text })

      expect(probe, `text=${JSON.stringify(text)}`).not.toHaveBeenCalled()
      expect(decision, `text=${JSON.stringify(text)}`).toEqual({ kind: 'text', text })
    }
  })

  it('returns the image payload so the caller can render its metadata', async () => {
    const image = { height: 220, name: 'clipboard image', token_estimate: 300, width: 760 }

    const decision = await resolveHotkeyPaste({
      probeClipboardImage: probing(image),
      text: ''
    })

    expect(decision).toEqual({ image, kind: 'image' })
  })

  it('resolves to none when the clipboard holds no image', async () => {
    // Not a failure — the caller falls back to its text-paste path.
    const decision = await resolveHotkeyPaste({ probeClipboardImage: probing({}), text: '' })

    expect(decision).toEqual({ kind: 'none' })
  })

  it('surfaces a read failure instead of appearing to do nothing', async () => {
    const image = { error: 'could not read image: /tmp/x.png' }
    const decision = await resolveHotkeyPaste({ probeClipboardImage: probing(image), text: '' })

    expect(decision).toEqual({ image, kind: 'image' })
  })

  it('surfaces missing clipboard tooling', async () => {
    const image = { unavailable: true }
    const decision = await resolveHotkeyPaste({ probeClipboardImage: probing(image), text: '' })

    expect(decision).toEqual({ image, kind: 'image' })
  })

  it('a rejected probe degrades to none rather than throwing', async () => {
    const decision = await resolveHotkeyPaste({
      probeClipboardImage: () => Promise.reject(new Error('backend gone')),
      text: ''
    })

    expect(decision).toEqual({ kind: 'none' })
  })

  it('a null probe result degrades to none', async () => {
    // What an unwired backend resolves to; must not be mistaken for an attach.
    const decision = await resolveHotkeyPaste({ probeClipboardImage: probing(null), text: '' })

    expect(decision).toEqual({ kind: 'none' })
  })
})

describe('attachedImageNotice', () => {
  it('confirms a real attach with its metadata', () => {
    expect(
      attachedImageNotice({ height: 914, name: 'shot.png', token_estimate: 976, width: 1568 })
    ).toContain('📎 Attached image: shot.png')
  })

  it('does NOT claim an attach when the backend reported an error', () => {
    // This printed "📎 Attached image" for a FAILED /image before: the notice
    // only checked `name`, and `name` is absent on failure.
    const msg = attachedImageNotice({ error: 'could not read image: /tmp/nope.png' })

    expect(msg).not.toContain('📎')
    expect(msg).toContain('not attached')
    expect(msg).toContain('/tmp/nope.png')
  })

  it('names the missing tooling so the user can fix it', () => {
    const msg = attachedImageNotice({ unavailable: true })

    expect(msg).not.toContain('📎')
    expect(msg).toMatch(/xclip|wl-clipboard/)
  })

  it('does not claim an attach for an empty reply', () => {
    expect(attachedImageNotice({})).not.toContain('📎')
    expect(attachedImageNotice(null)).not.toContain('📎')
  })
})
