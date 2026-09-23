# Multi-agent runtime verification

This verification compares Clawcodex's Python runtime with the local Claude Code
reference, `my-docs/claude-code-multi-agent-deep-dive.html` and `typescript/`.
The reference describes expected behavior; it is not Clawcodex implementation.
The review started from commit `b8b6d5d3`.

The supported collaboration backend is an in-process team of persistent workers.
Local background agents and workflow workers use the same session supervisor,
while retaining their different completion and communication contracts.

## Reproduced defects and resulting behavior

| Area | Defect or missing integration | Result |
| --- | --- | --- |
| Local worker resume | A completed worker became `running` without a new model loop | Retained executable continuations launch a managed thread, reload typed transcript history, and reuse the worker ID and settings |
| Message/completion race | A correction accepted during the final response could remain unread | Completion atomically checks the inbox and continues the loop when a message was accepted |
| Admission and naming | Concurrent named launches could claim a name before either task was visible | Publish the task before claiming its name; reject collisions and release failed launches; resume obeys pause/capacity/depth rules |
| Persistent teams | Team tools and mailbox helpers existed without a production worker lifecycle | TeamCreate establishes the leader and roster; named Agent calls create persistent teammates; a managed poller consumes their mailboxes |
| Team communication | Findings, control traffic, and private final prose lacked an integrated delivery contract | SendMessage delivers explicit peer/leader findings; final prose remains private; idle and exit notices describe worker availability |
| Plan decisions | A rejection could change permission mode | Only a matching leader approval can change mode; rejected or stale responses retain restrictions; unissued mailbox control records cannot approve plans |
| Permission UI | A worker's permission request could outlive interruption | Requests carry agent identity and an abort signal; interruption denies and removes a pending request |
| Shared tasks | Independent task dictionaries could not support automatic cooperative work | Team contexts share a locked, persisted board; claims honor dependencies; reciprocal links and completion hooks apply to real workers |
| Notification ownership | A process-wide queue could deliver another session's or parent's result | Producers carry the session registry and recipient; active parents receive child results, and the root receives orphaned results |
| Workflow budget | Queued calls checked the budget before acquiring capacity | Check after acquiring a slot and charge each attempt before releasing it, including failed attempts |
| Workflow startup/stop | A returned task handle could precede registry publication | Publish a controller and task before launching; immediate TaskStop works, and startup cannot resurrect a stopped task |
| Workflow replay | A resumed journal could omit cache hits or fail to checkpoint completed work | Atomically checkpoint successful calls and replayed results; failed/skipped calls remain eligible for execution |
| Isolation | Agent ignored worktree requests; workflow setup could fall back to shared files | Require a real Git checkout, execute in the worktree, preserve edits/commits, and report retained paths |
| Worktree lifetime | A clean parent checkout could disappear before its background descendant wrote | Supervisor ancestry keeps that checkout intact while a descendant remains active |
| Session exit | Background workers could outlive closed session transports | Pause admission, interrupt owned work, stop task adapters, join managed threads with a bound, and remove an exited team |

## Runtime map

- `src/tool_system/tools/agent.py` selects the delegation mode, admits work,
  owns foreground/background launch, and publishes progress and transcripts.
- `src/agent/run_agent.py` constructs or retains a child context, applies tool
  and permission restrictions, and drives the real query loop. An early query
  stop such as max-turn exhaustion is a delegation failure.
- `src/agent/resume_agent.py` serializes same-session relaunches and waits for
  the previous worker's cleanup before reusing its ID.
- `src/services/swarm/team_runtime.py` owns team membership, mailbox delivery,
  persistent assignment loops, plan/shutdown protocols, and lifecycle cleanup.
- `src/services/swarm/task_board.py` provides locked board transactions,
  atomic snapshots, dependency-aware automatic claiming, and assignment release.
- `src/services/swarm/agent_supervisor.py` is the common admission, ancestry,
  visibility, and interruption authority for local, team, and workflow workers.
