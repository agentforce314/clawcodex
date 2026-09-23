/**
 * The icon set, as inline SVG.
 *
 * Inline rather than a sprite or a font: every glyph inherits `currentColor`
 * and the surrounding font metrics, which is what lets a 16px row icon sit on
 * the text baseline without per-site nudging. All paths are drawn on a 24-unit
 * grid with a 2-unit stroke, so mixing sizes stays visually consistent.
 */

import type { SVGProps } from 'react'

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'children'> {
  size?: number
}

function Svg({ size = 16, ...props }: IconProps & { children: React.ReactNode }) {
  const { children, ...rest } = props

  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      viewBox="0 0 24 24"
      width={size}
      {...rest}
    >
      {children}
    </svg>
  )
}

export const PanelLeftIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect height="18" rx="2" width="18" x="3" y="3" />
    <path d="M9 3v18" />
  </Svg>
)

export const PlusIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
)

/** A delegation: one node handing work down to two. */
export const AgentIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="5" r="2.5" />
    <circle cx="6" cy="19" r="2.5" />
    <circle cx="18" cy="19" r="2.5" />
    <path d="M12 7.5v4M12 11.5 7 16.5M12 11.5l5 5" />
  </Svg>
)

/** Switch between siblings: two chevrons, up and down. */
export const SwitchIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m7 9 5-5 5 5M7 15l5 5 5-5" />
  </Svg>
)

export const FolderIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2Z" />
  </Svg>
)

export const ChevronDownIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m6 9 6 6 6-6" />
  </Svg>
)

export const ChevronRightIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m9 18 6-6-6-6" />
  </Svg>
)

export const ArrowUpIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 19V5M5 12l7-7 7 7" />
  </Svg>
)

export const ArrowDownIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M19 12l-7 7-7-7" />
  </Svg>
)

export const StopIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect fill="currentColor" height="10" rx="2" stroke="none" width="10" x="7" y="7" />
  </Svg>
)

export const BrainIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 3A3.5 3.5 0 0 0 6 6.5v.6A3 3 0 0 0 4 10a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3 2.5 2.5 0 0 0 2.5-2.5V5A2 2 0 0 0 9.5 3Z" />
    <path d="M14.5 3A3.5 3.5 0 0 1 18 6.5v.6A3 3 0 0 1 20 10a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3 2.5 2.5 0 0 1-2.5-2.5V5A2 2 0 0 1 14.5 3Z" />
  </Svg>
)

export const TerminalIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m4 17 6-5-6-5M12 19h8" />
  </Svg>
)

export const PaperclipIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </Svg>
)

export const FileTextIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
    <path d="M14 2v6h6M8 13h8M8 17h5" />
  </Svg>
)

export const FilePenIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12.5 22H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8l6 6v3" />
    <path d="M14 2v6h6" />
    <path d="M21.4 14.6a2 2 0 0 1 0 2.8L18 20.8l-3 .7.7-3 3.4-3.4a2 2 0 0 1 2.8 0Z" />
  </Svg>
)

export const SearchIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Svg>
)

export const GlobeIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18Z" />
  </Svg>
)

export const ListIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
  </Svg>
)

export const CheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5 13 4 4L19 7" />
  </Svg>
)

export const CircleIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
  </Svg>
)

export const AlertIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4M12 17h.01" />
  </Svg>
)

export const XIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Svg>
)

export const CopyIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect height="13" rx="2" width="13" x="9" y="9" />
    <path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />
  </Svg>
)

export const SunIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Svg>
)

export const MoonIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </Svg>
)

export const MonitorIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect height="14" rx="2" width="20" x="2" y="3" />
    <path d="M8 21h8M12 17v4" />
  </Svg>
)

export const SettingsIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
  </Svg>
)

export const MessageIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" />
  </Svg>
)

export const GitBranchIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3v12" />
    <circle cx="18" cy="6" r="3" />
    <circle cx="6" cy="18" r="3" />
    <path d="M18 9a9 9 0 0 1-9 9" />
  </Svg>
)

export const WrenchIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14.7 6.3a4 4 0 0 0 5 5l-9 9a2.8 2.8 0 0 1-4-4Z" />
  </Svg>
)

export const ShieldIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
  </Svg>
)

export const InfoIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 16v-4M12 8h.01" />
  </Svg>
)

export const HelpIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.3M12 17h.01" />
  </Svg>
)

export const LayersIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m12 3 9 5-9 5-9-5Z" />
    <path d="m3 13 9 5 9-5" />
  </Svg>
)

/* Permission-mode shields. One outline, three interiors: a check for "nothing
   happens without you", a pencil for "writes inside the workspace", an
   exclamation for "no checks at all". The shape stays constant so the
   interior is what the eye reads. */

