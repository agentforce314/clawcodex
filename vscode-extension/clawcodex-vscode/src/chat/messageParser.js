/**
 * messageParser — display helpers for tool activity (names, icons, input
 * previews) used by the chat controller and the permission cards.
 */

function parseToolInput(input) {
  if (!input || typeof input !== 'object') return String(input ?? '');
  if (input.command) return input.command;
  if (input.file_path || input.path) return input.file_path || input.path;
  if (input.query) return input.query;
  try { return JSON.stringify(input, null, 2); } catch { return String(input); }
}

function toolDisplayName(name) {
  const map = {
    Bash: 'Terminal',
    Read: 'Read File',
    Write: 'Write File',
    Edit: 'Edit File',
    MultiEdit: 'Multi Edit',
    Glob: 'Find Files',
    Grep: 'Search',
    LS: 'List Directory',
    WebFetch: 'Web Fetch',
    WebSearch: 'Web Search',
    TodoRead: 'Read Todos',
    TodoWrite: 'Write Todos',
    Task: 'Sub-agent',
    Agent: 'Sub-agent',
    AskUserQuestion: 'Question',
    NotebookEdit: 'Edit Notebook',
    ExitPlanMode: 'Plan Approval',
  };
  return map[name] || name || 'Tool';
}

function toolIcon(name) {
  const map = {
    Bash: '\u{1F4BB}',
    Read: '\u{1F4C4}',
    Write: '\u{270F}️',
    Edit: '\u{270F}️',
    MultiEdit: '\u{270F}️',
    NotebookEdit: '\u{270F}️',
    Glob: '\u{1F50D}',
    Grep: '\u{1F50E}',
    LS: '\u{1F4C2}',
    WebFetch: '\u{1F310}',
    WebSearch: '\u{1F310}',
    Task: '\u{1F916}',
    Agent: '\u{1F916}',
    ExitPlanMode: '\u{1F4CB}',
  };
  return map[name] || '\u{1F527}';
}

module.exports = {
  toolDisplayName,
  toolIcon,
  parseToolInput,
};
