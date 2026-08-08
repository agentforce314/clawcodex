# ClawCodex Desktop

The native desktop app for [ClawCodex](../README.md) — chat with the agent in a
polished native window: streaming responses, live tool activity, side-by-side
previews, a file browser, and settings, no terminal required. Built with
Electron, Vite, and React.

> **Status: port in progress.** The UI layer builds and its test suite runs
> (see *Development* below). Wiring to the ClawCodex Python backend
> (`clawcodex serve`) and packaged installers land in follow-up stages.

## Layout

- `src/` — the renderer: a React app (chat surface, panes, previews, settings).
- `electron/` — the main process: window/process lifecycle, native capabilities,
  backend boot, and a narrow typed IPC bridge (`preload.ts`).
- `packages/shared/` — `@clawcodex/shared`, types shared between surfaces.
- `e2e/` — Playwright specs driven against a mock backend.
- `scripts/` — build, packaging, and diagnostic tooling.

Engineering conventions live in [`AGENTS.md`](./AGENTS.md); the visual and
interaction contract lives in [`DESIGN.md`](./DESIGN.md).

## Development

```bash
cd ui-desktop
npm ci                 # standalone install — no workspace root required
npm run typecheck      # renderer + electron + e2e tsconfigs
npm run lint
npm test               # vitest: ui + electron projects
```

`npm run dev` (Vite renderer + Electron shell) boots the app shell; full
backend attach is part of the wiring stage.

## Testing

- Unit/component tests: `npm test` (vitest projects `ui` and `electron`).
- E2E: `npm run test:e2e` (Playwright against the mock backend) — enabled at
  the end of the port.
