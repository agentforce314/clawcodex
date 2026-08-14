import { useCallback, useEffect, useRef, useState } from 'react'

export interface CopyButtonProps {
  className?: string
  label?: string
  text: string
}

/**
 * Copy-to-clipboard with a two-second confirmation in place of a toast.
 *
 * `navigator.clipboard` is unavailable on insecure origins, which is exactly
 * where this app often runs (`http://127.0.0.1:…`), so the textarea fallback
 * is the normal path rather than a legacy one.
 */
export function CopyButton({ className, label = 'Copy', text }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback(async () => {
    try {
      if (navigator.clipboard?.writeText !== undefined) {
        await navigator.clipboard.writeText(text)
      } else {
        const scratch = document.createElement('textarea')
        scratch.value = text
        scratch.setAttribute('readonly', '')
        scratch.style.position = 'fixed'
        scratch.style.opacity = '0'
        document.body.append(scratch)
        scratch.select()
        document.execCommand('copy')
        scratch.remove()
      }

      setCopied(true)

      if (timer.current !== null) clearTimeout(timer.current)

      timer.current = setTimeout(() => {
        setCopied(false)
      }, 2000)
    } catch {
      /* clipboard denied: the affordance simply does not confirm */
    }
  }, [text])

  return (
    <button className={className} onClick={() => void copy()} type="button">
      {copied ? 'Copied' : label}
    </button>
  )
}
