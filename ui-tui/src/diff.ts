/**
 * Build the diff payload for an Edit/Write/MultiEdit tool call as
 * StructuredPatchHunk[] — the exact shape openclaude's StructuredDiff renderer
 * consumes. Mirrors typescript/src/components/FileEditToolDiff.tsx#loadDiffData:
 *
 *   - when the on-disk file is available, diff the WHOLE file with the edit(s)
 *     applied → structuredPatch emits hunks with true file line numbers and 3
 *     lines of surrounding context (no hand-rolled context window);
 *   - otherwise fall back to diffing the tool inputs only (old_string as the
 *     "file"), numbered from line 1, exactly like upstream diffToolInputsOnly.
 */
import {
  getPatchForDisplay,
  getPatchFromContents,
  type FileEdit,
  type StructuredPatchHunk,
} from './patch.js'

export interface ToolDiff {
  /**
   * 'edit'  → render a +/- StructuredDiff from `hunks` (Edit/MultiEdit, or a
   *           Write that overwrites a file we can read pre-write).
   * 'write' → render the full new `content` syntax-highlighted (ColorFile), the
   *           way the original shows a created file (no +/- markers).
   */
  kind: 'edit' | 'write'
  hunks: StructuredPatchHunk[]
  /** kind==='write': the full new file content (rendered via ColorFile). */
  content?: string
  /** First line of the file (shebang detection for syntax highlighting). */
  firstLine: string | null
  /** Full file content, when available — passed to ColorDiff for context. */
  fileContent?: string
  filePath: string
  /** Display verb for the tool-use line: Update / Create / Write. */
  displayName: string
}

function firstLineOf(s: string): string | null {
  return s.split('\n', 1)[0] ?? null
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/**
 * Build a ToolDiff from a tool call's raw input. `fileContent` (the on-disk
 * file, read best-effort by the caller) yields true file line numbers; without
 * it we diff the inputs region-relative.
 */
export function buildToolDiff(
  toolName: string,
  input: Record<string, unknown>,
  fileContent?: string,
): ToolDiff | null {
  const filePath = str(input['file_path']) || str(input['path']) || ''

  if (toolName === 'Write') {
    const content = str(input['content'])
    // If we can read the pre-write file AND it differs, show a real diff
    // (the original's "update" path); otherwise show the new content
    // highlighted (the "create" path). The file-not-yet-written and
    // file-unreadable cases both fall to 'write'.
    if (fileContent !== undefined && fileContent !== content && content) {
      const hunks = getPatchFromContents({ filePath, oldContent: fileContent, newContent: content })
      if (hunks.length) {
        return {
          kind: 'edit',
          hunks,
          firstLine: firstLineOf(content),
          fileContent: content,
          filePath,
          displayName: 'Update',
        }
      }
    }
    if (!content) return null
    return {
      kind: 'write',
      hunks: [],
      content,
      firstLine: firstLineOf(content),
      filePath,
      displayName: 'Write',
    }
  }

  if (toolName === 'Edit') {
    const old_string = str(input['old_string'])
    const new_string = str(input['new_string'])
    if (!old_string && !new_string) return null
    const replace_all = input['replace_all'] === true
    const edit: FileEdit = { old_string, new_string, replace_all }
    const displayName = old_string === '' ? 'Create' : 'Update'
    if (fileContent && fileContent.includes(old_string)) {
      const hunks = getPatchForDisplay({ filePath, fileContents: fileContent, edits: [edit] })
      if (!hunks.length) return null
      return { kind: 'edit', hunks, firstLine: firstLineOf(fileContent), fileContent, filePath, displayName }
    }
    // inputs-only: treat old_string as the whole "file"
    const hunks = getPatchForDisplay({ filePath, fileContents: old_string, edits: [edit] })
    if (!hunks.length) return null
    return { kind: 'edit', hunks, firstLine: null, filePath, displayName }
  }

  if (toolName === 'MultiEdit') {
    const raw = Array.isArray(input['edits']) ? (input['edits'] as Record<string, unknown>[]) : []
    const edits: FileEdit[] = raw.map(e => ({
      old_string: str(e['old_string']),
      new_string: str(e['new_string']),
      replace_all: e['replace_all'] === true,
    }))
    if (!edits.length) return null
    if (fileContent) {
      const hunks = getPatchForDisplay({ filePath, fileContents: fileContent, edits })
      if (hunks.length) {
        return { kind: 'edit', hunks, firstLine: firstLineOf(fileContent), fileContent, filePath, displayName: 'Update' }
      }
    }
    // inputs-only: one patch per edit, old_string as its "file"
    const hunks = edits.flatMap(e =>
      getPatchForDisplay({ filePath, fileContents: e.old_string, edits: [e] }),
    )
    if (!hunks.length) return null
    return { kind: 'edit', hunks, firstLine: null, filePath, displayName: 'Update' }
  }

  return null
}
