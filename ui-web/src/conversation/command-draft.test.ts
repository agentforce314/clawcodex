import { expect, it } from 'vitest'
import { commandDraft } from './command-draft.ts'

const rows = [{ name: '/plan', label: 'Plan', hint: '[description]' }, { name: '/help' }]
it('claims only complete argument commands, preserving the raw submitted prefix', () => {
  expect(commandDraft('/plan fix the app', rows)).toMatchObject({ prefix: '/plan ', body: 'fix the app' })
  expect(commandDraft('/plan', rows)).toBeNull()
  expect(commandDraft('/unknown text', rows)).toBeNull()
  expect(commandDraft('/help text', rows)).toBeNull()
})
