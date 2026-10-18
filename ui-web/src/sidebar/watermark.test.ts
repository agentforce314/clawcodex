import { expect, it } from 'vitest'
import { sessionWatermark } from './watermark.ts'

const rows = Array.from({ length: 80 }, (_, n) => ({ id: String(n), title: `Session ${n}` }))
it('reveals a contiguous prefix for active sessions and never shrinks on updates', () => {
  expect(sessionWatermark(rows, 5, '8')).toBe(9)
  expect(sessionWatermark(rows, 9, '0')).toBe(9)
  expect(sessionWatermark(rows, 5, '60')).toBe(5)
  expect(sessionWatermark(rows, 65, '60')).toBe(65)
})
