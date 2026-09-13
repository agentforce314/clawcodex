import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { classifyFile, FileTypeIcon } from './FileTypeIcon.tsx'

afterEach(cleanup)

describe('classifyFile', () => {
  it('reads the extension after the last dot', () => {
    expect(classifyFile('/repo/src/app.tsx')?.label).toBe('TSX')
    expect(classifyFile('notes.md')?.label).toBe('MD')
    expect(classifyFile('archive.tar.gz')?.label).toBe('ZIP')
  })

  it('knows whole names before extensions', () => {
    expect(classifyFile('/repo/Dockerfile')?.label).toBe('DKR')
    expect(classifyFile('.gitignore')?.label).toBe('GIT')
    expect(classifyFile('.env.local')?.label).toBe('ENV')
    expect(classifyFile('README')?.label).toBe('MD')
  })

  it('has no sheet for a kind it does not know', () => {
    expect(classifyFile('/repo/binary.xyz123')).toBeNull()
    expect(classifyFile('/repo/noext')).toBeNull()
  })
})

describe('FileTypeIcon', () => {
  it('draws a folder, a known file, and a generic sheet', () => {
    const { container } = render(
      <>
        <FileTypeIcon kind="folder" />
        <FileTypeIcon path="a.py" />
        <FileTypeIcon path="a.unknownkind" />
      </>,
    )
    const kinds = [...container.querySelectorAll('svg')].map(svg => svg.getAttribute('data-file-type'))

    expect(kinds).toEqual(['folder', 'PY', 'generic'])
    expect(container.querySelector('text')?.textContent).toBe('PY')
  })
})
