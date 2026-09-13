/**
 * An HTML document packed for its own frame.
 *
 * Ported from the reference's HTML preview. The document is shown in an iframe
 * whose origin is opaque (`sandbox="allow-scripts"`, never `allow-same-origin`),
 * so its scripts can reach neither this app's origin nor the gateway. That
 * frame cannot load resource URLs the parent creates either, which is why the
 * stylesheets and scripts the document declares are read *here*, under the
 * document's own path, and handed over as text inside a bootstrap document
 * that rebuilds them as Blob URLs on the inside.
 *
 * Only the finite, statically declared set is packed: relative `.css`
 * stylesheets and classic `.js` scripts. Module imports, CSS `url()` and
 * `@import`, images and runtime fetches are left to the browser, which cannot
 * resolve a relative one from a Blob document — so they stay blank, honestly.
 * A `<base href>` hands every resolution to the browser and packs nothing.
 */

export interface HtmlAsset {
  kind: 'script' | 'stylesheet'
  /** The attribute as written in the document, which is what gets rewritten. */
  reference: string
  text: string
}

export interface HtmlBundle {
  assets: HtmlAsset[]
  html: string
}

/** Read one dependency relative to the document; a failure rejects. */
export type ReadRelative = (reference: string, signal: AbortSignal) => Promise<Uint8Array>

/** The reference's own limits: per asset, for the whole package, and in count. */
export const MAX_ASSET_BYTES = 4 * 1024 * 1024
export const MAX_TOTAL_BYTES = 32 * 1024 * 1024
export const MAX_ASSETS = 64

const BASE64_CHUNK = 0x8000

/** UTF-8 text from bytes; invalid sequences throw rather than pass as text. */
export function decodeText(data: Uint8Array): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(data)
}

/** Base64 of a text's UTF-8 bytes, for the bootstrap's payload. */
export function encodeBase64(text: string): string {
  const bytes = new TextEncoder().encode(text)
  const chunks: string[] = []

  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK)))
  }

  return btoa(chunks.join(''))
}

/**
 * Whether a reference names something beside the document: not a URL with a
 * scheme, not an absolute path, not a fragment or a query alone.
 */
export function isRelativeReference(reference: string): boolean {
  return (
    reference.length > 0 &&
    !/^(?:[a-z][a-z\d+.-]*:|[/\\#?])/i.test(reference) &&
    !reference.includes('\0')
  )
}

/**
 * The file path a reference names, with its query and fragment removed and
 * its percent-encoding undone. Throws for anything that is not a plain
 * relative path, so the backend is never asked for one.
 */
export function referencePath(reference: string): string {
  const suffix = reference.search(/[?#]/)
  const path = decodeURIComponent(suffix === -1 ? reference : reference.slice(0, suffix))

  if (
    path.length === 0 ||
    /^(?:[a-z][a-z\d+.-]*:|[/\\])/i.test(path) ||
    path.includes('\0') ||
    path.includes('\\')
  ) {
    throw new Error('An HTML dependency must be a relative file path')
  }

  return path
}

/**
 * Collect the document's static dependencies without mounting any of it in
 * this page: the markup is parsed into an inert template.
 */
export async function packHtml(
  data: Uint8Array,
  readRelative: ReadRelative,
  signal: AbortSignal,
): Promise<HtmlBundle> {
  signal.throwIfAborted()

  let total = data.byteLength

  if (total > MAX_TOTAL_BYTES) throw new Error('The HTML package exceeds its total byte limit')

  const html = decodeText(data)
  const template = document.createElement('template')

  template.innerHTML = html

  const assets: HtmlAsset[] = []

  if (template.content.querySelector('base[href]') !== null) return { assets, html }

  const seen = new Set<string>()

  for (const element of template.content.querySelectorAll('script[src], link[href]')) {
    const script = element.localName === 'script'
    const type = element.getAttribute('type')?.trim().toLowerCase() ?? ''

    if (script && !['', 'text/javascript', 'application/javascript'].includes(type)) continue

    if (
      !script &&
      !(element.getAttribute('rel') ?? '').toLowerCase().split(/\s+/).includes('stylesheet')
    ) {
      continue
    }

    // The selector required the attribute, so it is present.
    const reference = element.getAttribute(script ? 'src' : 'href') ?? ''
    const suffix = reference.search(/[?#]/)
    const path = suffix === -1 ? reference : reference.slice(0, suffix)

    if (!isRelativeReference(reference) || !(script ? /\.js$/i : /\.css$/i).test(path)) continue

    const kind = script ? 'script' : 'stylesheet'
    const key = `${kind}:${reference}`

    if (seen.has(key)) continue
    if (assets.length >= MAX_ASSETS) throw new Error('The HTML package exceeds its asset count limit')

    signal.throwIfAborted()

    const asset = await readRelative(reference, signal)

    signal.throwIfAborted()

    if (asset.byteLength > MAX_ASSET_BYTES) throw new Error('An HTML asset exceeds its byte limit')

    total += asset.byteLength

    if (total > MAX_TOTAL_BYTES) throw new Error('The HTML package exceeds its total byte limit')

    assets.push({ kind, reference, text: decodeText(asset) })
    seen.add(key)
  }

  return { assets, html }
}

/**
 * The outer document the frame navigates to. It carries the bundle as one
 * base64 payload and, inside the sandbox, rewrites each packed reference to a
 * Blob URL of its own origin before writing the document — because that
 * opaque origin cannot load resource URLs created out here.
 */
export function createHtmlDocument(bundle: HtmlBundle): string {
  const payload = encodeBase64(JSON.stringify(bundle))

  return `<!doctype html><meta charset="utf-8"><script>(()=>{
const bytes=data=>Uint8Array.from(atob(data),character=>character.charCodeAt(0));
const text=data=>new TextDecoder('utf-8',{fatal:true}).decode(bytes(data));
const bundle=JSON.parse(text("${payload}"));
let html=bundle.html;
if(bundle.assets.length){
  const parsed=new DOMParser().parseFromString(html,'text/html');
  for(const asset of bundle.assets){
    const script=asset.kind==='script';
    const url=URL.createObjectURL(new Blob([asset.text],{type:script?'application/javascript':'text/css'}));
    const attribute=script?'src':'href';
    for(const element of parsed.querySelectorAll(script?'script[src]':'link[rel~="stylesheet" i][href]')){
      if(element.getAttribute(attribute)===asset.reference)element.setAttribute(attribute,url);
    }
  }
  html='<!doctype html>'+parsed.documentElement.outerHTML;
}
document.open();document.write(html);document.close();
})()</script>`
}
