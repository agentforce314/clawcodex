import type { ReactNode } from 'react'

import { BrandMark } from '../ui/BrandMark.tsx'
import { WorkspaceChip } from '../workspace/WorkspaceChip.tsx'
import css from './HeroShell.module.css'

export interface HeroShellProps {
  composer: ReactNode
  onSuggestion?: (text: string) => void
}

/** Openers that show what the agent is for without pretending to know the repo. */
const SUGGESTIONS = [
  'Explain this codebase',
  'Find and fix a bug',
  'Add a test for the module I name',
  'Review my uncommitted changes',
]

/** The empty state: the mark, the workspace you are about to work in, the composer. */
export function HeroShell({ composer, onSuggestion }: HeroShellProps) {
  return (
    <div className={css.stack}>
      <div className={css.glow} />
      <div className={css.headline}>
        <BrandMark size={30} />
        ClawCodex
      </div>
      <div className={css.workspaceRow}>
        <WorkspaceChip variant="hero" />
      </div>
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
