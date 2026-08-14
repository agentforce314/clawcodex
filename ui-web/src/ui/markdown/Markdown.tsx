/**
 * Markdown → React, over marked's token stream.
 *
 * Rendering the token tree rather than marked's HTML string is what lets code
 * fences become real `CodeBlock` components (sticky banner, lazy shiki, copy)
 * and math become KaTeX — and it means no model output is ever passed to
 * `dangerouslySetInnerHTML`. React escapes every text node by construction, so
 * a reply containing `<script>` is text, not markup.
 */

import { marked, type Token, type Tokens } from 'marked'
import { Fragment, memo, useMemo, type ReactNode } from 'react'

import { CodeBlock } from '../primitives/CodeBlock.tsx'
// Aliased: an unqualified `Math` here would shadow the global one for the
// whole module, and this file does arithmetic.
import { Math as MathNode } from './Math.tsx'
import css from './Markdown.module.css'

/* ── math extraction ─────────────────────────────────────────────────────── */

/**
 * `$…$`, `\(…\)` inline and `$$…$$`, `\[…\]` display math.
 *
 * The single-`$` form needs guards or prices become equations. Three rules,
 * each earning its place against a real sentence:
 *
 *   - both delimiters hug non-space characters, and the body stays on one line
 *     and stays short — "it costs $5 and then $10 more." fails, because the
 *     body would end in a space;
 *   - the closing `$` is not followed by a digit — "$5-$10" fails, because
 *     that closing delimiter is really a second price's opening one;
 *   - "$x^2$" passes both, which is the case the feature exists for.
 */
const MATH_PATTERN =
  /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^\s$](?:[^\n$]{0,197}[^\s$])?\$(?![0-9]))/g

interface MathPiece {
  display: boolean
  tex: string
  type: 'math'
}

interface TextPiece {
  text: string
  type: 'text'
}

function splitMath(input: string): (MathPiece | TextPiece)[] {
  if (!input.includes('$') && !input.includes('\\(') && !input.includes('\\[')) {
    return [{ text: input, type: 'text' }]
  }

  const pieces: (MathPiece | TextPiece)[] = []
  let last = 0

  for (const match of input.matchAll(MATH_PATTERN)) {
    const index = match.index

    if (index === undefined) continue

    const raw = match[0]

    if (index > last) pieces.push({ text: input.slice(last, index), type: 'text' })

    if (raw.startsWith('$$')) {
      pieces.push({ display: true, tex: raw.slice(2, -2), type: 'math' })
    } else if (raw.startsWith('\\[')) {
      pieces.push({ display: true, tex: raw.slice(2, -2), type: 'math' })
    } else if (raw.startsWith('\\(')) {
      pieces.push({ display: false, tex: raw.slice(2, -2), type: 'math' })
    } else {
      pieces.push({ display: false, tex: raw.slice(1, -1), type: 'math' })
    }

    last = index + raw.length
  }

  if (last < input.length) pieces.push({ text: input.slice(last), type: 'text' })

  return pieces
}

function renderTextWithMath(text: string, keyBase: string): ReactNode {
  const pieces = splitMath(text)

  if (pieces.length === 1 && pieces[0]?.type === 'text') return pieces[0].text

  return pieces.map((piece, index) =>
    piece.type === 'math' ? (
      <MathNode display={piece.display} key={`${keyBase}-m${index}`} tex={piece.tex} />
    ) : (
      <Fragment key={`${keyBase}-t${index}`}>{piece.text}</Fragment>
    ),
  )
}

/* ── token rendering ─────────────────────────────────────────────────────── */

function renderInline(tokens: Token[] | undefined, keyBase: string): ReactNode {
  if (tokens === undefined) return null

  return tokens.map((token, index) => renderToken(token, `${keyBase}-${index}`))
}

function isExternal(href: string): boolean {
  return /^(https?:)?\/\//.test(href)
}

