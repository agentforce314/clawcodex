/**
 * Markdown → React, over marked's token stream.
 *
 * Rendering the token tree rather than marked's HTML string is what lets code
 * fences become real `CodeBlock` components (sticky banner, lazy shiki, copy)
 * and math become KaTeX — and it means no model output is ever passed to
 * `dangerouslySetInnerHTML`. React escapes every text node by construction, so
 * a reply containing `<script>` is text, not markup.
 *
 * Math lives in the GRAMMAR (marked tokenizer extensions), not in a regex pass
 * over the finished tokens. The old post-hoc split broke on any TeX that
 * marked also had an opinion about — `$$\int x\,dx$$` arrived as three tokens
 * because `\,` lexed as an escape, and the delimiters could never reunite.
 * A tokenizer sees the source before escapes exist.
 *
 * While a reply streams, blocks that can no longer change are FROZEN: their
 * React elements are built once and reused, and only the trailing
 * `UNSTABLE_TAIL` blocks are re-lexed per delta. Keys are absolute source
 * offsets, so a block keeps its identity from first paint through seal.
 */

import { Marked, type Token, type Tokens, type TokenizerAndRendererExtension } from 'marked'
import { Fragment, memo, useMemo, useRef, type ReactNode } from 'react'

import { CodeBlock } from '../primitives/CodeBlock.tsx'
// Aliased: an unqualified `Math` here would shadow the global one for the
// whole module, and this file does arithmetic.
import { Math as MathNode } from './Math.tsx'
import css from './Markdown.module.css'

/* ── math grammar ────────────────────────────────────────────────────────── */

interface MathToken {
  display: boolean
  raw: string
  tex: string
  type: 'math' | 'inlineMath'
}

// `$$…$$` and `\[…\]` opening a line: display math as its own block. The
// close must be followed by end-of-line, so a paragraph does not lose its
// tail to a greedy match.
const BLOCK_DOLLARS = /^\$\$([\s\S]+?)\$\$ *(?:\n|$)/
const BLOCK_BRACKETS = /^\\\[([\s\S]+?)\\\] *(?:\n|$)/

/**
 * Inline forms. The single-`$` pattern carries the price guards, each earning
 * its place against a real sentence:
 *
 *   - both delimiters hug non-space characters, and the body stays on one
 *     line and stays short — "it costs $5 and then $10 more." fails, because
 *     a body may not contain `$` and no closing candidate survives;
 *   - the closing `$` is not followed by a digit — "$5-$10" fails, because
 *     that closing delimiter is really a second price's opening one;
 *   - "$x^2$" passes both, which is the case the feature exists for.
 */
const INLINE_DOLLARS = /^\$\$([\s\S]+?)\$\$/
const INLINE_PARENS = /^\\\(([\s\S]+?)\\\)/
const INLINE_BRACKETS = /^\\\[([\s\S]+?)\\\]/
const INLINE_SINGLE = /^\$([^\s$](?:[^\n$]{0,197}[^\s$])?)\$(?![0-9])/

