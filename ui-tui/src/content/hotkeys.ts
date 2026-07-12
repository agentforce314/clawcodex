import { linkOpenHotkey } from '../lib/linkAffordance.js'
import { isMac, isRemoteShell } from '../lib/platform.js'

const action = isMac ? 'Cmd' : 'Ctrl'
const paste = isMac ? 'Cmd' : 'Alt'

// Terminal-appropriate link-opening gesture (Cmd+click in OSC 8 terminals,
// Cmd+double-click the URL in Apple Terminal). Omitted when unknown, so we
// never advertise a gesture the user's terminal can't perform.
const linkHotkeys: [string, string][] = (() => {
  const row = linkOpenHotkey()

  return row ? [row] : []
})()

const copyHotkeys: [string, string][] = isMac
  ? [
      ['Cmd+C', 'copy selection'],
      ['Ctrl+C', 'clear draft']
    ]
  : isRemoteShell()
    ? [
        ['Cmd+C', 'copy selection when forwarded by the terminal'],
        ['Ctrl+C', 'copy selection / clear draft']
      ]
    : [['Ctrl+C', 'copy selection / clear draft']]

export const HOTKEYS: [string, string][] = [
  ['Esc', 'interrupt the running turn'],
  ...copyHotkeys,
  ...linkHotkeys,
  [action + '+D', 'exit'],
  [action + '+G / Alt+G', 'open $EDITOR (Alt+G fallback for VSCode/Cursor)'],
  [action + '+L', 'redraw / repaint'],
  [paste + '+V / /paste', 'paste text; /paste attaches clipboard image'],
  ['Tab', 'apply completion'],
  ['↑/↓', 'completions / queue edit / history'],
  ['Ctrl+X', 'open live session switcher (deletes queued message while editing)'],
  [action + '+A/E', 'home / end of line'],
  [action + '+Z / ' + action + '+Y', 'undo / redo input edits'],
  [action + '+W', 'delete word'],
  [action + '+U/K', 'delete to start / end'],
  [action + '+←/→', 'jump word'],
  ['Home/End', 'start / end of line'],
  ['Shift+Enter / Alt+Enter', 'insert newline'],
  ['\\+Enter', 'multi-line continuation (fallback)'],
  ['!<cmd>', 'run a shell command (e.g. !ls, !git status)'],
  ['{!<cmd>}', 'interpolate shell output inline (e.g. "branch is {!git branch --show-current}")']
]
