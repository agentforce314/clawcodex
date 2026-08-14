import type { ReactNode } from 'react'

import { BrandMark } from '../ui/BrandMark.tsx'
import { FolderIcon } from '../ui/icons.tsx'
import css from './HeroShell.module.css'

export interface HeroShellProps {
  composer: ReactNode
  onSuggestion?: (text: string) => void
  workspace?: string
}

/** Openers that show what the agent is for without pretending to know the repo. */
const SUGGESTIONS = [
  'Explain this codebase',
  'Find and fix a bug',
  'Add a test for the module I name',
  'Review my uncommitted changes',
]

/** The last two path segments — enough to identify a checkout, short enough to fit. */
function shortWorkspace(path: string): string {
  const segments = path.split(/[/\\]/).filter(Boolean)

  return segments.length <= 2 ? path : segments.slice(-2).join('/')
}

/** The empty state: the mark, the workspace you are about to work in, the composer. */
export function HeroShell({ composer, onSuggestion, workspace }: HeroShellProps) {
  return (
    <div className={css.stack}>
      <div className={css.glow} />
      <div className={css.headline}>
        <BrandMark size={30} />
        ClawCodex
      </div>
      {workspace !== undefined && workspace !== '' && (
        <div className={css.workspaceRow}>
          <span className={css.workspace} title={workspace}>
            <FolderIcon className={css.folder} size={14} />
            <span className={css.workspaceLabel}>{shortWorkspace(workspace)}</span>
          </span>
        </div>
      )}
      {composer}
      {onSuggestion !== undefined && (
        <div className={css.suggestions}>
          {SUGGESTIONS.map(suggestion => (
            <button
              className={css.suggestion}
              key={suggestion}
              onClick={() => {
                onSuggestion(suggestion)
              }}
              type="button"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