const SHIELD = 'M12 2.2 20 5.2v5.1c0 6.4-4.6 9.4-8 10.7-3.4-1.3-8-4.3-8-10.7V5.2Z'

export const ShieldCheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d={SHIELD} />
    <path d="m8.6 11.4 2.3 2.3 4.5-4.6" />
  </Svg>
)

export const ShieldPenIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d={SHIELD} />
    <path d="M14.8 8.4 9.6 13.6l-1.4 2.6 2.6-1.4 5.2-5.2a1.2 1.2 0 0 0-1.2-1.2Z" />
  </Svg>
)

export const ShieldAlertIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d={SHIELD} />
    <path d="M12 7.6v4.6M12 15.4h.01" />
  </Svg>
)

/* ── the right sidebar and the stats pills ───────────────────────────────── */

export const FolderOpenIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1" />
    <path d="m3.5 20 2.6-7.3A1 1 0 0 1 7 12h13.4a1 1 0 0 1 .95 1.32L19 20Z" />
  </Svg>
)

export const RefreshIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20.5 9A8.5 8.5 0 0 0 5.6 6.6L3 9" />
    <path d="M3.5 15A8.5 8.5 0 0 0 18.4 17.4L21 15" />
    <path d="M3 4v5h5M21 20v-5h-5" />
  </Svg>
)

/** Wrap lines: a line that turns back on itself. */
export const WrapIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 6h18M3 18h7" />
    <path d="M3 12h14a3 3 0 0 1 0 6h-3" />
    <path d="m16 15-2.5 3 2.5 3" />
  </Svg>
)

/** Fill the frame. Four corners pushing outward. */
export const ExpandIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 3H3v6M21 9V3h-6M15 21h6v-6M3 15v6h6" />
  </Svg>
)

/** Back to the column. The same four corners, pulled in. */
export const CollapseIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 9h6V3M15 3v6h6M21 15h-6v6M9 21v-6H3" />
  </Svg>
)

/**
 * A dial: the run's counts and speeds. The needle is drawn from a centre
 * dropped below the arc's own, because a bottom-open arc reads high.
 */
export const GaugeIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 18a9 9 0 1 1 15 0" />
    <path d="m12 14.5 3.5-4" />
  </Svg>
)

/** Stacked platters: what the run was billed for. */
export const DatabaseIcon = (p: IconProps) => (
  <Svg {...p}>
    <ellipse cx="12" cy="5.5" rx="8" ry="3" />
    <path d="M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13" />
    <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
  </Svg>
)

/*
 * Composer-menu glyphs. One per command family the menu lists, on the same
 * 24-unit stroke grid as everything above so a row of them reads as one set.
 */

export const ImageIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect height="18" rx="2" ry="2" width="18" x="3" y="3" />
    <circle cx="9" cy="9" r="2" />
    <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
  </Svg>
)

/** A goal: concentric rings. */
export const TargetIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="10" />
    <circle cx="12" cy="12" r="6" />
    <circle cx="12" cy="12" r="2" />
  </Svg>
)

/** Compaction: two arrows folding toward a centre line. */
export const CompactIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 22v-6M12 8V2M4 12H2M10 12H8M16 12h-2M22 12h-2" />
    <path d="m15 19-3-3-3 3M15 5l-3 3-3-3" />
  </Svg>
)

export const TrashIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 6h18" />
    <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
    <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
    <path d="M10 11v6M14 11v6" />
  </Svg>
)

/** Rewind: an arrow curling back to the left. */
export const UndoIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 14 4 9l5-5" />
    <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11" />
  </Svg>
)

export const CoinsIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M18.09 10.37A6 6 0 1 1 10.34 18" />
    <path d="M7 6h1v4" />
    <path d="m16.71 13.88.7.71-2.82 2.82" />
  </Svg>
)

/** Effort: a bolt. */
export const ZapIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
  </Svg>
)

/** A model: the sparkle the model chip family uses. */
export const SparklesIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
    <path d="M20 3v4M22 5h-4M4 17v2M5 18H3" />
  </Svg>
)

export const LeafIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z" />
    <path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12" />
  </Svg>
)

export const BookIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 7v14" />
    <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" />
  </Svg>
)

/** A slash command with no glyph of its own: a slash in a square. */
export const SlashSquareIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect height="18" rx="2" width="18" x="3" y="3" />
    <path d="m9 15 6-6" />
  </Svg>
)

/** The guide's placeholder cube: an isometric box. */
export const CubeIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m21 16-9 5-9-5V8l9-5 9 5z" />
    <path d="m3 8 9 5 9-5M12 13v8" />
  </Svg>
)

/** A compass: the guide page's mark. */
export const CompassIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="10" />
    <path d="m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z" />
  </Svg>
)