function renderToken(token: Token, key: string): ReactNode {
  switch (token.type) {
    case 'space':
      return null

    case 'text': {
      const text = token as Tokens.Text

      // An inline-token list means the text has nested emphasis/code/links;
      // otherwise it is a leaf and only math can still be hiding in it.
      if (text.tokens !== undefined && text.tokens.length > 0) {
        return <Fragment key={key}>{renderInline(text.tokens, key)}</Fragment>
      }

      return <Fragment key={key}>{renderTextWithMath(text.text, key)}</Fragment>
    }

    case 'escape':
      return <Fragment key={key}>{(token as Tokens.Escape).text}</Fragment>

    case 'paragraph': {
      const paragraph = token as Tokens.Paragraph

      return <p key={key}>{renderInline(paragraph.tokens, key)}</p>
    }

    case 'heading': {
      const heading = token as Tokens.Heading
      const Tag = `h${clampHeadingDepth(heading.depth)}` as 'h1'

      return <Tag key={key}>{renderInline(heading.tokens, key)}</Tag>
    }

    case 'strong':
      return <strong key={key}>{renderInline((token as Tokens.Strong).tokens, key)}</strong>

    case 'em':
      return <em key={key}>{renderInline((token as Tokens.Em).tokens, key)}</em>

    case 'del':
      return <del key={key}>{renderInline((token as Tokens.Del).tokens, key)}</del>

    case 'codespan':
      return <code key={key}>{(token as Tokens.Codespan).text}</code>

    case 'br':
      return <br key={key} />

    case 'link': {
      const link = token as Tokens.Link
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
          {renderInline(link.tokens, key)}
        </a>
      )
    }

    case 'image': {
      const image = token as Tokens.Image

      return <img alt={image.text} key={key} src={image.href} title={image.title ?? undefined} />
    }

    case 'code': {
      const code = token as Tokens.Code

      return <CodeBlock code={code.text} key={key} language={code.lang} />
    }

    case 'blockquote': {
      const quote = token as Tokens.Blockquote

      return <blockquote key={key}>{renderBlocks(quote.tokens, key)}</blockquote>
    }

    case 'hr':
      return <hr key={key} />

    case 'list': {
      const list = token as Tokens.List
      const items = list.items.map((item, index) => renderListItem(item, `${key}-${index}`))

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
                    {renderInline(cell.tokens, `${key}-h${index}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} style={{ textAlign: table.align[cellIndex] ?? undefined }}>
                      {renderInline(cell.tokens, `${key}-r${rowIndex}c${cellIndex}`)}
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

function renderListItem(item: Tokens.ListItem, key: string): ReactNode {
  if (item.task) {
    return (
      <li className={css.taskItem} key={key}>
        <input checked={item.checked === true} disabled readOnly type="checkbox" />
        {renderBlocks(item.tokens, key)}
      </li>
    )
  }

  return <li key={key}>{renderBlocks(item.tokens, key)}</li>
}

function renderBlocks(tokens: Token[], keyBase: string): ReactNode {
  return tokens.map((token, index) => renderToken(token, `${keyBase}-${index}`))
}

function clampHeadingDepth(depth: number): number {
  return Math.min(6, Math.max(1, depth))
}

/* ── component ───────────────────────────────────────────────────────────── */

export interface MarkdownProps {
  className?: string
  /** Paints a caret at the end while the reply is still streaming. */
  streaming?: boolean
  text: string
}

function MarkdownImpl({ className, streaming = false, text }: MarkdownProps) {
  const tokens = useMemo(() => {
    try {
      return marked.lexer(text, { gfm: true })
    } catch {
      // A malformed document must still show its text.
      return [{ raw: text, text, type: 'text' } as Token]
    }
  }, [text])

  return (
    <div className={[css.markdown, className].filter(Boolean).join(' ')}>
      {renderBlocks(tokens, 'md')}
      {streaming && <span aria-hidden="true" className={css.cursor} />}
    </div>
  )
}

/**
 * Memoized on text: a streaming turn re-renders the whole transcript on every
 * delta, and only the growing bubble's tokens actually changed.
 */
export const Markdown = memo(MarkdownImpl)
