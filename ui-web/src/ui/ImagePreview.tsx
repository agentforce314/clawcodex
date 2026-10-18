import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import css from './ImagePreview.module.css'

/** Accessible full-size image inspection, shared by sent attachments and Markdown images. */
export function ExpandableImage({ src, alt, title, className }: { src: string; alt: string; title?: string; className?: string }) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className={css.thumbnail} aria-label={`Expand image: ${alt || 'image'}`} onClick={() => setOpen(true)}>
      <img alt={alt} className={className} src={src} title={title} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
    </button>
    {open && <ImageDialog src={src} alt={alt} onClose={() => setOpen(false)} />}
  </>
}

function ImageDialog({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [actual, setActual] = useState(false)
  useEffect(() => {
    const previous = document.activeElement
    dialog.current?.showModal()
    return () => { if (previous instanceof HTMLElement) previous.focus() }
  }, [])
  return createPortal(<dialog ref={dialog} aria-label={`Image preview: ${alt || 'image'}`} className={css.dialog}
    onCancel={event => { event.preventDefault(); onClose() }} onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className={css.toolbar}>
      <span>{alt}</span>
      <button type="button" onClick={() => setActual(value => !value)}>{actual ? 'Fit image' : 'Actual size'}</button>
      <button type="button" onClick={onClose} aria-label="Close image preview">Close</button>
    </div>
    <div className={css.viewport}><img className={actual ? css.actual : css.fit} src={src} alt={alt} referrerPolicy="no-referrer" /></div>
  </dialog>, document.body)
}

/** A linked image loads its thumbnail only when the link is hovered or focused. */
export function ImageLink({ href, title, children }: { href: string; title?: string; children: ReactNode }) {
  const [preview, setPreview] = useState(false)
  return <span className={css.link} onMouseEnter={() => setPreview(true)} onMouseLeave={() => setPreview(false)}>
    <a href={href} title={title} target="_blank" rel="noopener noreferrer"
      onFocus={() => setPreview(true)} onBlur={() => setPreview(false)}>{children}</a>
    {preview && <span className={css.hoverPreview}><img src={href} alt="Linked image preview" referrerPolicy="no-referrer" /></span>}
  </span>
}
