import { useStore } from '@nanostores/react'
import { useMemo, useState } from 'react'

import type { ProjectNode, SessionRow } from '../gateway/protocol.ts'
import { createSession, resumeSession } from '../state/actions.ts'
import { toggleSidebar } from '../state/layout.ts'
import { $connection, $projects, $sessionId, $storedSessionId, $workspace } from '../state/store.ts'
import { $themePreference, cycleTheme } from '../state/theme.ts'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ClawMarkIcon,
  GitBranchIcon,
  MonitorIcon,
  MoonIcon,
  PanelLeftIcon,
  PlusIcon,
  SunIcon,
} from '../ui/icons.tsx'
import css from './Sidebar.module.css'

export interface SidebarProps {
  collapsed: boolean
}

function relativeTime(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''

  const at = Date.parse(value)

  if (Number.isNaN(at)) return ''

  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000))

  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`

  return `${Math.floor(seconds / 86_400)}d`
}

function sessionLabel(row: SessionRow): string {
  const title = row.title ?? ''

  if (title.trim() !== '') return title

  const preview = row.preview ?? ''

  return preview.trim() === '' ? 'Untitled session' : preview.slice(0, 80)
}

function laneCount(project: ProjectNode): number {
  return project.repos.reduce((total, repo) => total + repo.groups.length, 0)
}

interface SessionListProps {
  activeId: string | null
  /** Runtime session id to suppress: a replay's fresh session duplicating a row. */
  hiddenId: string | null
  liveId: string | null
  project: ProjectNode
}

function SessionList({ activeId, hiddenId, liveId, project }: SessionListProps) {
  // Lane headers only earn their line when a repo actually has more than one
  // checkout — a single-worktree project would otherwise show a header that
  // repeats its own name.
  const showLanes = laneCount(project) > 1

  return (
    <>
      {project.repos.map(repo =>
        repo.groups.map(lane => (
          <div key={lane.id}>
            {showLanes && (
              <div className={css.laneLabel}>
                <GitBranchIcon size={11} />
                {lane.label}
              </div>
            )}
            {lane.sessions.filter(row => row.id !== hiddenId).map(row => (
              <button
                className={[css.sessionRow, row.id === activeId ? css.sessionActive : '']
                  .filter(Boolean)
                  .join(' ')}
                key={row.id}
                onClick={() => {
                  void resumeSession(row.id, lane.path ?? row.cwd ?? undefined)
                }}
                title={sessionLabel(row)}
                type="button"
              >
                {row.id === liveId && <span className={css.liveDot} />}
                <span className={css.sessionTitle}>{sessionLabel(row)}</span>
                <span className={css.sessionMeta}>{relativeTime(row.last_active)}</span>
              </button>
            ))}
          </div>
        )),
      )}
    </>
  )
}

/**
 * The session tree.
 *
 * Sessions are grouped by the repository they were started in, with a lane per
 * worktree — the grouping the backend already computes for the desktop app, so
 * both surfaces place a session in the same place. Collapsed, the column
 * becomes a rail of the three controls that still make sense without labels.
 */
export function Sidebar({ collapsed }: SidebarProps) {
  const projects = useStore($projects)
  const workspace = useStore($workspace)
  const liveId = useStore($sessionId)
  const activeId = useStore($storedSessionId)
  const connection = useStore($connection)
  const themePreference = useStore($themePreference)

  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({})

  const ordered = useMemo(
    () => [...projects].sort((a, b) => (b.lastActive ?? 0) - (a.lastActive ?? 0)),
    [projects],
  )

  // Resuming spawns a FRESH runtime session that replays a stored one, so the
  // tree lists both: the row the user clicked and an empty "Untitled session"
  // for the runtime. They are one conversation — show the row that has the
  // history, and suppress its runtime twin.
  const duplicateLiveId = liveId !== null && liveId !== activeId ? liveId : null

  const ThemeIcon =
    themePreference === 'light' ? SunIcon : themePreference === 'dark' ? MoonIcon : MonitorIcon

  const connectionClass =
    connection === 'open'
      ? css.connectionOk
      : connection === 'error'
        ? css.connectionError
        : connection === 'reconnecting' || connection === 'connecting'
          ? css.connectionWarn
          : ''

  return (
    <div className={[css.root, collapsed ? css.collapsed : ''].filter(Boolean).join(' ')}>
      <div className={css.logoRow}>
        {!collapsed && (
          <button
            className={css.brand}
            onClick={() => {
              void createSession({ cwd: workspace })
            }}
            title="New session"
            type="button"
          >
            <ClawMarkIcon size={22} />
            <span className={css.brandLabel}>ClawCodex</span>
          </button>
        )}
        <button
          className={css.iconButton}
          onClick={toggleSidebar}
          title={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          type="button"
        >
          <PanelLeftIcon size={collapsed ? 18 : 16} />
        </button>
      </div>

      <button
        className={css.newSession}
        onClick={() => {
          void createSession({ cwd: workspace })
        }}
        title="New session"
        type="button"
      >
        <PlusIcon size={16} />
        <span className={css.newSessionLabel}>New session</span>
      </button>

      <div className={css.region}>
        {!collapsed &&
          (ordered.length === 0 ? (
            <div className={css.empty}>No sessions yet. Start one above.</div>
          ) : (
            ordered.map(project => {
              const isCollapsed = collapsedProjects[project.id] === true

              return (
                <div className={css.project} key={project.id}>
                  <button
                    className={css.projectHeader}
                    onClick={() => {
                      setCollapsedProjects(current => ({
                        ...current,
                        [project.id]: !isCollapsed,
                      }))
                    }}
                    title={project.path ?? project.label}
                    type="button"
                  >
                    {isCollapsed ? <ChevronRightIcon size={12} /> : <ChevronDownIcon size={12} />}
                    <span className={css.projectLabel}>{project.label}</span>
                    <span className={css.projectCount}>{project.sessionCount ?? 0}</span>
                  </button>
                  {!isCollapsed && (
                    <SessionList
                      activeId={activeId}
                      hiddenId={duplicateLiveId}
                      liveId={liveId}
                      project={project}
                    />
                  )}
                </div>
              )
            })
          ))}
      </div>

      <div className={css.foot}>
        <button
          className={css.iconButton}
          onClick={cycleTheme}
          title={`Appearance: ${themePreference}`}
          type="button"
        >
          <ThemeIcon size={16} />
        </button>
        {!collapsed && (
          <>
            <span className={css.footSpacer} />
            <span className={[css.connection, connectionClass].filter(Boolean).join(' ')}>
              <span className={css.connectionDot} />
              {connection === 'open' ? 'Connected' : connection}
            </span>
          </>
        )}
      </div>
    </div>
  )
}
