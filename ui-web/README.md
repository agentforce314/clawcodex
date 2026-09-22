# ClawCodex Web

The browser client. Same agent, same gateway, same sessions as the TUI and the
desktop app — a third front end, not a second product.

```bash
clawcodex web --build      # build the bundle, then serve and open a browser
clawcodex web              # once built
```

## What it is

`clawcodex web` is not a separate server. It is `clawcodex serve` — the
existing JSON-RPC gateway the desktop app already talks to — with this app's
built `dist/` mounted on it. So a browser tab drives the same in-process agent
the TUI does, over the same socket, against the same saved sessions.

```
  browser  ──HTTP/WS──▶  clawcodex serve  ──▶  in-process agent (src/server/agent_server.py)
   ui-web                /api/ws                 the same one the TUI runs
```

The whole coupling to the backend lives in four files:

| File | Responsibility |
| --- | --- |
| `src/gateway/protocol.ts` | The wire vocabulary, as types. The one place the client states what the server says. |
| `src/gateway/client.ts` | The socket: request/response by id, pushes by type, reconnect with backoff. |
| `src/gateway/tool-vocabulary.ts` | ClawCodex tool names → the names the tool cards are keyed by (mirrors the server's own table, for rehydrated transcripts). |
| `src/state/transcript.ts` | Gateway events → renderable nodes. Every rule about what the reader sees is a pure function here. |
| `src/state/trajectory.ts` | The same events at full resolution, timed — the ledger behind the Trajectory tab. |

Everything above those is ordinary UI and knows nothing about JSON-RPC.

## Layout

```
src/
  gateway/       protocol types, socket client, token/backend discovery
  state/         stores (nanostores), actions, the transcript reducer, theme
  layout/        three-column solver + AppFrame (drag handles, concession chain)
  sidebar/       project/worktree/session tree
  conversation/  chat flow, message + tool + reasoning rows, composer, approvals
  trajectory/    the run as a metered ledger: timeline, rows, inspector, totals
  workspace/     directory picker: which folder a session runs in
  sidebar-right/ right-hand column: tabs for session facts, the workspace tree,
                 and a paged reader per file the conversation opened
  ui/            primitives (buttons, cards, code/diff/terminal blocks) + markdown
  styles/        design tokens, typography, scrollbars, shiki wiring
```

Two structural rules hold throughout:

- **The conversation column owns exactly one scrollport**, holding both the
  transcript and the sticky composer seat. That is why a wheel gesture over the
  input card still scrolls the conversation.
- **One width axis.** `--cc-chat-content-width` sizes the transcript and the
  dock cards; the input card is exactly that plus 32px, at every viewport. The
  relation is declared once, on the conversation root.

## The composer

The `+` button and a typed `/` open the same menu. With nothing typed it
lists an **Add** section (the image picker, plan, goal) and a **Commands**
section, each in usage order; every row carries a glyph, a title, the command
name beside a title that differs from it (`Output style` / `output-style`), and
the catalog's own description right-aligned, so the titles read as one column.
A typed query ranks every row by a case-insensitive ordered subsequence of the
name or the title, prefix hits first — `/ol` finds `Output style` before
`Model` — the way the reference's `/` menu does. The menu is capped at the
design height or the space above the card, whichever is less, and a fade at
its foot says there is more below.

What a pick does depends on the row. The image row opens the picker. A
command that takes an argument claims the draft as `/name ` — or as
`/name <the text already typed>` when the launcher opened over a sentence, so
"fix the bug" and Plan read `/plan fix the bug`. A bare command runs at once,
as it would on Enter. The rows and their arrangement are a pure function
(`src/conversation/command-menu.ts`); the composer decides what a pick does.

A sent message shows an `@path` mention as a chip carrying the file's type
icon and name, and a click opens the file in the right column, read against
the session's workspace. A folder mention keeps the chip but not the click.

## The right column

The right column is a small docking surface rather than one fixed panel. It
opens on **Session** — the facts about this run, the files it touched, the tools
it leaned on — and grows a tab for anything else you point it at. Each chip
leads with its kind: the folder for the tree, the file's own type icon for a
file, the compass for the start page.

- **Start** is what the strip's `+` opens, drawn only while the column holds
  none: a muted compass over one capsule per page, each with a line on what it
  opens. Picking a capsule opens that page in the start page's slot, so the
  guide gives way to what it opened. The header's folder button opens the tree
  directly, as the reference's *Open workspace in Files* does.

- **Files** lists the workspace one directory level at a time, fetched the first
  time you open a level and kept afterwards, so collapsing and reopening costs
  nothing. Clicking a file opens it. A level is ordered *server-side* before its
  2000-entry cap is applied — and before anything is stat'd, so the cap bounds
  the work and not just the answer — which makes what `truncated` hides the
  alphabetical tail rather than an arbitrary sample of the directory. That
  server-side order is the column's own collation minus one thing it cannot
  afford: case-insensitive, digits compared as numbers (so a `chunk1…chunk5000`
  directory keeps `chunk1`, not `chunk1, chunk10, chunk100`), but **not**
  directories-first — that needs every child's type, which is the stat-per-child
  the cut exists to avoid. So over the cap the cut can still fall a few names
  from where the displayed list ends.
- **A file** opens as its own tab, drawn by the viewer its path picks —
  Markdown rendered as prose, code highlighted behind a line gutter, an HTML
  document in a frame of its own, an image at its own size, a PDF through the
  browser's viewer, anything else as bare text — with the text viewers one
  pick away in the header's chooser where they still make sense (an HTML
  document or an SVG is also its source; a raster image or a PDF is only
  itself), and back. The header names the file over its directory in the
  quieter ink, and only the directory gives way when the row is short. The
  text viewers read a page at a time (`fs.read_file`, 5000 lines per page),
  with **Load more** at the end of the loaded text until the file ends; the
  frame, the image and the PDF read the file whole (`fs.read_bytes`, capped at
  32 MB, the reference's own full-file limit). A `read` row in the conversation hands its 1-based `offset` along,
  so the file opens where the agent was looking — walking forward at most five
  pages, because each page is a round trip whose backend re-reads the lines
  before it. Five pages of 5000 is how far a jump reaches; past that it lands on
  the last loaded line, beside **Load more**. Wrap, scroll offset and the page you were on live with the tab, not
  with the component — switching tabs and coming back does not re-read anything.
  A page is also capped at 2 MB, and a page over that is **refused** rather than
  truncated: a file whose first line is bigger than the cap (a minified bundle,
  a one-line JSON dump) therefore cannot be shown here at all.

The HTML frame is `sandbox="allow-scripts"` and never `allow-same-origin`:
scripts run, but on an opaque origin that can reach neither this app nor the
gateway. That origin cannot load resource URLs the parent creates either, so
the stylesheets and classic scripts the document declares by relative path
are read up front through `fs.read_related` — the backend joins the
document's directory, and the client never names an asset by an absolute
path — and handed to the frame inside a bootstrap document that rebuilds them
as Blob URLs of its own. The limits are the reference's: 4 MB per asset, 32 MB
per package, 64 assets. Module imports, CSS `url()` and `@import`, images and
runtime fetches are left to the browser, which cannot resolve a relative one
from a Blob document, so they stay blank rather than being fetched through the
gateway; a `<base href>` hands every resolution to the browser and packs
nothing. Every read behind these viewers is confined to the workspace like
the paged one — a related file outside it is refused, where the reference
allows it.

A tab's identity is its path, so opening the same file twice reveals the tab it
is already in (and jumps again) rather than stacking duplicates. Full screen
takes the whole frame; the conversation is one click back.

The change notice — *"The file has changed; this is the older text."* — is
derived from the transcript, not from a filesystem watcher: a completed
`write_file` or `edit_file` for that path, after the page landed. So an edit
made **outside** the agent is not announced, and Reload is there for it. The
notice never applies itself: reloading under a reader loses their place, and a
file the agent is writing changes repeatedly.

For the same reason a file **deleted** under the reader says nothing: there is
no `stat` channel to fail on, so the loaded pages simply stay on screen until
Reload reports it. A failure while *reading* is announced, at the end of the
loaded text — but a file already read to `eof` has no next page to fail. Nor
does the agent merely *reading* a file that something else changed raise the
notice, as it does upstream: there, the read observes a new version through the
resource; here it observes nothing the client can see.

All four reads are confined to the session's workspace root, symlinks
resolved (`src/server/desktop_workspace_files.py`). The client names the *session*, never
the root: the backend derives the boundary, because one the client can move is
not a boundary. It is honesty rather than a security boundary, under every
binding: one token gates the whole socket, so anyone who can call these methods
can also start a session and have the agent read the disk with its own tools.
What it buys is that a column claiming to show the workspace cannot be walked
out of through `..`.

## Subagents

A session that delegates shows it in the header: **N subagents ▾** beside the
title, with a live dot while any are still running. The list behind it is one
row per delegation — what it was asked (the `Agent` call's description), the
agent type and model it ran on, its state, and what it cost in tokens and
time. Clicking a row opens the subagent in the conversation column, in the
session's place: its prompt, then everything it did, rendered with the same
tool rows the session uses, and a read-only seat where the composer would be.
The parent's title is the way back, and the child's own name switches among
its siblings.

Two sources feed that list, and the catalog (`src/state/subagents.ts`) is a
pure fold over both:

- **The `Agent` tool rows** in the transcript carry the prompt and, once the
  call settles, the report and the run's own totals. The backend persists the
  Agent tool's display envelope (`agent_id`, status, model, duration, tokens,
  tool count) beside the stored result, so a resumed session lists its
  subagents exactly as the live one did.
- **`subagent.progress` events** — the Agent tool's per-message progress,
  translated by the gateway — carry what a run is doing while it runs: its
  last activity, its tool count so far, and finally how it stopped. A frame
  names the call it answers (`tool_use_id`), which is how a running row and its
  progress find each other before the row's result names the run.

A subagent's full record is the sidechain transcript the Agent tool writes as
the run goes (`~/.clawcodex/transcripts/<agent_id>.jsonl`, for foreground and
background runs alike), read through `subagent.transcript` and rehydrated with
the same fold as a resumed session. A run that predates transcripts, or whose
file was cleaned up, shows its prompt and report alone.

The Agent row itself reads `Agent · <description>` with, at its right edge,
the run's activity while it runs and `N tools · duration` once it is done; its
body holds the prompt, the report as prose, and a button into the run.

The catalog is also where a run is stopped. A running row carries **Stop**,
the child view's seat carries the same, and the list's foot says how many
are running against the session's cap beside a **Pause spawning** switch.
The reference gives Stop to a continuable child's composer; here the runs are
one-shot, so their controls live with the catalog rather than in a tab of
their own beside it — one surface for the subagents, not two.

## Session titles

A session is named the moment its first prompt is sent — the prompt's first
line, throat-clearing stripped — so neither the header nor the sidebar ever
shows a blank. Then the session's own model is asked for a short title, and
its answer replaces the heuristic when it arrives, a few seconds later. The
side query runs on the same provider the conversation does, never a
hard-coded one, and a rename typed in the meantime stands. Nothing about the
title ever reaches the model's context.

The sidebar lists a blank session — one nothing has been typed into — only
while it is the one on screen, labelled **New session**; the backend keeps
every runtime session it spawned, and a row per abandoned press of the button
was a column of nothing.

Workspace folders start collapsed, with only the current session's folder
expanded (or the selected workspace before a session starts). As in DeepSeek
Harness, selecting a session opens its folder unless it was manually collapsed;
manual toggles last for the page's lifetime. Filtering temporarily expands
matching folders and restores their previous state when cleared.

## Opening a saved session

Clicking a row is two round-trips, the way the reference opens a session.
`session.history` reads the stored transcript cold — a file read, tens of
milliseconds even for a multi-megabyte conversation — and the client renders
it at once: title, workspace, model chip, nodes, trajectory timings. Then
`session.resume` attaches the runtime that will answer the next prompt
(provider, tool registry, system prompt, the stored conversation loaded into
it) behind the transcript; the composer says *Connecting the agent…*
meanwhile, and a prompt sent during that window waits for the attach rather
than starting a session of its own. Before this the whole click sat on the
attach, and the attach sat on a system-prompt walk of the workspace that
took ~20 s per build on a repo with a few `node_modules` trees (built at
spawn and again on resume — ~45 s to open a session).

A row the backend has already replayed comes back with the same runtime
(subscribed to this socket too, so a second window sees the turns): the
backend keys live sessions by runtime id, matches a resume on the stored id
the runtime replays, and a second click while the first is still attaching
waits on it instead of spawning twice. A runtime handed back mid-turn is
adopted as running. `session.history` and `projects.tree` are pure reads
the gateway serves beside whatever else the socket is doing, so a click's
transcript never queues behind the previous click's attach or the teardown
of the session being left.

Leaving a session that was only *looked at* — nothing sent to it, no
approval or question answered — releases its runtime: `session.close` with
`if_idle`, which the backend refuses while another window or a desktop tile
still holds the runtime (each socket that opened it is a holder until it
lets go or disconnects), while a turn runs or an ask is pending, and again
on the agent's own word (`get_activity`: a `/goal` continuation, a `/loop`
or cron job waiting to fire, a queued prompt, a background shell). So
browsing through saved sessions does not leave a trail of idle agents,
while a session that was used stays up; "used" survives a reload with the
remembered session. A runtime that goes away — closed by its last holder, or
its agent stream ended — tells every window (`session.closed`) and leaves
the registry, so the next prompt or click reconnects the conversation (a
prompt to a runtime the backend no longer has is sent again after the
reconnect); the row replays from the record the runtime saved under its own
id. A backend without `session.history` gets the one-call resume,
transcript included, as before.

