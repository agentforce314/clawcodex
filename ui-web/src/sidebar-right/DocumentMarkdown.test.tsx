import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import DocumentMarkdown, { frontmatterFields, splitFrontmatter } from './DocumentMarkdown.tsx'

afterEach(cleanup)
it('renders YAML mappings as metadata and leaves the Markdown body intact', () => {
  render(<DocumentMarkdown text={'---\ntitle: A report\ntags: [one, two]\n---\n# Body'} />)
  expect(screen.getByRole('heading', { name: 'Body' })).toBeTruthy()
  expect(screen.getByText('A report')).toBeTruthy()
  expect(screen.getByText('[one, two]')).toBeTruthy()
})
it('keeps invalid, non-mapping, and unclosed YAML readable', () => {
  expect(frontmatterFields('title: [invalid')).toBeUndefined()
  expect(frontmatterFields('- one\n- two')).toBeUndefined()
  expect(splitFrontmatter('---\ntitle: unfinished')).toBeUndefined()
  render(<DocumentMarkdown text={'---\ntitle: [invalid\n---\nBody'} />)
  expect(screen.getByText('title: [invalid')).toBeTruthy()
})
it('does not expand aliases, and preserves authored numbers', () => {
  expect(frontmatterFields('value: 1.0\nlist: &items [one, two]\ncopy: *items')).toEqual([
    { key: 'value', value: '1.0' }, { key: 'list', value: '[one, two]' }, { key: 'copy', value: '*items' },
  ])
})
