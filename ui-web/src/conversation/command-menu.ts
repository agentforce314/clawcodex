/**
 * The composer's command menu: what the `+` button and a typed `/` both open.
 *
 * Adapted from the reference client's `/` menu (its `presentation.ts` and
 * shared `rankByName`): an empty query lists an **Add** section and a
 * **Commands** section in usage order, each row with a glyph, a title, the
 * command name as an alias where the title differs from it, and the catalog's
 * own description; a typed query ranks every row by a case-insensitive ordered
 * subsequence of the name or the title, prefix hits first.
 *
 * Pure: the catalog comes in, rows go out. The view decides what a pick does.
 */

import type { ComponentType } from 'react'

import type { CommandEntry } from '../gateway/protocol.ts'
import {
  AgentIcon,
  BookIcon,
  BrainIcon,
  CoinsIcon,
  CompactIcon,
  DatabaseIcon,
  FilePenIcon,
  GaugeIcon,
  HelpIcon,
  ImageIcon,
  InfoIcon,
  LayersIcon,
  LeafIcon,
  ListIcon,
  MessageIcon,
  MonitorIcon,
  RefreshIcon,
  ShieldIcon,
  SparklesIcon,
  SwitchIcon,
  TargetIcon,
  TrashIcon,
  UndoIcon,
  WrenchIcon,
  ZapIcon,
  type IconProps,
} from '../ui/icons.tsx'

export interface MenuRow {
  /** Identity: the pick payload and the first search key — `/name`. */
  readonly name: string
  /** Display title; the bare name when absent. */
  readonly label?: string
  readonly description?: string
  /** Argument hint from the catalog, for a command that takes one. */
  readonly hint?: string
  readonly icon?: ComponentType<IconProps>
  /** Heading shared by adjacent rows; only the empty query has sections. */
  readonly section?: string
  /** A client-side action rather than a command: picking it runs the action. */
  readonly action?: 'image'
}

/** The one row that is not a command: the image picker, listed under Add. */
export const IMAGE_ROW: MenuRow = {
  action: 'image',
  description: 'Attach an image',
  icon: ImageIcon,
  label: 'Image',
  name: '/image',
}

/** Row names per section, highest usage first; the rest close Commands in catalog order. */
const SECTION_ROWS = {
  add: ['/image', '/plan', '/goal'],
  commands: [
    '/compact',
    '/permissions',
    '/model',
    '/clear',
    '/context',
    '/cost',
    '/rewind',
    '/effort',
    '/thinking',
    '/provider',
  ],
} as const

/** The client face of a built-in command: its title and glyph. */
const FACES: ReadonlyMap<string, { label: string; icon: ComponentType<IconProps> }> = new Map([
  ['/plan', { icon: FilePenIcon, label: 'Plan' }],
  ['/goal', { icon: TargetIcon, label: 'Goal' }],
  ['/subgoal', { icon: TargetIcon, label: 'Subgoal' }],
  ['/compact', { icon: CompactIcon, label: 'Compact' }],
  ['/permissions', { icon: ShieldIcon, label: 'Permissions' }],
  ['/model', { icon: SparklesIcon, label: 'Model' }],
  ['/provider', { icon: SwitchIcon, label: 'Provider' }],
  ['/clear', { icon: TrashIcon, label: 'Clear' }],
  ['/context', { icon: GaugeIcon, label: 'Context' }],
  ['/cost', { icon: CoinsIcon, label: 'Cost' }],
  ['/rewind', { icon: UndoIcon, label: 'Rewind' }],
  ['/effort', { icon: ZapIcon, label: 'Effort' }],
  ['/thinking', { icon: BrainIcon, label: 'Thinking' }],
  ['/help', { icon: HelpIcon, label: 'Help' }],
  ['/output-style', { icon: ListIcon, label: 'Output style' }],
  ['/logo', { icon: MonitorIcon, label: 'Logo' }],
  ['/eco', { icon: LeafIcon, label: 'Eco' }],
  ['/advisor', { icon: MessageIcon, label: 'Advisor' }],
  ['/fusion', { icon: LayersIcon, label: 'Fusion' }],
  ['/vision', { icon: ImageIcon, label: 'Vision' }],
  ['/workflows', { icon: LayersIcon, label: 'Workflows' }],
  ['/knowledge', { icon: BookIcon, label: 'Knowledge' }],
  ['/memory', { icon: DatabaseIcon, label: 'Memory' }],
  ['/skills', { icon: WrenchIcon, label: 'Skills' }],
  ['/loop', { icon: RefreshIcon, label: 'Loop' }],
  ['/insights', { icon: InfoIcon, label: 'Insights' }],
  ['/bg', { icon: AgentIcon, label: 'Background agents' }],
  ['/resume', { icon: MessageIcon, label: 'Resume' }],
  ['/rename', { icon: FilePenIcon, label: 'Rename' }],
])

