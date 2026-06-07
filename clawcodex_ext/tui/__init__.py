"""Downstream TUI extensions — lazy proxy for circular-safety.

Eager imports avoided to break the circular chain:
  __init__ → .app → .screens.* → ..app → ...
"""

__all__ = ["ClawCodexTUI", "AdvisorEventMessage", "AgentRunFinished",
           "AgentRunStarted", "AssistantChunk", "AssistantMessage",
           "ToolEventMessage"]

_NAME_TO_MODULE = {
    "ClawCodexTUI": "clawcodex_ext.tui.app",
    "AdvisorEventMessage": "clawcodex_ext.tui.messages",
    "AgentRunFinished": "clawcodex_ext.tui.messages",
    "AgentRunStarted": "clawcodex_ext.tui.messages",
    "AssistantChunk": "clawcodex_ext.tui.messages",
    "AssistantMessage": "clawcodex_ext.tui.messages",
    "ToolEventMessage": "clawcodex_ext.tui.messages",
}


def __getattr__(name: str):
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    mod = importlib.import_module(module_name)
    val = getattr(mod, name)
    globals()[name] = val
    return val