## New session

**New session** (the sidebar button, the brand mark, `⌘⇧N`) opens a dialog
rather than starting a session on the spot. Its workspace picker lists every
folder the sidebar knows (a row per folder, the path as a second line only
where two folders share a name, the current one checked) with **Add
workspace…** pinned below the list after a divider — at the end of a long
list it was the row nobody scrolled to. That takes an absolute folder path
and creates the folder if it is not there yet (`session.create` with
`create_dir`). The **Worktree** switch runs the session in a fresh git
worktree of that repo — the CLI's `--worktree`, under
`.clawcodex/worktrees/<name>` — so parallel sessions in one repo cannot step
on each other's files; the worktree is left in place when the session ends,
since a browser tab has no exit dialog to offer keep-or-remove. The folder
is created as the backend's own user, anywhere it can write (`~` expands).
A refusal (a relative path, a folder that is not a git repository, Worktree
on a folder that does not exist yet) stays in the dialog for correcting,
before anything is created.

## The session across a reload

A reload lands back on the session the window was on. The client remembers
the runtime it was attached to and the row the sidebar showed for it (the
same id for a session created here; different for one resumed from a row,
since a resume spawns a fresh runtime that replays the stored one and saves
its later turns under its own id). On boot it resumes the *runtime*: while
the backend still has it, the reply is the very same session, its running
turn included; once it is gone, the runtime's own record — the complete one —
is replayed into a new runtime. A runtime that never saved, because nothing
was typed after resuming a row, has no record, so the row it came from is
replayed instead (which releases the blank runtime the first attempt landed
on, as any navigation away from an idle runtime does). A session the backend
no longer knows is forgotten without a notice: the hero is the honest place
to land.

