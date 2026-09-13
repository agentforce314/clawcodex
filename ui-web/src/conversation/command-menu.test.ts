import { describe, expect, it } from 'vitest'

import type { CommandEntry } from '../gateway/protocol.ts'
import { aliasOf, menuRows, rankRows, sectionRows } from './command-menu.ts'

const catalog: CommandEntry[] = [
  { description: 'Show available commands', name: '/help' },
  { description: 'Clear the conversation', name: '/clear' },
  { description: 'Switch the model', name: '/model' },
  { description: 'Set the output style', hint: '[<name>]', name: '/output-style' },
  { description: 'Compact the conversation to save context', name: '/compact' },
  { description: 'Enable plan mode or view the current session plan', hint: '[<description>]', name: '/plan' },
  { description: 'Set a completion condition', hint: '[<condition>]', name: '/goal' },
  { description: 'Run a skill', name: '/deploy', origin: 'local' },
]

describe('menuRows', () => {
  it('gives every built-in its face and keeps the catalog description and hint', () => {
    const rows = menuRows(catalog, false)
    const plan = rows.find(row => row.name === '/plan')

    expect(plan?.label).toBe('Plan')
    expect(plan?.icon).toBeDefined()
    expect(plan?.hint).toBe('[<description>]')
    expect(plan?.description).toBe('Enable plan mode or view the current session plan')
  })

  it('lists the image action only when the model can read one', () => {
    expect(menuRows(catalog, false).some(row => row.action === 'image')).toBe(false)
    expect(menuRows(catalog, true)[0]?.action).toBe('image')
  })

  it('leaves a skill with its own copy and no face', () => {
    const skill = menuRows(catalog, false).find(row => row.name === '/deploy')

    expect(skill?.label).toBeUndefined()
    expect(skill?.icon).toBeUndefined()
    expect(skill?.description).toBe('Run a skill')
  })
})

describe('aliasOf', () => {
  it('names the command beside a title that is not the name in another case', () => {
    const rows = menuRows(catalog, false)

    expect(aliasOf(rows.find(row => row.name === '/plan')!)).toBeUndefined()
    expect(aliasOf(rows.find(row => row.name === '/output-style')!)).toBe('output-style')
    expect(aliasOf(rows.find(row => row.name === '/deploy')!)).toBeUndefined()
  })
})

describe('sectionRows', () => {
  it('arranges Add then Commands in usage order, unlisted rows last in catalog order', () => {
    const sectioned = sectionRows(menuRows(catalog, true))

    expect(sectioned.map(row => [row.section, row.name])).toEqual([
      ['Add', '/image'],
      ['Add', '/plan'],
      ['Add', '/goal'],
      ['Commands', '/compact'],
      ['Commands', '/model'],
      ['Commands', '/clear'],
      ['Commands', '/help'],
      ['Commands', '/output-style'],
      ['Commands', '/deploy'],
    ])
  })
})

describe('rankRows', () => {
  const rows = menuRows(catalog, true)

  it('returns the rows unchanged for an empty query', () => {
    expect(rankRows(rows, '')).toEqual(rows)
  })

  it('lists prefix hits in catalog order', () => {
    expect(rankRows(rows, 'c').map(row => row.name)).toEqual(['/clear', '/compact'])
  })

  it('ranks subsequence hits by alignment, then catalog order', () => {
    // "ol" opens "output-style" on a word boundary, so it outranks the two
    // rows where both letters sit mid-word; those keep their catalog order.
    expect(rankRows(rows, 'ol').map(row => row.name)).toEqual(['/output-style', '/model', '/goal'])
  })

  it('matches the title as well as the name', () => {
    expect(rankRows(rows, 'output s').map(row => row.name)).toEqual(['/output-style'])
    expect(rankRows(rows, 'image').map(row => row.name)).toEqual(['/image'])
  })

  it('is case-insensitive and drops rows the query does not fit', () => {
    expect(rankRows(rows, 'HELP').map(row => row.name)).toEqual(['/help'])
    expect(rankRows(rows, 'zzz')).toEqual([])
  })
})
