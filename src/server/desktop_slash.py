"""Server-side slash-command dispatch for the desktop gateway.

The desktop renderer fulfils some commands locally (new chat, model picker,
…) and sends the rest to the backend via ``slash.exec`` / ``command.dispatch``.
This ports the TUI client's ``dispatchSlash`` map (``ui-tui/src/gatewayClient
.ts``) to the server: each command relays to the matching agent control and
formats a one-line result the desktop prints. Anything without an explicit
mapping falls through to ``skill_command`` — the universal skill-expansion
path — so user skills and workflow commands work too.

Returns one of:
- ``{"output": str, "type": "exec"}`` — printed as command output,
- ``{"message": str, "name": str, "type": "skill"}`` — submitted as a turn,
- ``{"output": str, "type": "exec"}`` on any error (never raises to the UI).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

ControlQuery = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _out(text: str) -> dict[str, Any]:
    return {"output": text, "type": "exec"}


def _num(arg: str | None, default: int = 1) -> int:
    try:
        return int(str(arg).strip()) if arg else default
    except (TypeError, ValueError):
        return default


async def dispatch_slash(control: ControlQuery, name: str, arg: str | None) -> dict[str, Any]:
    """Dispatch one slash command against the agent, mirroring the TUI map."""
    name = name.lstrip("/").strip().lower()
    arg = (arg or "").strip() or None

    async def skill_fallback() -> dict[str, Any]:
        reply = await control("skill_command", {"name": name, "args": arg or ""})
        if isinstance(reply, dict) and reply.get("ok") and reply.get("prompt"):
            return {"message": str(reply["prompt"]), "name": name, "type": "skill"}
        return _out(f"/{name}: not available")

    try:
        if name == "clear":
            r = await control("clear", {})
            if not isinstance(r, dict) or r.get("ok") is False:
                return _out(f"clear: {(r or {}).get('error', 'backend not ready')}")
            return _out("Conversation cleared.")

        if name == "compact":
            r = await control("compact", {"instructions": arg})
            saved = (r or {}).get("tokens_saved") if isinstance(r, dict) else None
            return _out(f"Compacted{f' (saved ~{saved} tokens)' if saved else ''}.")

        if name in ("context", "usage"):
            r = await control("get_context_usage", {})
            r = r if isinstance(r, dict) else {}
            pct = "?" if r.get("percentage") is None else round(r["percentage"])
            return _out(f"Context: {r.get('total_tokens', '?')}/{r.get('max_tokens', '?')} tokens ({pct}%).")

        if name == "cost":
            r = await control("cost", {})
            if not isinstance(r, dict) or not r:
                return _out("Cost totals unavailable (backend not ready).")
            cost = r.get("total_cost_usd", 0.0)
            return _out(f"Total cost: ${cost:.4f} · {r.get('num_turns', 0)} turns.")

        if name == "eco":
            r = await control("eco", {"arg": arg or ""})
            if not isinstance(r, dict) or not r:
                return _out("eco: backend not ready")
            if r.get("ok") is False:
                return _out(f"eco: {r.get('error', 'failed')}")
            return _out(str(r.get("text") or f"Eco mode {'on' if r.get('enabled') else 'off'}."))

        if name == "effort":
            r = await control("set_effort", {"effort": arg})
            r = r if isinstance(r, dict) else {}
            if r.get("ok") is False:
                return _out(f"effort: {r.get('error', 'invalid value')}")
            if r.get("effort") == "ultracode":
                return _out("Ultracode on: workflow auto-orchestration for this session.")
            return _out(f"Reasoning effort: {r.get('effort', arg or 'unchanged')}.")

        if name == "thinking":
            r = await control("set_thinking", {"action": arg or "toggle"})
            r = r if isinstance(r, dict) else {}
            note = f" {r['note']}" if r.get("note") else ""
            return _out(f"Thinking {'on' if r.get('thinking') else 'off'}.{note}")

        if name == "provider":
            r = await control("set_provider", {"provider": arg})
            r = r if isinstance(r, dict) else {}
            model = f" (model {r['model']})" if r.get("model") else ""
            return _out(f"Provider: {r.get('provider', arg or '(unchanged)')}{model}.")

        if name in ("rewind", "undo"):
            r = await control("rewind", {"turns": _num(arg)})
            r = r if isinstance(r, dict) else {}
            return _out(f"Rewound {r.get('removed', 0)} turn(s).")

        if name == "insights":
            r = await control("insights", {})
            r = r if isinstance(r, dict) else {}
            return _out(str(r.get("insights")) if r.get("insights") else "No insights available.")

        if name == "knowledge":
            r = await control("knowledge", {"action": arg or "status"})
            r = r if isinstance(r, dict) else {}
            bits = [b for b in (
                f"enabled={r['enabled']}" if r.get("enabled") is not None else "",
                f"semantic={r['semantic']}" if r.get("semantic") is not None else "",
            ) if b]
            return _out("Knowledge: " + (", ".join(bits) if bits else str(r.get("text", "ok"))))

        if name in ("advisor", "fusion", "vision"):
            r = await control(name, {"arg": arg or ""})
            if not isinstance(r, dict) or not r:
                return _out(f"{name}: backend not ready")
            return _out(str(r.get("text") or r.get("error") or f"{name}: no response"))

        if name in ("goal", "subgoal"):
            r = await control(name, {"arg": arg or ""})
            if not isinstance(r, dict) or not r:
                return _out(f"{name}: backend not ready")
            return _out(str(r.get("text") or r.get("error") or f"{name}: no response"))

        if name == "memory":
            r = await control("memory_manage", {"arg": arg or ""})
            if not isinstance(r, dict) or not r:
                return _out("memory: backend not ready")
            return _out(str(r.get("text") or r.get("error") or "memory: no response"))

        if name == "bg":
            if arg:
                r = await control("bg_agent", {"command": arg})
                r = r if isinstance(r, dict) else {}
                return _out(f"Started background agent {r.get('id', '')}.")
            r = await control("bg_run", {"action": "list"})
            r = r if isinstance(r, dict) else {}
            procs = r.get("processes") or r.get("tasks") or []
            return _out(f"{len(procs)} background task(s).")

        if name == "version":
            from src import __version__

            return _out(f"ClawCodex {__version__}")

        if name == "interrupt" or name == "stop":
            await control("interrupt", {})
            return _out("Interrupted.")

        return await skill_fallback()
    except Exception as exc:  # noqa: BLE001 — a bad command must not raise to the UI
        return _out(f"/{name}: {exc}")


__all__ = ["dispatch_slash"]