- `src/workflow/runtime.py`, `runner.py`, and `launch.py` connect scheduling,
  observed-token accounting, live agents, task handles, and replay journals.
- `src/utils/message_queue_manager.py`, `src/query/query.py`, and
  `src/server/task_notifications.py` deliver messages to their session and
  recipient at model-turn boundaries. Dynamic task XML is escaped.
- `src/server/agent_server.py` exposes agent progress, permission requests,
  interruption, and teammate messages through the real WebSocket transport.

The leader's root ToolContext keeps `agent_id=None`. Its roster identity is
stored separately, so starting a team does not make the root conversation act
like a subagent. Teammates retain their context and file-read fingerprints
across assignments. Anonymous teammate delegation is synchronous; teammates
cannot create another team or spawn named teammates.

## Acceptance evidence

The new deterministic tests drive real query loops, tools, task registries,
transcripts, mailboxes, task files, worktrees, and/or WebSocket connections.
They replace the external model provider with a script; selected tests also
control startup timing or install a hook to exercise a specific race or veto.
They do not require a model to happen to choose the desired sequence.

| Suite | Evidence |
| --- | --- |
| `tests/test_multi_agent_runtime_e2e.py` | Real Read, foreground/background output, same-ID resume and prior history, late corrections, concurrent sends, HUD eviction, paused admission, eight competing named launches, max-turn failure, two-session XML delivery, nested/orphan notifications, workflow budget, immediate and active TaskStop, failed-attempt usage |
| `tests/test_team_runtime_e2e.py` | Leader/member identity; peer and leader delivery; private final output; repeated assignments; shared board and dependencies; 24 concurrent claimers for 12 tasks; completion-hook veto; real Write denied after plan rejection and permitted after approval; stale/forged controls; duplicate spawn cleanup; shutdown rejection/approval; session isolation; Read followed by Edit across assignments |
| `tests/test_agent_worktree_e2e.py` | Foreground and background Agent plus workflow execute real Write in a separate checkout; non-Git isolation fails before model execution; a real fork launches a background descendant that writes after the parent completes |
| `tests/server/test_multi_agent_collaboration_e2e.py` | Actual DirectConnect/WebSocket client and server: TeamCreate → Agent → permission request → allow or interrupt/retry the same worker → Write → SendMessage → root summary → approved shutdown → TeamDelete, with progress and root tool display assertions |
| Existing lifecycle, fork, coordinator, workflow, task, shell, permission, and server suites | Regression coverage for admission limits, tool filtering, parent prompt/history rules, cancellation, retries, replay, task output, and UI event compatibility |

### Live external-provider smoke test

A separate smoke test used the configured DeepSeek provider with
`deepseek-v4-pro` in a temporary fixture workspace. It passed all four checks:

1. A background worker used Read on values 17 and 23 and returned `TOTAL=40`.
2. The same worker resumed with its history and returned `FOLLOWUP=80`.
3. Persistent Alice sent a result to Bob; Bob acknowledged it to the leader.
4. Both teammates approved matching shutdown requests, exited, and TeamDelete
   removed the team.

This verifies actual provider/tool interoperability for those traces. It is not
an exhaustive live-model benchmark or a claim about every provider.

### Validation status

- Local full Python run: **10,758 passed, 16 skipped, 340 passing subtests**
  in 604.73 seconds. The 11 warnings include existing unittest coroutine and
  deprecation warnings. Command: `python -m pytest -q tests --tb=short`.
- The first full Python run: **10,743 passed, 12 failed, 16 skipped**, plus
  340 passing subtests. Failures exposed a None-context plan permission check,
  lightweight monitor contexts, and outdated lifecycle test doubles. These
  were fixed; the affected regression group then passed **413 tests**.
- After the workflow startup fix, **197 workflow/runtime/server tests passed**.
- After the descendant-worktree fix, **38 worktree/supervisor tests passed**.
- PR CI additionally runs the full Python suite on Linux and Windows,
  desktop checks on both platforms, web typecheck/tests/build, and the Harbor
  adapter suite. The PR's checks are the authority for those platform results.
