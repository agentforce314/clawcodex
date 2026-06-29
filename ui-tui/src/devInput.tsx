/**
 * Phase 0 de-risk harness: hermes's TextInput on hermes's renderer, standalone.
 *
 * NO backend, NO gateway — just the input box + a local echo list. The whole
 * point is to validate, on a real Terminal.app, whether hermes's input
 * subsystem is smooth (no backspace freeze) when running inside clawcodex's
 * build/launch. If this is smooth, the wholesale port is worth it.
 *
 * Run:  node ui-tui/dist/devInput.js   (in a real terminal)
 *       type a line, Enter to submit, hold BACKSPACE to test latency,
 *       Ctrl+C or Esc to quit.
 */
import { Box, render, Text, useApp, useInput } from '@hermes/ink'
import { useState } from 'react'

import { TextInput } from './components/textInput.js'

function Dev() {
  const { exit } = useApp()
  const [value, setValue] = useState('')
  const [lines, setLines] = useState<string[]>([])
  const cols = process.stdout.columns || 80

  useInput((input, key) => {
    if ((key.ctrl && input === 'c') || key.escape) exit()
  })

  return (
    <Box flexDirection="column">
      <Text>devInput — type, Enter submits, hold Backspace to test, Ctrl+C/Esc quits</Text>
      {lines.map((l, i) => (
        <Text key={i}>{`• ${l}`}</Text>
      ))}
      <Box>
        <Text>{'❯ '}</Text>
        <TextInput
          columns={Math.max(20, cols - 2)}
          onChange={setValue}
          onSubmit={(v: string) => {
            if (v.trim()) setLines((ls) => [...ls, v])
            setValue('')
          }}
          value={value}
        />
      </Box>
    </Box>
  )
}

const { waitUntilExit } = await render(<Dev />, { exitOnCtrlC: true })
await waitUntilExit()