## Trajectory

The **Trajectory** tab is the forensic view of the same session: every model
request and tool call in order, with what each cost and how long each phase
took. Chat answers "what was said"; this answers "what happened, and where did
the time go".

- **Timeline** — three lanes (input / model / tools). `Duration` off gives every
  operation equal width (the run's *shape*); on, it uses real elapsed widths with
  idle removed (where the time *went*). A model bar is drawn two-tone: the pale
  head is time waiting for the first token, the solid tail is generation. Drag
  across it to filter the ledger to a time range.
- **Ledger** — one line per operation, foldable by turn and by step.
- **Inspector** — Summary (tokens, model, stop reason, request timing),
  Preview (rendered content), Raw (the record as JSON).

### Where the numbers come from

Token counts are the backend's own per-request accounting, carried by the
`step.complete` event. **Timings are observed on the client** — the gateway
reports what happened, not when — so they include the loopback socket's
transport, which is far below the resolution these are read at.

A metric that could not be measured says so ("First token unavailable") rather
than showing a zero. That is why a **resumed** session starts with an empty
ledger: a replayed transcript carries no timings, and inventing them would be
worse than the empty state.

The same rule limits what a resumed session's stats line can say. The
figures under the composer — turns and steps, model time and tool time, TTFT
and output speed, cache-hit share, input and output tokens — are the ledger's
own totals, so the line and the Trajectory tab never disagree. A stored
transcript carries each step's timestamp, model and token accounting, so a
resumed session totals its cost exactly; what it does not carry is a
first-token time, so TTFT and output speed are left off the line rather than
shown as zeros.

One semantic worth knowing: `usage.input` is the cache **miss**, not the whole
prompt — the backend splits a prompt into what it paid full price for and what
came from cache, because they bill differently. `input + cache_read` is the full
prompt, which is what the Trajectory shows.

The **cache-hit rate** divides by a third bucket as well: `cache_write` (tokens
written into the cache) was processed in full and charged for, so it is a miss.
The rate is `cache_read / (input + cache_read + cache_write)` — the same sum the
stats line's input figure sums, so the percentage and the count beside it
describe one arithmetic.

## Development

```bash
npm install
npm run typecheck
npm run test
npm run build          # → dist/, what `clawcodex web` serves

# Live reload against a running backend:
clawcodex serve --host 127.0.0.1 --port 8317 --token dev
npm run dev            # http://127.0.0.1:5175/?token=dev  (proxies /api to 8317)
```

`CLAWCODEX_WEB_DIST=/path/to/dist` points the server at a bundle elsewhere; it
is authoritative, so a path with no bundle in it means "no bundle" rather than
a silent fall back to the checkout's.
`CLAWCODEX_WEB_SOURCEMAP=1` builds with sourcemaps (off by default: they more
than double the bundle, and only a developer with devtools open fetches them).

## Serving and the token

The gateway is token-gated, and a browser has no way to learn that token on its
own — so `GET /` serves this app with the token inlined as
`window.__CLAWCODEX_SESSION_TOKEN__` (the same global the desktop shell already
scrapes to adopt a running backend). The client reads it, or takes a `?token=`
from the URL and strips it from the address bar.

That page hands out the token, which is safe exactly as long as the server is
reachable from this machine only. `clawcodex web` therefore **refuses a
non-loopback `--host`** unless you pass `--allow-remote` and put your own
authentication in front of it.

## Packaging

A `pip`-installed ClawCodex does not ship a built bundle yet: `clawcodex web`
looks for `ui-web/dist` in a source checkout, then for a packaged
`src/server/web_dist`, and tells you how to build one when it finds neither.
`clawcodex web --build` runs the npm build for you (Node required).

## Brand assets

`public/favicon-32.png`, `public/favicon-192.png`, `public/apple-touch-icon.png`
and `src/assets/logo.png` are the official mark from
[clawcodex.app](https://www.clawcodex.app) — re-fetch them from
`/assets/` there if the mark changes. They are raster on purpose: the mark is
pixel art, so it has to land on exact pixel boundaries, and it carries its own
palette (`#aa2c00` shell, `#fe7500` highlights) rather than inheriting the
surrounding ink — which is why `BrandMark` is an `<img>` and not an inline SVG.

The server serves every root-level file in `dist/`, so adding another icon
needs no backend change.

## Notice

The visual design and several structural ideas are adapted from the
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) web client,
which is MIT licensed:

> MIT License — Copyright (c) 2026 DeepSeek

Adapted here: the design-token architecture (raw palette → semantic aliases →
surface-specific roles, with only the aliases moving between themes), the
three-column concession solver, the single-scrollport conversation column with
its sticky composer seat and shared width axis, the tool-card family
(terminal / diff / read / generic), the tabbed right column with its lazy
workspace tree, start page, file-type icons and paged reader with its
Markdown / code / plain-text viewers, the composer's sectioned command menu
behind the `+` button and the `/` trigger, the file-reference chips in sent
messages, and the scrollbar's rebindable rail geometry.

Diverged deliberately: the PDF viewer, which is the browser's own rather
than the reference's bundled PDF.js pages — a viewer every Chromium and Firefox
already has costs no bundle, at the price of the reference's lazy per-page
rendering and its tab-local zoom. And the session stats strip under the
composer. The
reference ran an in-page A/B between a one-line strip and a two-pill variant
with click-open dialogs, kept the pills, and deleted the line. This app has
the line. It is not what upstream settled on and is not a port of the current
design — it reads the same figures from the same ledger and drops a group with
nothing measured, but exact token counts are not reachable from it the way the
dialogs made them. Anyone re-syncing this column against the reference should
know the difference is a decision here rather than drift.

Not adapted: the DeepSeek branding, the cordis plugin runtime, the client module
system, and the right column's docking engine — splits, floating panes, drag and
drop and an undo history are a plugin runtime's worth of machinery for a column
this app never splits, so the tabs are here and the engine is not. The protocol
layer is ClawCodex's own gateway, which is a different contract entirely: the
reference client reads files through a host resource registry, this one through
two workspace-confined gateway methods.
