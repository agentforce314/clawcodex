import logo from '../assets/logo.png'

export interface BrandMarkProps {
  className?: string
  size?: number
}

/**
 * The ClawCodex mark, from clawcodex.app.
 *
 * An image rather than an inline SVG because the mark is pixel art with its
 * own palette — it is not a glyph that should inherit the surrounding ink, and
 * its rust-and-orange reads on both the light and the dark surface unchanged.
 *
 * Always paired with the "ClawCodex" wordmark or a heading, so it is
 * decorative to a screen reader and carries no alt text of its own.
 */
export function BrandMark({ className, size = 24 }: BrandMarkProps) {
  return (
    <img
      alt=""
      aria-hidden="true"
      className={className}
      draggable={false}
      height={size}
      src={logo}
      width={size}
    />
  )
}
