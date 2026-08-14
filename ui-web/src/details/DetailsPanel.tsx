import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { shortPath } from '../conversation/tool-view.ts'
import { closeDetails } from '../state/layout.ts'
import { $contextUsage, $sessionId, $transcript, $workspace } from '../state/store.ts'
import { LayersIcon, XIcon } from '../ui/icons.tsx'
import css from './DetailsPanel.module.css'

const FILE_TOOLS = new Set(['edit_file', 'read_file', 'write_file'])

const VERBS: Record<string, string> = {
  edit_file: 'edited',
  read_file: 'read',
  write_file: 'wrote',
}

/**
 * What this session has actually done, beside the conversation.
 *
 * The transcript answers "what was said"; this column answers "what changed" —
 * which files the agent touched and which tools it leaned on. Both are derived
 * from the same nodes, so there is no second source of truth to drift.
 */
export function DetailsPanel() {
  const transcript = useStore($transcript)
  const workspace = useStore($workspace)
  const sessionId = useStore($sessionId)
  const usage = useStore($contextUsage)

  const files = useMemo(() => {
    // Last verb wins: a file read and then written should read as written.
    const seen = new Map<string, string>()

    for (const node of transcript.nodes) {
      if (node.kind !== 'tool' || !FILE_TOOLS.has(node.name)) continue
      // Only calls that actually landed: a refused write did not touch a file,
      // and listing it here would be a lie about the working tree.
      if (node.state !== 'done') continue

      const args = node.args
      const path =
        (typeof args.path === 'string' ? args.path : '') ||
        (typeof args.file_path === 'string' ? args.file_path : '') ||
        (typeof node.result?.path === 'string' ? node.result.path : '')

      if (path === '') continue

      const verb = VERBS[node.name] ?? 'used'

      if (verb === 'read' && seen.has(path)) continue

      seen.set(path, verb)
    }

    return [...seen.entries()]
  }, [transcript.nodes])

  const toolCounts = useMemo(() => {
    const counts = new Map<string, number>()

    for (const node of transcript.nodes) {
      if (node.kind !== 'tool') continue

      counts.set(node.name, (counts.get(node.name) ?? 0) + 1)
    }

    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [transcript.nodes])

  const info = transcript.info

  return (
    <div className={css.root}>
      <div className={css.header}>
        <LayersIcon size={16} />
        Session
        <span className={css.spacer} />
        <button className={css.iconButton} onClick={closeDetails} title="Close" type="button">
          <XIcon size={16} />
        </button>
      </div>
      <div className={css.body}>
        <section className={css.section}>
          <div className={css.sectionTitle}>Details</div>
          <dl className={css.facts}>
            <dt>Model</dt>
            <dd title={info.model}>{info.model ?? '—'}</dd>
            <dt>Provider</dt>
            <dd>{info.provider ?? '—'}</dd>
            {info.reasoning_effort !== undefined && (
              <>
                <dt>Effort</dt>
                <dd>{info.reasoning_effort}</dd>
              </>
            )}
            <dt>Approvals</dt>
            <dd>{info.approval_mode ?? '—'}</dd>
            <dt>Workspace</dt>
            <dd title={workspace}>{workspace === '' ? '—' : workspace}</dd>
            <dt>Session</dt>
            <dd title={sessionId ?? undefined}>{sessionId ?? 'not started'}</dd>
            {usage?.total_tokens !== undefined && (
              <>
                <dt>Context</dt>
                <dd>
                  {usage.total_tokens.toLocaleString()}
                  {usage.max_tokens !== undefined && ` / ${usage.max_tokens.toLocaleString()}`}
                </dd>
              </>
            )}
          </dl>
        </section>

        <section className={css.section}>
          <div className={css.sectionTitle}>Files touched</div>
          {files.length === 0 ? (
            <div className={css.empty}>No files touched yet.</div>
          ) : (
            <ul className={css.list}>
              {files.map(([path, verb]) => (
                <li className={css.fileRow} key={path}>
                  <span className={css.filePath} title={path}>
                    {shortPath(path, workspace)}
                  </span>
                  <span className={css.fileVerb}>{verb}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={css.section}>
          <div className={css.sectionTitle}>Tool runs</div>
          {toolCounts.length === 0 ? (
            <div className={css.empty}>No tools have run yet.</div>
          ) : (
            <ul className={css.list}>
              {toolCounts.map(([name, count]) => (
                <li className={css.toolRow} key={name}>
                  <span>{name}</span>
                  <span className={css.toolCount}>{count}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
