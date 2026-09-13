/**
 * The sentence one refused read deserves — in terms of the file or the
 * directory, never of the transport.
 *
 * Apart from the components so the mapping is testable on its own, and split in
 * two because the same code means different things to a reader looking at a
 * file and a reader looking at a folder. A code neither list names falls to the
 * generic line, carrying the backend's own message: the panel has nothing
 * useful to add to a transport-level failure.
 */

import type { WorkspaceFileFailure } from '../gateway/protocol.ts'

/** A byte count the way a person reads one. */
export function humanBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`

  return `${bytes} B`
}

/**
 * @param whole - the read was of the file entire (an image, a PDF, an HTML
 * document), so a size refusal is about the file rather than a page of it.
 */
export function fileFailureLine(failure: WorkspaceFileFailure, whole = false): string {
  switch (failure.code) {
    case 'workspace-file/not-found':
      return 'That file is gone. It may have been moved or deleted.'
    case 'workspace-file/outside-workspace':
      return 'That file is outside the workspace, so the sidebar will not read it.'
    case 'workspace-file/too-large': {
      const limit = humanBytes(failure.details?.limit ?? 0)

      return whole
        ? `That file is too large; the sidebar does not read files above ${limit}.`
        : `That page is too large; the sidebar does not read pages above ${limit}.`
    }
    case 'workspace-file/not-text':
      return 'That is not a text file, so it cannot be shown here.'
    case 'workspace-file/not-regular-file':
      return 'That is not a regular file, so it has no text to show.'
    case 'workspace-file/unknown-workspace':
      return 'This session has no workspace directory.'
    default:
      return `Read failed: ${failure.message}`
  }
}

export function directoryFailureLine(failure: WorkspaceFileFailure): string {
  switch (failure.code) {
    case 'workspace-file/not-found':
      return 'That directory is gone. It may have been moved or deleted.'
    case 'workspace-file/outside-workspace':
      return 'That directory is outside the workspace, so the sidebar will not read it.'
    case 'workspace-file/not-directory':
      return 'That is not a directory.'
    case 'workspace-file/unknown-workspace':
      return 'This session has no workspace directory.'
    default:
      return `Read failed: ${failure.message}`
  }
}