/** The name without its slash — what titles and aliases show. */
export function bareName(name: string): string {
  return name.startsWith('/') ? name.slice(1) : name
}

/**
 * The command name beside a title that is not the name in another letter
 * case, so a row titled "Output style" still says what to type.
 */
export function aliasOf(row: MenuRow): string | undefined {
  if (row.label === undefined) return undefined

  const bare = bareName(row.name)

  return row.label.toLowerCase() === bare.toLowerCase() ? undefined : bare
}

/**
 * Every row the menu can list: the catalog, each built-in with its face, and
 * the image action first when the session's model can read one.
 */
export function menuRows(commands: readonly CommandEntry[], vision: boolean): MenuRow[] {
  const rows: MenuRow[] = vision ? [IMAGE_ROW] : []

  for (const command of commands) {
    const face = FACES.get(command.name)

    rows.push({
      name: command.name,
      ...(face === undefined ? {} : { icon: face.icon, label: face.label }),
      ...(command.description === '' ? {} : { description: command.description }),
      ...(command.hint === undefined ? {} : { hint: command.hint }),
    })
  }

  return rows
}

/**
 * Arrange the empty-query menu: the Add section, then the Commands section,
 * each in usage order, with unlisted rows closing Commands in catalog order.
 */
export function sectionRows(rows: readonly MenuRow[]): MenuRow[] {
  const listed = new Set<string>([...SECTION_ROWS.add, ...SECTION_ROWS.commands])
  const byName = new Map(rows.map(row => [row.name, row]))
  const pick = (names: readonly string[]): MenuRow[] =>
    names.flatMap(name => {
      const row = byName.get(name)

      return row === undefined ? [] : [row]
    })

  const add = pick(SECTION_ROWS.add).map(row => ({ ...row, section: 'Add' }))
  const commands = [...pick(SECTION_ROWS.commands), ...rows.filter(row => !listed.has(row.name))].map(
    row => ({ ...row, section: 'Commands' }),
  )

  return [...add, ...commands]
}

/**
 * Alignment score of `query` as an ordered subsequence of `name`: the sum of
 * a bonus for each matched character, higher for characters that continue a
 * run or start a word. `undefined` when the query is not a subsequence.
 */
function alignmentScore(name: string, query: string): number | undefined {
  let score = 0
  let from = 0
  let previous = -2

  for (const char of query) {
    const at = name.indexOf(char, from)

    if (at === -1) return undefined

    const boundary = at === 0 || name[at - 1] === '-' || name[at - 1] === '_' || name[at - 1] === ' '

    score += at === previous + 1 ? 3 : boundary ? 2 : 1
    previous = at
    from = at + 1
  }

  return score
}

/**
 * Rank rows by a typed query: prefix hits first, then the strongest alignment
 * over the name or the title, then catalog order. The rows themselves for an
 * empty query.
 */
export function rankRows(rows: readonly MenuRow[], rawQuery: string): MenuRow[] {
  const query = rawQuery.toLowerCase()

  if (query === '') return [...rows]

  const ranked: { row: MenuRow; index: number; prefix: boolean; score: number }[] = []

  rows.forEach((row, index) => {
    const keys = row.label === undefined ? [bareName(row.name)] : [bareName(row.name), row.label]
    let prefix = false
    let score: number | undefined

    for (const key of keys) {
      const lower = key.toLowerCase()
      const keyScore = alignmentScore(lower, query)

      if (keyScore === undefined) continue

      prefix ||= lower.startsWith(query)
      score = score === undefined ? keyScore : Math.max(score, keyScore)
    }

    if (score !== undefined) ranked.push({ index, prefix, row, score })
  })

  ranked.sort(
    (left, right) =>
      Number(right.prefix) - Number(left.prefix) || right.score - left.score || left.index - right.index,
  )

  return ranked.map(entry => entry.row)
}