const mathBlockExtension: TokenizerAndRendererExtension = {
  name: 'math',
  level: 'block',
  start(src: string) {
    const index = src.search(/\$\$|\\\[/)

    return index === -1 ? undefined : index
  },
  tokenizer(src: string): MathToken | undefined {
    const match = BLOCK_DOLLARS.exec(src) ?? BLOCK_BRACKETS.exec(src)

    if (match === null) return undefined

    return { display: true, raw: match[0], tex: (match[1] ?? '').trim(), type: 'math' }
  },
}

const mathInlineExtension: TokenizerAndRendererExtension = {
  name: 'inlineMath',
  level: 'inline',
  start(src: string) {
    const index = src.search(/\$|\\\(|\\\[/)

    return index === -1 ? undefined : index
  },
  tokenizer(src: string): MathToken | undefined {
    const display = INLINE_DOLLARS.exec(src) ?? INLINE_BRACKETS.exec(src)

    if (display !== null) {
      return { display: true, raw: display[0], tex: (display[1] ?? '').trim(), type: 'inlineMath' }
    }

    const inline = INLINE_PARENS.exec(src) ?? INLINE_SINGLE.exec(src)

    if (inline !== null) {
      return { display: false, raw: inline[0], tex: (inline[1] ?? '').trim(), type: 'inlineMath' }
    }

    return undefined
  },
}

// One private instance so the extensions never leak into other users of the
// shared `marked` singleton.
const parser = new Marked({ gfm: true }, { extensions: [mathBlockExtension, mathInlineExtension] })

function lex(text: string): Token[] {
  try {
    return parser.lexer(text)
  } catch {
    // A malformed document must still show its text.
    return [{ raw: text, text, type: 'text' } as Token]
  }
}

/* ── url safety ──────────────────────────────────────────────────────────── */

/**
 * Model-authored URLs are untrusted. Links keep only protocols that navigate
 * somewhere harmless; anything else (`javascript:`, `data:`, `vbscript:`,
 * protocol-relative smuggling) renders as its text without an anchor.
 */
const SAFE_LINK = /^(?:https?:|mailto:)/i

/** Images additionally must be absolute http(s): a relative src would fetch
    from this app's own origin, and `data:` bodies can be megabytes of noise. */
const SAFE_IMAGE = /^https?:\/\//i

function isExternal(href: string): boolean {
  return /^(https?:)?\/\//.test(href)
}

const BARE_URL = /^https?:\/\/\S+$/i

/* ── token rendering ─────────────────────────────────────────────────────── */

interface RenderOptions {
  /**
   * False for the live tail of a streaming reply. An unsettled code fence
   * renders plain — its text is still growing, and an async highlight of a
   * stale prefix would lag the stream by one round trip, showing old code
   * under a new banner.
   */
  settled: boolean
}

const SETTLED: RenderOptions = { settled: true }

function renderInline(tokens: Token[] | undefined, keyBase: string, options: RenderOptions): ReactNode {
  if (tokens === undefined) return null

  return tokens.map((token, index) => renderToken(token, `${keyBase}-${index}`, options))
}

function renderToken(token: Token, key: string, options: RenderOptions): ReactNode {
  switch (token.type) {
    case 'space':
      return null

    case 'math':
    case 'inlineMath': {
      const math = token as unknown as MathToken

      return <MathNode display={math.display} key={key} tex={math.tex} />
    }

    // The checkbox itself is drawn by `renderListItem`; marked's token would
    // otherwise fall through to the default arm and print a literal `[x]`.
    case 'checkbox':
      return null

    case 'text': {
      const text = token as Tokens.Text

      // An inline-token list means the text has nested emphasis/code/links;
      // otherwise it is a leaf.
      if (text.tokens !== undefined && text.tokens.length > 0) {
        return <Fragment key={key}>{renderInline(text.tokens, key, options)}</Fragment>
      }

      return <Fragment key={key}>{text.text}</Fragment>
    }

    case 'escape':
      return <Fragment key={key}>{(token as Tokens.Escape).text}</Fragment>

    case 'paragraph': {
      const paragraph = token as Tokens.Paragraph

      return <p key={key}>{renderInline(paragraph.tokens, key, options)}</p>
    }

    case 'heading': {
      const heading = token as Tokens.Heading
      const Tag = `h${clampHeadingDepth(heading.depth)}` as 'h1'

      return <Tag key={key}>{renderInline(heading.tokens, key, options)}</Tag>
    }

    case 'strong':
      return (
        <strong key={key}>{renderInline((token as Tokens.Strong).tokens, key, options)}</strong>
      )

    case 'em':
      return <em key={key}>{renderInline((token as Tokens.Em).tokens, key, options)}</em>

    case 'del':
      return <del key={key}>{renderInline((token as Tokens.Del).tokens, key, options)}</del>

    case 'codespan': {
      const code = (token as Tokens.Codespan).text

      // A backticked URL is a *reference*, and readers expect to follow it.
      if (BARE_URL.test(code)) {
        return (
          <a href={code} key={key} rel="noopener noreferrer" target="_blank">
            <code>{code}</code>
          </a>
        )
      }

      return <code key={key}>{code}</code>
    }

    case 'br':
      return <br key={key} />

    case 'link': {
      const link = token as Tokens.Link

      // Unsafe protocol: the text stays, the affordance goes.
      if (!SAFE_LINK.test(link.href)) {
        return <Fragment key={key}>{renderInline(link.tokens, key, options)}</Fragment>
      }

      const external = isExternal(link.href)

      return (
        <a
          href={link.href}
          key={key}
          // `noopener` is the security half and `noreferrer` the privacy half;
          // both belong on any link that opens a page we do not control.
          rel={external ? 'noopener noreferrer' : undefined}
          target={external ? '_blank' : undefined}
          title={link.title ?? undefined}
        >
          {renderInline(link.tokens, key, options)}
        </a>
      )
    }

    case 'image': {
      const image = token as Tokens.Image

      if (!SAFE_IMAGE.test(image.href)) {
        return (
          <span className={css.imageAlt} key={key}>
            {image.text || image.href}
          </span>
        )
      }

      return (
        <img
          alt={image.text}
          decoding="async"
          key={key}
          loading="lazy"
          referrerPolicy="no-referrer"
          src={image.href}
          title={image.title ?? undefined}
        />
      )
    }

    case 'code': {
      const code = token as Tokens.Code

      return <CodeBlock code={code.text} key={key} language={code.lang} settled={options.settled} />
    }

    case 'blockquote': {
      const quote = token as Tokens.Blockquote

      return <blockquote key={key}>{renderBlocks(quote.tokens, key, options)}</blockquote>
    }

    case 'hr':
      return <hr key={key} />

    case 'list': {
      const list = token as Tokens.List
      const items = list.items.map((item, index) =>
        renderListItem(item, `${key}-${index}`, options),
      )

      return list.ordered ? (
        <ol key={key} start={typeof list.start === 'number' ? list.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul key={key}>{items}</ul>
      )
    }

    case 'table': {
      const table = token as Tokens.Table

      return (
        <div className={css.tableScroll} key={key}>
          <table>
            <thead>
              <tr>
                {table.header.map((cell, index) => (
                  <th key={index} style={{ textAlign: table.align[index] ?? undefined }}>
                    {renderInline(cell.tokens, `${key}-h${index}`, options)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} style={{ textAlign: table.align[cellIndex] ?? undefined }}>
                      {renderInline(cell.tokens, `${key}-r${rowIndex}c${cellIndex}`, options)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    }

    case 'html':
      // Raw HTML in model output is shown as source, never mounted. A model can
      // emit anything, and the transcript is not a place where markup executes.
      return <Fragment key={key}>{(token as Tokens.HTML).text}</Fragment>

    default: {
      const fallback = token as { raw?: string; text?: string }

      return <Fragment key={key}>{fallback.text ?? fallback.raw ?? ''}</Fragment>
    }
  }
}

function renderListItem(item: Tokens.ListItem, key: string, options: RenderOptions): ReactNode {
  if (item.task) {
    return (
      <li className={css.taskItem} key={key}>
        <input checked={item.checked === true} disabled readOnly type="checkbox" />
        {renderBlocks(item.tokens, key, options)}
      </li>
    )
  }

  return <li key={key}>{renderBlocks(item.tokens, key, options)}</li>
}

function renderBlocks(tokens: Token[], keyBase: string, options: RenderOptions): ReactNode {
  return tokens.map((token, index) => renderToken(token, `${keyBase}-${index}`, options))
}

function clampHeadingDepth(depth: number): number {
  return Math.min(6, Math.max(1, depth))
}

/* ── streaming: freeze settled blocks ────────────────────────────────────── */

/**
 * Trailing blocks that stay live. The LAST block is obviously still growing;
 * one more is margin for constructs a later line can still reshape (a setext
 * underline, a table's delimiter row, lazy list continuation). Everything
 * before that is final by the block grammar and never re-lexed.
 */
const UNSTABLE_TAIL = 2

interface StreamState {
  /** Source prefix the frozen elements cover. */
  frozenSource: string
  frozenNodes: ReactNode[]
}

/** Top-level blocks keyed by ABSOLUTE source offset, stable across deltas. */
function renderAbsolute(
  tokens: Token[],
  startOffset: number,
  options: RenderOptions,
): { length: number; nodes: ReactNode[] } {
  const nodes: ReactNode[] = []
  let offset = startOffset

  for (const token of tokens) {
    nodes.push(renderToken(token, `md-${offset}`, options))
    offset += token.raw.length
  }

  return { length: offset - startOffset, nodes }
}

/* ── component ───────────────────────────────────────────────────────────── */

export interface MarkdownProps {
  className?: string
  /** Paints a caret at the end while the reply is still streaming. */
  streaming?: boolean
  text: string
}

function MarkdownImpl({ className, streaming = false, text }: MarkdownProps) {
  const stream = useRef<StreamState | null>(null)

  const content = useMemo(() => {
    if (!streaming) {
      stream.current = null

      // Absolute keys here too, so the one re-render at seal reconciles
      // against the streamed blocks instead of remounting the whole reply.
      return renderAbsolute(lex(text), 0, SETTLED).nodes
    }

    let state = stream.current

    // A rewind (retry, edit) is the only way the frozen prefix can change;
    // starting over is correct and costs one full lex.
    if (state === null || !text.startsWith(state.frozenSource)) {
      state = { frozenNodes: [], frozenSource: '' }
    }

    const tailTokens = lex(text.slice(state.frozenSource.length))
    const freezeCount = tailTokens.length - UNSTABLE_TAIL

    if (freezeCount > 0) {
      const frozen = renderAbsolute(
        tailTokens.slice(0, freezeCount),
        state.frozenSource.length,
        SETTLED,
      )

      state = {
        frozenNodes: [...state.frozenNodes, ...frozen.nodes],
        frozenSource:
          state.frozenSource +
          text.slice(state.frozenSource.length, state.frozenSource.length + frozen.length),
      }
    }

    stream.current = state

    const live = renderAbsolute(
      tailTokens.slice(Math.max(0, freezeCount)),
      state.frozenSource.length,
      { settled: false },
    )

    return [...state.frozenNodes, ...live.nodes]
  }, [streaming, text])

  return (
    <div className={[css.markdown, className].filter(Boolean).join(' ')}>
      {content}
      {streaming && <span aria-hidden="true" className={css.cursor} />}
    </div>
  )
}

/**
 * Memoized on text: a streaming turn re-renders the whole transcript on every
 * delta, and only the growing bubble's tokens actually changed.
 */
export const Markdown = memo(MarkdownImpl)
