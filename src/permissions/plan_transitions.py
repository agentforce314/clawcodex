"""Plan-mode permission transitions.

Ports the plan-mode arms of ``typescript/src/utils/permissions/
permissionSetup.ts``:

* :func:`prepare_context_for_plan_mode` — ``prepareContextForPlanMode``
  (:1463-1494), the plain (non-classifier) branch: stash the current mode as
  ``pre_plan_mode`` so ExitPlanMode can restore it.
* :func:`transition_permission_mode` — ``transitionPermissionMode``
  (:598-647) minus the auto-mode/classifier handling (the port's auto mode is
  not cycle-reachable — see ``cycle.py``): fires the plan enter/exit
  attachment flags and manages the ``pre_plan_mode`` stash.

Both are pure (return a new context; input unchanged) to match the
``apply_permission_update`` functional contract — callers rebind
``tool_context.permission_context``.

NOTE on the entry-side stash: in TS the ``prepareContextForPlanMode`` call
inside ``transitionPermissionMode`` sits behind ``feature(
'TRANSCRIPT_CLASSIFIER')`` — which the shipping reference build compiles
TRUE (scripts/build.ts:51), so shipping behavior IS "stash on every plan
entry" (shift+tab, /mode, /plan, EnterPlanMode). The port stashes
unconditionally, matching the shipping build minus the auto-mode arms.
"""

from __future__ import annotations

from dataclasses import replace

from src.bootstrap.state import (
    handle_plan_mode_transition,
    set_has_exited_plan_mode,
)

from .types import PermissionMode, ToolPermissionContext


def prepare_context_for_plan_mode(
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """Stash the current mode as ``pre_plan_mode`` ahead of entering plan.

    No-op when already in plan mode (``permissionSetup.ts:1467``). Does NOT
    set ``mode`` itself — the caller applies the ``setMode`` update, exactly
    like the TS call sites.
    """
    if context.mode == "plan":
        return context
    return replace(context, pre_plan_mode=context.mode)


def transition_permission_mode(
    from_mode: PermissionMode | str,
    to_mode: PermissionMode | str,
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """Run mode-switch side effects; return the context to set the mode on.

    Mirrors ``transitionPermissionMode`` (permissionSetup.ts:598-647):

    * same-mode switch → no-op (a plan→plan set_permission_mode must not
      fire the leave branch);
    * ``handle_plan_mode_transition`` arms/clears the one-shot
      plan_mode_exit attachment flag;
    * leaving plan → ``set_has_exited_plan_mode(True)`` and clear
      ``pre_plan_mode``;
    * entering plan → :func:`prepare_context_for_plan_mode` (stash).

    The caller still sets ``mode`` on the returned context (TS contract).
    """
    if from_mode == to_mode:
        return context

    handle_plan_mode_transition(str(from_mode), str(to_mode))

    if from_mode == "plan" and to_mode != "plan":
        set_has_exited_plan_mode(True)

    if to_mode == "plan" and from_mode != "plan":
        return prepare_context_for_plan_mode(context)

    if from_mode == "plan" and to_mode != "plan" and context.pre_plan_mode:
        return replace(context, pre_plan_mode=None)

    return context
