import type { ProjectNode, SessionRow } from '../gateway/protocol.ts'

/** What a row is searched by: its display name, then the prompt behind it. */
function haystack(row: SessionRow): string {
  return `${row.title ?? ''}\n${row.preview ?? ''}`.toLowerCase()
}

/**
 * Whether a row matches, by every whitespace-separated term.
 *
 * AND rather than OR, and substring rather than word-prefix: sessions are
 * named from the first line of a prompt, so the useful query is a couple of
 * remembered fragments ("askuser colour"), not one exact word.
 */
export function matches(row: SessionRow, terms: string[]): boolean {
  if (terms.length === 0) return true

  const text = haystack(row)

  return terms.every(term => text.includes(term))
}

/** Split a raw query into the terms `matches` tests. */
export function termsOf(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(term => term !== '')
}

/**
 * The tree with every non-matching session removed, and every project, repo
 * and lane that ends up empty removed with it.
 *
 * Pruning empties matters more than it sounds: a project header with a count
 * and nothing under it reads as "this project has matches, they just failed to
 * render". An empty query returns the input untouched — no copying, and no
 * chance of the filter altering what the unfiltered tree shows.
 */
export function filterProjects(projects: ProjectNode[], query: string): ProjectNode[] {
  const terms = termsOf(query)

  if (terms.length === 0) return projects

  const kept: ProjectNode[] = []

  for (const project of projects) {
    const repos = []

    for (const repo of project.repos) {
      const groups = []

      for (const lane of repo.groups) {
        const sessions = lane.sessions.filter(row => matches(row, terms))

        if (sessions.length > 0) groups.push({ ...lane, sessions })
      }

      if (groups.length > 0) repos.push({ ...repo, groups })
    }

    if (repos.length > 0) {
      // The count is re-derived rather than carried over: the header would
      // otherwise advertise 73 sessions above a list showing two.
      const sessionCount = repos.reduce(
        (total, repo) => total + repo.groups.reduce((sum, lane) => sum + lane.sessions.length, 0),
        0,
      )

      kept.push({ ...project, repos, sessionCount })
    }
  }

  return kept
}
