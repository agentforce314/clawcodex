"""Session-owned persistent teammates, mailbox delivery, and control protocols."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape, quoteattr

from src.agent.agent_tool_utils import resolve_agent_tools
from src.agent.prompt import get_agent_system_prompt
from src.agent.run_agent import RunAgentParams, run_agent
from src.agent.transcript import TranscriptWriter, get_agent_transcript_path
from src.services.swarm.mailbox import (
    TeammateMessage,
    get_inbox_path,
    make_iso_timestamp,
    write_to_mailbox,
)
from src.services.swarm.mailbox_poller import sweep_mailboxes
from src.services.swarm.task_board import (
    claim_next_task,
    release_tasks,
    write_json_atomic,
)
from src.services.swarm.team_file import (
    TeamFile,
    TeamMember,
    add_member,
    get_team_file_path,
    remove_member,
    write_team_file,
)
from src.tasks.in_process_teammate import (
    InProcessTeammateTaskState,
    TeammateIdentity,
    append_capped_message,
)
from src.tasks.progress import (
    ProgressTracker,
    get_progress_update,
    update_progress_from_message,
)
from src.tasks_core import generate_task_id, is_terminal_task_status
from src.tool_system.errors import ToolInputError
from src.tool_system.registry import ToolRegistry
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController, create_child_abort_controller
from src.utils.message_queue_manager import enqueue_pending_notification

logger = logging.getLogger(__name__)


def _approve(message: dict[str, Any]) -> bool:
    value = message.get("approve")
    if value in ("true", "false"):
        value = value == "true"
    if not isinstance(value, bool):
        raise ToolInputError("approve must be a boolean")
    return value


class TeamRuntime:
    """Own one team's workers and a single mailbox consumer for this session."""

    def __init__(self, context: Any, name: str, description: str | None) -> None:
        get_inbox_path(
            "team-lead", name, context.workspace_root
        )  # validate names first
        self.context = context
        self.lock = context.task_board_lock
        self.name = name
        self.lead_id = context.agent_id or generate_task_id("in_process_teammate")
        self.team = TeamFile(
            name, self.lead_id, description, (TeamMember(self.lead_id, "team-lead"),)
        )
        self.stop = threading.Event()
        self.closed = False
        self.poller = None
        self.contexts: dict[str, Any] = {}
        self.workers: dict[str, Any] = {}
        self.identities = {"team-lead": self.lead_id}
        self.shutdown_requests: dict[str, str] = {}
        self.controls: dict[str, tuple[str, str, str]] = {}
        self.previous = (context.agent_id, context.tasks, context.task_board_path)
        path = get_team_file_path(context.workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # TeamCreate from another session must not replace a live roster.
            with path.open("x", encoding="utf-8") as stream:
                stream.write("{}")
        except FileExistsError as exc:
            raise ToolInputError(f"A team already exists at {path}") from exc
        try:
            write_team_file(self.team, context.workspace_root)
            context.team = {
                "team_name": name,
                "lead_agent_id": self.lead_id,
                "sender_name": "team-lead",
            }
            context.team_runtime = self
            context.tasks = {}
            context.task_board_path = (
                context.workspace_root / ".clawcodex" / "tasks" / f"{name}.json"
            )
            write_json_atomic(context.task_board_path, {})
            self.poller = context.task_manager.start(
                name=f"team-mailboxes:{name}", target=self._poll
            )
        except BaseException:
            path.unlink(missing_ok=True)
            context.agent_id, context.tasks, context.task_board_path = self.previous
            context.team = None
            context.team_runtime = None
            raise

    def _member(self, name: str) -> TeamMember | None:
        return next(
            (
                member
                for member in self.team.members
                if member.name.casefold() == name.casefold()
            ),
            None,
        )

    def has_recipient(self, name: str) -> bool:
        with self.lock:
            return self._member(name) is not None

    def _sender(self, context: Any) -> str:
        if context is self.context or context.agent_id == self.lead_id:
            return "team-lead"
        member = next(
            (
                member
                for member in self.team.members
                if member.agent_id == context.agent_id
            ),
            None,
        )
        if member is None:
            raise ToolInputError("Only active team members can send team messages")
        return member.name

    def _write(
        self,
        recipient: str,
        sender: str,
        text: str,
        *,
        summary: str | None = None,
        protocol: bool = False,
    ) -> None:
        control_id = uuid4().hex if protocol else None
        if control_id is not None:
            self.controls[control_id] = (self.identities[recipient], sender, text)
        try:
            write_to_mailbox(
                recipient,
                TeammateMessage(
                    from_=sender,
                    text=text,
                    timestamp=make_iso_timestamp(),
                    summary=summary,
                    protocol=protocol,
                    control_id=control_id,
                ),
                team_name=self.name,
                workspace_root=self.context.workspace_root,
            )
        except BaseException:
            if control_id is not None:
                self.controls.pop(control_id, None)
            raise

    def send(
        self, context: Any, recipient: str, message: Any, summary: str | None
    ) -> dict[str, Any]:
        """Validate routing/identity, then commit a plain or protocol message."""
        with self.lock:
            if self.closed:
                raise ToolInputError("The team is closed")
            sender = self._sender(context)
            member = self._member(recipient)
            if member is not None:
                recipient = member.name
            if isinstance(message, str):
                if not summary:
                    raise ToolInputError("summary is required for plain-text messages")
                recipients = (
                    [m.name for m in self.team.members if m.name != sender]
                    if recipient == "*"
                    else [recipient]
                )
                for target in recipients:
                    if self._member(target) is None:
                        raise ToolInputError(
                            f"Recipient {target!r} is not on this team"
                        )
                for target in recipients:
                    self._write(target, sender, message, summary=summary)
                return {
                    "success": True,
                    "recipients": recipients,
                    "message": "Message committed to teammate inbox",
                }
            if not isinstance(message, dict) or recipient == "*":
                raise ToolInputError("Structured messages require one named recipient")
            member = self._member(recipient)
            if member is None:
                raise ToolInputError(f"Recipient {recipient!r} is not on this team")
            kind = message.get("type")
            request_id = message.get("request_id")
            envelope = dict(message, **{"from": sender})
            if kind == "shutdown_request":
                if sender != "team-lead" or recipient == "team-lead":
                    raise ToolInputError(
                        "The team lead requests shutdown of a teammate"
                    )
                request_id = self.shutdown_requests.get(recipient) or uuid4().hex
                self.shutdown_requests[recipient] = request_id
                envelope["request_id"] = request_id
            elif kind == "shutdown_response":
                if recipient != "team-lead" or sender == "team-lead":
                    raise ToolInputError(
                        "shutdown_response must be sent by a teammate to team-lead"
                    )
                if not request_id or self.shutdown_requests.get(sender) != request_id:
                    raise ToolInputError(
                        "shutdown_response does not match an outstanding request"
                    )
                approved = _approve(message)
                if not approved and not str(message.get("reason") or "").strip():
                    raise ToolInputError("Rejecting shutdown requires a reason")
                envelope["approve"] = approved
            elif kind == "plan_approval_response":
                if sender != "team-lead":
                    raise ToolInputError("Only the team lead can approve a plan")
                state = self.context.runtime_tasks.get(member.agent_id)
                if (
                    not isinstance(state, InProcessTeammateTaskState)
                    or not request_id
                    or state.plan_request_id != request_id
                ):
                    raise ToolInputError(
                        "plan_approval_response does not match an outstanding request"
                    )
                approved = _approve(message)
                mode = message.get("permission_mode", "default")
                if mode not in {
                    "default",
                    "acceptEdits",
                    "dontAsk",
                    "bypassPermissions",
                }:
                    raise ToolInputError("Invalid approved permission mode")
                if (
                    mode == "bypassPermissions"
                    and self.context.permission_context.mode != "bypassPermissions"
                ):
                    raise ToolInputError("The leader cannot grant bypassPermissions")
                envelope.update(approve=approved, permission_mode=mode)
            else:
                raise ToolInputError(f"Unknown structured message type: {kind!r}")
            self._write(recipient, sender, json.dumps(envelope), protocol=True)
            if kind == "shutdown_response":
                self.shutdown_requests.pop(sender, None)
                state = self.context.runtime_tasks.get(context.agent_id)
                if isinstance(state, InProcessTeammateTaskState):
                    self.context.runtime_tasks.update(
                        context.agent_id,
                        lambda prev: replace(
                            prev,
                            shutdown_requested=False,
                            shutdown_approved=envelope["approve"],
                        ),
                    )
                    if (
                        envelope["approve"]
                        and state.current_work_abort_controller is not None
                    ):
                        state.current_work_abort_controller.abort("shutdown approved")
            return {
                "success": True,
                "recipient": recipient,
                "request_id": request_id,
                "message": f"{kind} committed to teammate inbox",
            }

    def notify_assignment(self, context: Any, task: dict[str, Any]) -> None:
        """Wake a known assignee when TaskUpdate explicitly changes ownership."""
        owner = task.get("owner")
        if (
            owner
            and self._member(owner) is not None
            and task.get("status") != "completed"
        ):
            self._write(
                owner,
                self._sender(context),
                "Task assigned: " + json.dumps(task),
                summary=task["subject"],
            )

    def request_plan(self, context: Any, plan: str, path: str) -> str:
        """Keep the teammate in plan mode until a matching leader decision."""
        with self.lock:
            sender = self._sender(context)
            request_id = uuid4().hex
            self.context.runtime_tasks.update(
                context.agent_id,
                lambda prev: replace(
                    prev,
                    awaiting_plan_approval=True,
                    plan_request_id=request_id,
                ),
            )
            self._write(
                "team-lead",
                sender,
                json.dumps(
                    {
                        "type": "plan_approval_request",
                        "request_id": request_id,
                        "from": sender,
                        "plan": plan,
                        "plan_file_path": path,
                    }
                ),
                protocol=True,
            )
            return request_id

    def _notice(self, sender: str, text: str) -> None:
        enqueue_pending_notification(
            value=f"<teammate-message teammate_id={quoteattr(sender)}>{escape(text)}</teammate-message>",
            mode="teammate-message",
            scope=self.context.runtime_tasks,
        )

    def _receive(self, agent_id: str, message: TeammateMessage) -> None:
        with self.lock:
            sender_id = self.identities.get(message.from_)
            if sender_id is None or self.closed:
                return
            if message.protocol:
                expected = (
                    self.controls.pop(message.control_id, None)
                    if isinstance(message.control_id, str)
                    else None
                )
                if expected != (agent_id, message.from_, message.text):
                    return
            if agent_id == self.lead_id:
                self._notice(message.from_, message.text)
                return
            state = self.context.runtime_tasks.get(agent_id)
            if not isinstance(
                state, InProcessTeammateTaskState
            ) or is_terminal_task_status(state.status):
                return
            if message.protocol:
                try:
                    envelope = json.loads(message.text)
                except (ValueError, TypeError):
                    return
                if not isinstance(envelope, dict):
                    return
                if envelope.get("from") != message.from_:
                    return
                kind = envelope.get("type")
                if kind == "plan_approval_response":
                    if (
                        sender_id != self.lead_id
                        or envelope.get("request_id") != state.plan_request_id
                    ):
                        return
                    if not isinstance(envelope.get("approve"), bool):
                        return
                    mode = (
                        envelope.get("permission_mode")
                        if envelope["approve"]
                        else state.permission_mode
                    )
                    if mode not in {
                        "plan",
                        "default",
                        "acceptEdits",
                        "dontAsk",
                        "bypassPermissions",
                    }:
                        return
                    if (
                        mode == "bypassPermissions"
                        and self.context.permission_context.mode != mode
                    ):
                        return
                    self.context.runtime_tasks.update(
                        agent_id,
                        lambda prev: replace(
                            prev,
                            awaiting_plan_approval=False,
                            plan_request_id=None,
                            permission_mode=mode,
                        ),
                    )
                    active = self.contexts.get(agent_id)
                    if active is not None:
                        active.permission_context = replace(
                            active.permission_context, mode=mode
                        )
                elif kind == "shutdown_request":
                    if sender_id != self.lead_id or envelope.get(
                        "request_id"
                    ) != self.shutdown_requests.get(state.identity.agent_name):
                        return
                    self.context.runtime_tasks.update(
                        agent_id, lambda prev: replace(prev, shutdown_requested=True)
                    )
            wrapped = f"<teammate-message teammate_id={quoteattr(message.from_)}>{escape(message.text)}</teammate-message>"
            self.context.runtime_tasks.update(
                agent_id,
                lambda prev: replace(
                    prev,
                    pending_user_messages=[*prev.pending_user_messages, wrapped],
                ),
            )

    def _poll(self, stop_event: threading.Event) -> None:
        while not self.stop.is_set() and not stop_event.is_set():
            try:
                with self.lock:
                    recipients = {
                        member.name: member.agent_id for member in self.team.members
                    }
                sweep_mailboxes(
                    runtime_tasks=self.context.runtime_tasks,
                    workspace_root=self.context.workspace_root,
                    team_name=self.name,
                    recipient_to_agent_id=recipients,
                    deliver=self._receive,
                )
            except Exception:
                logger.exception("team mailbox sweep failed for %s", self.name)
            self.stop.wait(0.05)

    def interrupt_work(self, agent_id: str) -> bool:
        """Cancel this assignment while leaving the persistent teammate alive."""
        state = self.context.runtime_tasks.get(agent_id)
        if not isinstance(state, InProcessTeammateTaskState) or is_terminal_task_status(
            state.status
        ):
            return False
        if state.current_work_abort_controller is not None:
            state.current_work_abort_controller.abort("assignment interrupted")
        return True

    def _progress(
        self, state: InProcessTeammateTaskState, *, status: str, activity: str
    ) -> None:
        emit = self.context.agent_progress_emit
        if emit is not None:
            try:
                tracking = self.context.query_tracking
                emit(
                    {
                        "agent_id": state.id,
                        "depth": tracking.depth + 1 if tracking else 0,
                        "name": state.identity.agent_name,
                        "description": state.description,
                        "subagent_type": state.selected_agent.agent_type,
                        "status": status,
                        "activity": activity,
                        "tool_use_id": state.tool_use_id,
                    }
                )
            except Exception:
                logger.debug("teammate progress emit failed", exc_info=True)

    def spawn(
        self, params: RunAgentParams, description: str, *, mode: str | None = None
    ) -> dict[str, Any]:
        """Start a persistent in-process teammate under session admission."""
        from src.permissions.types import EXTERNAL_PERMISSION_MODES

        name = params.agent_name
        if not name:
            raise ToolInputError("A teammate requires a name")
        if not params.agent_id:
            raise ToolInputError("A teammate requires an admitted agent ID")
        get_inbox_path(name, self.name, self.context.workspace_root)
        if mode is not None and mode not in EXTERNAL_PERMISSION_MODES:
            raise ToolInputError("Invalid teammate permission mode")
        if mode == "bypassPermissions" and self.context.permission_context.mode != mode:
            raise ToolInputError("The leader cannot grant bypassPermissions")
        with self.lock:
            if self.closed or self._member(name) is not None:
                raise ToolInputError(f"Teammate name {name!r} is unavailable")
            agent_id = params.agent_id
            controller = params.abort_controller or AbortController()
            permission_mode = mode or self.context.permission_context.mode
            identity = TeammateIdentity(
                agent_id,
                name,
                self.name,
                self.context.session_id or "",
                plan_mode_required=mode == "plan",
            )
            state = InProcessTeammateTaskState(
                id=agent_id,
                status="running",
                description=description,
                start_time=time.time(),
                output_file=get_agent_transcript_path(agent_id),
                identity=identity,
                prompt=params.prompt,
                model=params.model,
                selected_agent=params.agent_definition,
                tool_use_id=params.parent_context.tool_use_id,
                abort_controller=controller,
                permission_mode=permission_mode,
            )
            self.context.runtime_tasks.upsert(state)
            self.team = add_member(self.team, TeamMember(agent_id, name))
            self.identities[name] = agent_id
            try:
                write_team_file(self.team, self.context.workspace_root)
                self.workers[agent_id] = self.context.task_manager.start(
                    name=f"teammate:{name}",
                    target=lambda _stop: asyncio.run(self._run(params, state)),
                )
            except BaseException:
                self.team = remove_member(self.team, agent_id)
                self.context.runtime_tasks.remove(agent_id)
                self.identities.pop(name, None)
                try:
                    write_team_file(self.team, self.context.workspace_root)
                except OSError:
                    logger.exception("Could not persist failed teammate launch")
                raise
            return {
                "status": "teammate_spawned",
                "agent_id": agent_id,
                "name": name,
                "team_name": self.name,
                "output_file": state.output_file,
            }

    async def _run(
        self, params: RunAgentParams, initial: InProcessTeammateTaskState
    ) -> None:
        agent_id, name = initial.id, initial.identity.agent_name
        registry = self.context.runtime_tasks
        controller = initial.abort_controller
        transcript = None
        history: list[Any] = []
        tracker = ProgressTracker()
        child_context = None
        error = None
        try:
            transcript = TranscriptWriter(initial.output_file)
            # Preserve definition scoping, adding only teammate orchestration tools.
            allowed = resolve_agent_tools(
                params.agent_definition, params.available_tools, is_async=False
            ).resolved_tools
            tools = {
                tool.name: tool
                for tool in allowed
                if tool.name not in {"TeamCreate", "TeamDelete"}
            }
            for tool in params.available_tools:
                if tool.name in {"Agent", "ExitPlanMode"}:
                    tools[tool.name] = tool
            scoped_registry = ToolRegistry(tools.values())
            identity_prompt = (
                f"\nYou are {name}, a persistent teammate in team {self.name}. "
                "Use SendMessage to deliver findings to team-lead or peers; final prose is not forwarded. "
                "Update assigned tasks with TaskUpdate. When idle, wait for another assignment. "
                "For shutdown_request, respond to team-lead with shutdown_response, the exact request_id, "
                "and approve true, or approve false with a reason. A request alone does not stop you. "
                "ExitPlanMode submits your plan to the leader; stay in plan mode until the matching decision."
            )
            base_prompt = get_agent_system_prompt(params.agent_definition)
            system_prompt = (
                [*base_prompt, {"type": "text", "text": identity_prompt}]
                if isinstance(base_prompt, list)
                else base_prompt + identity_prompt
            )
            prompt = params.prompt
            first = True
            while not controller.signal.aborted:
                state = registry.get(agent_id)
                if state is None or state.shutdown_approved:
                    break
                if not first:
                    pending = drain_teammate_messages(agent_id, registry)
                    from src.utils.message_queue_manager import (
                        drain_pending_notifications,
                    )

                    pending.extend(
                        note.value
                        for note in drain_pending_notifications(
                            scope=registry, recipient=agent_id
                        )
                    )
                    if not pending and not state.awaiting_plan_approval:
                        assignment = claim_next_task(self.context, name)
                        if assignment:
                            pending = ["Task assigned: " + json.dumps(assignment)]
                    if not pending:
                        await asyncio.sleep(0.05)
                        continue
                    prompt = "\n\n".join(pending)
                first = False
                state = registry.get(agent_id)
                work_abort = create_child_abort_controller(controller)
                registry.update(
                    agent_id,
                    lambda prev: replace(
                        prev, is_idle=False, current_work_abort_controller=work_abort
                    ),
                )
                user_message = UserMessage(content=prompt)
                history.append(user_message)
                transcript.append(user_message)

                def remember(context: Any) -> None:
                    nonlocal child_context
                    child_context = context
                    self.contexts[agent_id] = context

                turn = replace(
                    params,
                    prompt="",
                    context_messages=list(history),
                    is_async=True,
                    is_teammate=True,
                    retained_context=child_context,
                    on_context=remember,
                    abort_controller=work_abort,
                    permission_mode_override=state.permission_mode,
                    available_tools=list(tools.values()),
                    tool_registry=scoped_registry,
                    use_exact_tools=True,
                    system_prompt_override=system_prompt,
                )
                try:
                    async for message in run_agent(turn):
                        history.append(message)
                        transcript.append(message)
                        if isinstance(message, AssistantMessage):
                            update_progress_from_message(tracker, message)
                        registry.update(
                            agent_id,
                            lambda prev: replace(
                                prev,
                                messages=append_capped_message(prev.messages, message),
                                progress=get_progress_update(tracker),
                            ),
                        )
                        self.context.agent_supervisor.set_tool_count(
                            agent_id, tracker.tool_use_count
                        )
                except Exception as exc:
                    logger.exception("teammate %s work turn failed", name)
                    self._notice(name, f"Work turn failed: {exc}")
                finally:
                    work_abort.abort(
                        "work turn finished"
                    )  # detach the lifecycle listener
                state = registry.get(agent_id)
                if (
                    state is None
                    or controller.signal.aborted
                    or state.shutdown_approved
                ):
                    break
                registry.update(agent_id, lambda prev: replace(prev, is_idle=True))
                self._progress(
                    state,
                    status="running",
                    activity="Idle; waiting for another assignment",
                )
                self._notice(name, "Teammate is idle and available for more work.")
                for callback in state.on_idle_callbacks:
                    callback()
        except BaseException as exc:
            error = str(exc)
            logger.exception("teammate %s lifecycle failed", name)
        finally:
            from src.tasks.local_shell import kill_shell_tasks_for_agent

            await kill_shell_tasks_for_agent(agent_id, registry)
            if params.worktree is not None:
                params.worktree.close()
                if params.worktree.retained:
                    self._notice(name, params.worktree.notice())
            if transcript is not None:
                transcript.close()
            with self.lock:
                state = registry.get(agent_id)
                status = (
                    "failed"
                    if error
                    else "completed" if state and state.shutdown_approved else "killed"
                )
                try:
                    release_tasks(self.context, name)
                except Exception:
                    logger.exception("Could not release %s's task assignments", name)
                self.context.agent_supervisor.release(agent_id)
                if state is not None:
                    registry.update(
                        agent_id,
                        lambda prev: replace(
                            prev,
                            status=status,
                            end_time=time.time(),
                            error=error,
                            is_idle=False,
                        ),
                    )
                self.team = remove_member(self.team, agent_id)
                try:
                    write_team_file(self.team, self.context.workspace_root)
                except OSError:
                    logger.exception("Could not persist %s's roster removal", name)
                self.contexts.pop(agent_id, None)
                self.shutdown_requests.pop(name, None)
                self._notice(name, f"Teammate exited ({status}).")
                self._progress(
                    initial,
                    status="interrupted" if status == "killed" else status,
                    activity="Exited",
                )

    def delete(self) -> None:
        """Delete only after every worker has exited and released its resources."""
        with self.lock:
            active = [
                member.name
                for member in self.team.members
                if member.agent_id != self.lead_id
            ]
            if active:
                raise ToolInputError(
                    "Stop active teammates before TeamDelete: " + ", ".join(active)
                )
            self.closed = True
            self.stop.set()
        if self.poller is not None:
            self.poller.thread.join(timeout=2)
        with self.lock:
            get_team_file_path(self.context.workspace_root).unlink(missing_ok=True)
            inbox = get_inbox_path(
                "team-lead", self.name, self.context.workspace_root
            ).parent
            shutil.rmtree(inbox, ignore_errors=True)
            if self.context.task_board_path is not None:
                self.context.task_board_path.unlink(missing_ok=True)
            self.context.agent_id, self.context.tasks, self.context.task_board_path = (
                self.previous
            )
            self.context.team = None
            self.context.team_runtime = None


def drain_teammate_messages(agent_id: str, registry: Any) -> list[str]:
    """Take a teammate's accepted inbox messages at a model-turn boundary."""
    messages: list[str] = []

    def drain(previous: Any) -> Any:
        if not isinstance(previous, InProcessTeammateTaskState):
            return previous
        messages.extend(previous.pending_user_messages)
        return replace(previous, pending_user_messages=[])

    registry.update(agent_id, drain)
    return messages
