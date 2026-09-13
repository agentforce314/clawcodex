/**
 * The workspace, one directory level at a time.
 *
 * The tree lists lazily: a level is fetched the first time it is opened and
 * kept afterwards, so collapsing and reopening costs nothing. It filters
 * nothing the backend returned — dotfiles included — because this is the
 * workspace as it is, and an entry that cannot be opened is shown as such
 * rather than hidden, so a directory is described whole.
 *
 * Clicking a file opens it in this same column; the tree never decides which
 * viewer draws it.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useMemo } from 'react'

import { $workspace } from '../state/store.ts'
import { FolderIcon, FolderOpenIcon, RefreshIcon } from '../ui/icons.tsx'
import { FileTypeIcon } from '../ui/primitives/FileTypeIcon.tsx'
import { directoryFailureLine } from './failure-line.ts'
import {
  $filesTree,
  childPath,
  orderEntries,
  reloadTree,
  startTree,
  toggleLevel,
  type FilesTreeState,
} from './files-store.ts'
import { openFile } from './store.ts'
import css from './FilesTree.module.css'

/** The workspace's own name — the final segment of its path. */
export function workspaceTitle(root: string): string {
  const segments = root.split(/[/\\]/).filter(Boolean)

  return segments.at(-1) ?? root
}

function Level({ path, tree }: { path: string; tree: FilesTreeState }) {
  const level = tree.levels[path]
  // Ordered once per listing rather than on every tree change: expanding one
  // directory would otherwise re-sort every other open level.
  const entries = useMemo(
    () => (level?.kind === 'ready' ? orderEntries(level.entries) : []),
    [level],
  )

  if (level === undefined || level.kind === 'loading') {
    return (
      <li className={css.note} data-files-row="loading">
        Reading…
      </li>
    )
  }

  if (level.kind === 'failed') {
    return (
      <li className={css.note} data-files-code={level.failure.code} data-files-row="failed">
        {directoryFailureLine(level.failure)}
      </li>
    )
  }

  return (
    <>
      {entries.length === 0 && (
        <li className={css.note} data-files-row="empty">
          Empty directory
        </li>
      )}
      {entries.map(entry => {
        const child = childPath(path, entry.name)

        if (entry.type === 'directory') {
          const expanded = tree.expanded.includes(child)

          return (
            <li className={css.item} data-files-entry="directory" key={entry.name}>
              <button
                aria-expanded={expanded}
                className={css.row}
                onClick={() => {
                  void toggleLevel(child)
                }}
                type="button"
              >
                {expanded ? <FolderOpenIcon size={14} /> : <FolderIcon size={14} />}
                <span className={css.name}>{entry.name}</span>
              </button>
              {expanded && (
                <ul className={css.level}>
                  <Level path={child} tree={tree} />
                </ul>
              )}
            </li>
          )
        }

        if (entry.type === 'file') {
          return (
            <li className={css.item} data-files-entry="file" key={entry.name}>
              <button
                className={css.row}
                onClick={() => {
                  openFile(child)
                }}
                type="button"
              >
                <FileTypeIcon path={child} size={14} />
                <span className={css.name}>{entry.name}</span>
              </button>
            </li>
          )
        }

        return (
          <li className={css.item} data-files-entry="other" key={entry.name}>
            <span
              aria-disabled="true"
              className={[css.row, css.other].join(' ')}
              title="Not a file or a directory, so it cannot be opened."
            >
              <span className={css.name}>{entry.name}</span>
            </span>
          </li>
        )
      })}
      {level.truncated && (
        <li className={css.note} data-files-row="truncated">
          Too many entries; showing only some of them.
        </li>
      )}
    </>
  )
}

export function FilesTree() {
  const workspace = useStore($workspace)
  const tree = useStore($filesTree)

  useEffect(() => {
    if (workspace !== '') void startTree(workspace)
  }, [workspace])

  if (workspace === '') {
    return (
      <div className={css.status} data-files-state="no-workspace">
        This session has no workspace directory.
      </div>
    )
  }

  if (tree.root !== workspace) return null

  return (
    <div className={css.root} data-files-state="tree">
      <div className={css.header}>
        <FolderOpenIcon size={14} />
        <span className={css.name}>{workspaceTitle(tree.root)}</span>
        <button
          aria-label="Reload"
          className={css.tool}
          onClick={() => {
            void reloadTree()
          }}
          title="Reload"
          type="button"
        >
          <RefreshIcon size={14} />
        </button>
      </div>
      <ul className={css.level}>
        <Level path={tree.root} tree={tree} />
      </ul>
    </div>
  )
}
