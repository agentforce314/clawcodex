# clawcodex TUI — screenshot-verification gallery

Rendered terminal frames ("screenshots") of ported features, captured via the
systematic ink-testing-library method (render → drive input/messages → save the
exact frame the terminal would show). Regenerate with:

```
npm run build && CLAWCODEX_FULLSCREEN=1 node scripts/gallery.mjs
```

Each `.txt` holds the raw frame (ANSI colour included — `cat` it in a terminal to
see it in colour). These are display-faithful: `lastFrame()` is the precise text
the Ink renderer emits.

| Frame | Feature | Demonstrates |
|-------|---------|--------------|
| `01-banner.txt` | Startup banner + gradient logo (§7) | CLAWCODEX ANSI-shadow wordmark (sunset palette), info panel, ready line |
| `02-slash-menu.txt` | Slash command menu (§1/§2) | autocomplete list after typing `/` |
| `03-at-mention.txt` | `@` file mention (§1) | live file dropdown (`src/App.tsx`, `src/cli.tsx`) after `@src` |
| `04-permission-destructive.txt` | Permission prompt + destructive warning (§5) | Bash prompt with `⚠ this command looks destructive` for `rm -rf` |
| `05-mcp-elicitation.txt` | MCP elicitation form (§6) | server's "Enter your GitHub username" → typed `octocat` |
| `06-live-thinking.txt` | Live-streaming thinking (§3) | dim `∴` reasoning buffer streaming during a turn |
| `07-queue-priorities.txt` | Queued prompts + spinner + folder-trust (§1/§7/§6) | busy spinner, a queued `⏎` prompt, first-run trust notice |
| `08-model-picker.txt` | Interactive picker (§6) | `❯ deepseek-v4-pro` with selectable models |
| `09-rtl.txt` | RTL text shaping (§8) | Hebrew `שלום עולם` rendered visually reversed (`םלוע םולש`) |

All frames captured against a `deepseek-v4-pro` session over the Direct Connect
protocol (FakeTransport injecting the same wire messages the Python agent-server
emits).