- The [prior Windows CI run](https://github.com/agentforce314/clawcodex/actions/runs/35822292378)
  failed in four fixtures that assumed Unix stdout encoding, newline
  translation, permission bits, or home-directory environment variables.
  The fixtures now emit explicit UTF-8, preserve literal line endings,
  simulate an unreadable directory at the filesystem boundary, and retain the
  native environment while isolating the config variable under test. Their
  behavioral assertions remain in place; the affected group passes all
  **144 tests** locally.
- Admission tests also synchronize with the managed worker threads using
  thread-safe events, elapsed-time waits, and teardown joins. Their prior
  zero-delay event-loop spins could finish before a Windows thread started;
  one fixture tried to wake a worker using another loop's asyncio.Event.
  The corrected admission and end-to-end group passes **54 tests** locally.
- The automatic-claim test observes completion through TaskGet's transaction
  lock before checking the persisted snapshot. This avoids reading an
  in-flight dictionary mutation. The team/task group passes **163 tests**,
  and the dependency/automatic-claim trace passes **20 independent runs**.
- The bridge heartbeat regression waits for its persisted refresh with a
  deadline and guaranteed teardown. The mocked connection-error regression
  still exhausts retries and checks exception identity, with network backoff
  reduced to zero inside that test. Their combined group passes **51 tests**.
- Black and isort were applied to changed Python code. The four new runtime
  modules pass targeted mypy. Full-project mypy reports **395 diagnostics**
  versus **397 on the starting commit**, with **zero added diagnostics** after
  normalizing line numbers. The environment lacks some third-party stubs;
  this is a baseline comparison, not a claim that full-project mypy is green.
- The TUI typecheck and **65 agent-tree/task-output tests pass**. Its full
  suite returned **1,896 passed, 8 failed, 4 skipped**. Seven failures reproduce
  on an untouched archive of the starting commit (inline-diff formatting,
  status-bar fields, indicator defaults, and height estimates). The eighth is
  a cursor-layout timeout under the full concurrent run; all four cursor tests
  pass when rerun alone, as on the baseline. No TUI source is changed by this patch.

## Operational boundaries

- **In-process collaboration:** remote-control workers, tmux/iTerm panes, and
  UDS permission relay backends from the reference are not implemented here.
  A filesystem mailbox is a persistence/delivery format, not a supported
  cross-process team backend. Control records are checked against the live
  runtime that issued them.
- **Same-session worker resumption:** completed local workers can resume after
  HUD eviction because their executable continuation remains in the session.
  A transcript alone does not recreate an executable worker after a process
  restart. Workflow journals can replay successes when a new run is explicitly
  launched with the matching script and prior run ID.
- **One team per workspace:** an existing `.clawcodex/team.json` is never
  overwritten by another session. Normal shutdown removes an exited team.
  A process crash may leave a stale roster; automatic crash recovery is not
  provided. Confirm the prior process has stopped before manually removing it.
- **Task-board locking:** shared threads use a common RLock and atomic file
  replacement. External processes editing the same JSON board do not join
  that transaction. Direct owner reassignment remains allowed, matching the
  reference; automatic pickup separately enforces owner/dependency eligibility.
- **Budget semantics:** the observed token budget prevents subsequent work
  from starting after usage reaches the threshold. Already-running requests
  can finish above it; this is not a provider-side hard spending cap.
- **Cancellation:** signals stop work at cooperative boundaries. A provider
  blocked inside a synchronous call may finish that call before its thread
  exits. Session cleanup has a bounded wait and retains a team still stopping.
- **Worktree retention:** changed or committed work is preserved. A clean
  checkout still used by a background descendant is also preserved and its path
  returned; it is not later deleted automatically. Isolation setup failure
  never silently redirects edits to the parent checkout.
