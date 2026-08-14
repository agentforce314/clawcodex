import { describe, expect, it } from 'vitest'

import type { ProjectNode, SessionRow } from '../gateway/protocol.ts'
import { filterProjects, matches, termsOf } from './filter.ts'

function row(id: string, title: string, preview = ''): SessionRow {
  return { id, preview, title }
}

function tree(...sessions: SessionRow[]): ProjectNode[] {
  return [
    {
      id: 'p1',
      label: 'clawcodex',
      lastActive: 2,
      path: '/repo',
      repos: [
        {
          groups: [{ id: 'l1', label: 'main', path: '/repo', sessions }],
          id: 'r1',
          label: 'clawcodex',
          path: '/repo',
        },
      ],
      sessionCount: sessions.length,
    },
  ]
}

const SESSIONS = [
  row('a', 'Fix the sidebar timestamps'),
  row('b', 'Use AskUserQuestion to ask a colour'),
  row('c', 'Untitled session', 'walk me through the gateway protocol'),
]

describe('termsOf', () => {
  it('splits on whitespace and lowercases', () => {
    expect(termsOf('  Fix   THE Sidebar ')).toEqual(['fix', 'the', 'sidebar'])
  })

  it('yields nothing for a blank query', () => {
    expect(termsOf('   ')).toEqual([])
  })
})

describe('matches', () => {
  it('requires every term, not just one', () => {
    // Sessions are named from a prompt's first line, so the useful query is a
    // couple of remembered fragments narrowing each other down.
    expect(matches(SESSIONS[0]!, ['fix', 'sidebar'])).toBe(true)
    expect(matches(SESSIONS[0]!, ['fix', 'gateway'])).toBe(false)
  })

  it('matches inside a word, not only at its start', () => {
    expect(matches(SESSIONS[1]!, ['userquestion'])).toBe(true)
  })

  it('searches the preview as well as the title', () => {
    // Rows titled "Untitled session" are otherwise unfindable.
    expect(matches(SESSIONS[2]!, ['gateway'])).toBe(true)
  })

  it('ignores case', () => {
    expect(matches(SESSIONS[0]!, ['SIDEBAR'.toLowerCase()])).toBe(true)
  })

  it('matches everything when there are no terms', () => {
    expect(matches(SESSIONS[0]!, [])).toBe(true)
  })
})

describe('filterProjects', () => {
  it('returns the input untouched for a blank query', () => {
    const projects = tree(...SESSIONS)

    expect(filterProjects(projects, '   ')).toBe(projects)
  })

  it('keeps only the matching sessions', () => {
    const [project] = filterProjects(tree(...SESSIONS), 'colour')

    expect(project?.repos[0]?.groups[0]?.sessions.map(s => s.id)).toEqual(['b'])
  })

  it('re-derives the header count from what survived', () => {
    // Otherwise the header advertises 73 sessions above a list showing one.
    const [project] = filterProjects(tree(...SESSIONS), 'colour')

    expect(project?.sessionCount).toBe(1)
  })

  it('drops a project whose sessions all failed to match', () => {
    // A header with nothing under it reads as a rendering failure.
    expect(filterProjects(tree(...SESSIONS), 'nothing-matches-this')).toEqual([])
  })

  it('drops an empty lane but keeps a sibling lane that matched', () => {
    const projects: ProjectNode[] = [
      {
        id: 'p1',
        label: 'clawcodex',
        path: '/repo',
        repos: [
          {
            groups: [
              { id: 'main', label: 'main', path: '/repo', sessions: [row('a', 'gateway work')] },
              { id: 'wt', label: 'feature', path: '/wt', sessions: [row('b', 'unrelated')] },
            ],
            id: 'r1',
            label: 'clawcodex',
            path: '/repo',
          },
        ],
      },
    ]

    const [project] = filterProjects(projects, 'gateway')

    expect(project?.repos[0]?.groups.map(lane => lane.id)).toEqual(['main'])
  })

  it('does not mutate the tree it was given', () => {
    const projects = tree(...SESSIONS)

    filterProjects(projects, 'colour')

    expect(projects[0]?.repos[0]?.groups[0]?.sessions).toHaveLength(3)
    expect(projects[0]?.sessionCount).toBe(3)
  })
})
