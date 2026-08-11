import { describe, expect, it } from 'vitest'

import { extractToolErrorMessage, formatToolResultSummary } from './tool-result-summary'

describe('formatToolResultSummary', () => {
  it('unwraps wrapper payloads into structured key-value lines', () => {
    const summary = formatToolResultSummary({
      success: true,
      result: {
        data: {
          path: '/tmp/demo.txt',
          status: 'ok',
          lines_written: 12,
          checksum: 'abc123'
        }
      }
    })

    expect(summary).toContain('- Path: /tmp/demo.txt')
    expect(summary).toContain('- Status: ok')
    expect(summary).toContain('- Lines Written: 12')
    expect(summary).not.toContain('"path"')
  })

  it('summarizes object arrays as readable list items', () => {
    const summary = formatToolResultSummary([
      { title: 'First result', snippet: 'alpha preview text' },
      { title: 'Second result', status: 'cached' },
      { title: 'Third result', summary: 'more details' },
      { title: 'Fourth result', summary: 'line 4' },
      { title: 'Fifth result', summary: 'line 5' },
      { title: 'Sixth result', summary: 'line 6' },
      { title: 'Seventh result', summary: 'line 7' }
    ])

    expect(summary).toContain('- First result - alpha preview text')
    expect(summary).toContain('- Second result (cached)')
    expect(summary).toContain('- … 1 more item')
  })

  it('truncates long field values for compact display', () => {
    const summary = formatToolResultSummary({
      message: 'ok',
      details: `prefix ${'x'.repeat(500)}`
    })

    const detailsLine = summary.split('\n').find(line => line.startsWith('- Details:'))

    expect(detailsLine).toBeTruthy()
    expect(detailsLine?.length).toBeLessThan(230)
    expect(detailsLine).toContain('…')
  })

  it('formats stringified json payloads without raw dumps', () => {
    const summary = formatToolResultSummary(
      JSON.stringify({
        data: {
          title: 'Build report',
          completed: true
        }
      })
    )

    expect(summary).toContain('- Title: Build report')
    expect(summary).toContain('- Completed: true')
  })

  // TaskOutput is the one tool whose result is not JSON — it serializes to the
  // original's `<tag>value</tag>` part list. Before this was decoded, the whole
  // tag soup (including the full captured log) landed on the card.
  describe('TaskOutput tagged results', () => {
    const BUILD_LOG = 'EXIT=1\n#7 [internal] load build context\n#9 ERROR: exit code: 127'

    const tagged = [
      '<retrieval_status>success</retrieval_status>',
      '',
      '<task_id>baa0sty3d</task_id>',
      '',
      '<task_type>bash_background</task_type>',
      '',
      '<status>completed</status>',
      '',
      '<description>Build the sandbox image with CA support</description>',
      '',
      '<exit_code>0</exit_code>',
      '',
      `<output>\n${BUILD_LOG}\n</output>`
    ].join('\n')

    it('renders the same key-value lines the pre-tagged JSON did', () => {
      const summary = formatToolResultSummary(tagged)

      expect(summary).toContain('- Retrieval Status: success')
      expect(summary).toContain('Status: completed')
      expect(summary).toContain('Description: Build the sandbox image with CA support')
      expect(summary).not.toContain('<output>')
      expect(summary).not.toContain('</retrieval_status>')
    })

    it('keeps the stuck-task hint on the first line', () => {
      // The backend puts it first in both serializations for the same reason:
      // it is the one line the poll guard exists to surface. Building the
      // record with retrieval_status first quietly demoted it, because
      // formatRecordSummary renders in key order.
      const withHint = `<stuck_task_hint>[stuck-task guard] stop polling</stuck_task_hint>\n\n${tagged}`
      const summary = formatToolResultSummary(withHint)

      expect(summary.split('\n')[0]).toContain('Stuck Task Hint')
    })

    it('never renders the captured log on the card', () => {
      const noisy = tagged.replace(BUILD_LOG, `${'a'.repeat(300)}\nSECRETLINE\n${'b'.repeat(300)}`)

      expect(formatToolResultSummary(noisy)).not.toContain('SECRETLINE')
    })

    it('handles the task: null result', () => {
      expect(formatToolResultSummary('<retrieval_status>success</retrieval_status>')).toContain(
        '- Retrieval Status: success'
      )
    })

    it('leaves markup that is not a TaskOutput result alone', () => {
      // A fetched page or an XML file read must not be mistaken for tag parts.
      const html = '<html><body><p>hello</p></body></html>'

      expect(formatToolResultSummary(html)).toContain('hello')

      const xml = '<retrieval_status_like>not ours</retrieval_status_like>'

      expect(formatToolResultSummary(xml)).toContain('not ours')
    })
  })
})

describe('extractToolErrorMessage', () => {
  it('finds nested error messages through wrappers', () => {
    const error = extractToolErrorMessage({
      success: false,
      result: {
        output: {
          error: {
            message: 'Permission denied writing /tmp/demo.txt'
          }
        }
      }
    })

    expect(error).toBe('Permission denied writing /tmp/demo.txt')
  })

  it('does not treat successful payload messages as errors', () => {
    const error = extractToolErrorMessage({
      success: true,
      message: 'Completed successfully',
      data: { count: 3 }
    })

    expect(error).toBe('')
  })

  it('ignores placeholder error fields in successful payloads', () => {
    const error = extractToolErrorMessage({
      success: true,
      data: {
        error: 'none',
        status: 'ok'
      }
    })

    expect(error).toBe('')
  })
})
