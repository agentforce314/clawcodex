/** The `[Image #N]` chip shown in the composer for a pending image attachment.
 *
 *  Port of `formatImageRef` / the image half of `parseReferences` in
 *  `typescript/src/history.ts`. The backend mirrors the same pattern
 *  (`_IMAGE_REF_RE` in `agent_server.py`) because the chip is the contract, not
 *  decoration: at submit the backend drops any pending image whose chip is no
 *  longer in the text, which is what makes deleting the chip un-attach the
 *  image. Keep the two patterns in sync.
 *
 *  The chip stays in the prompt text that reaches the model — the reference
 *  leaves image refs inline and sends the bytes as separate content blocks
 *  (history.ts:79), so the model sees where in the message the image belongs. */
export const IMAGE_REF_RE = /\[Image #(\d+)\]/g

export function formatImageRef(id: number): string {
  return `[Image #${id}]`
}

/** Image ids still referenced by a chip in `text`. */
export function parseImageRefs(text: string): number[] {
  return [...text.matchAll(IMAGE_REF_RE)]
    .map(m => Number.parseInt(m[1] ?? '0', 10))
    .filter(id => id > 0)
}
