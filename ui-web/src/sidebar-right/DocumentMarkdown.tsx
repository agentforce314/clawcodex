import { useMemo } from 'react'
import { isMap, isNode, isScalar, parseDocument } from 'yaml'
import { Markdown } from '../ui/markdown/Markdown.tsx'
import css from './TextPreview.module.css'

/** Leading, closed YAML only; ordinary Markdown rules and unfinished blocks retain their text. */
export function splitFrontmatter(text: string): { source: string; body: string } | undefined {
  const match = /^\uFEFF?---[ \t]*\r?\n(?:([\s\S]*?)\r?\n)??(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/u.exec(text)
  return match ? { source: match[1] ?? '', body: text.slice(match[0].length) } : undefined
}

/** Read the YAML syntax tree without resolving aliases or constructing application objects. */
export function frontmatterFields(source: string): { key: string; value: string }[] | undefined {
  try {
    const document = parseDocument(source)
    if (document.errors.length) return undefined
    if (document.contents === null) return []
    if (!isMap(document.contents)) return undefined
    const textOf = (node: unknown): string => {
      if (isScalar(node) && typeof node.value === 'string') return node.value.trimEnd()
      if (!isNode(node) || !node.range) return ''
      return source.slice(node.range[0], node.range[1]).trimEnd()
    }
    return document.contents.items.map(({ key, value }) => ({ key: textOf(key), value: textOf(value) }))
  } catch { return undefined }
}

export default function DocumentMarkdown({ text }: { text: string }) {
  const front = useMemo(() => splitFrontmatter(text), [text])
  const fields = useMemo(() => front ? frontmatterFields(front.source) : undefined, [front])
  return <>
    {front && (fields === undefined ? <pre className={css.frontmatterRaw}>{front.source}</pre> :
      fields.length > 0 && <dl className={css.frontmatter} aria-label="Document properties">
        {fields.map((field, index) => <div key={index}><dt>{field.key}</dt><dd>{field.value}</dd></div>)}
      </dl>)}
    <Markdown text={front?.body ?? text} />
  </>
}
