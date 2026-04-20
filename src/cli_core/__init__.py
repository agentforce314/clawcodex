"""CLI protocol layer shared by interactive and non-interactive entrypoints.

Port of ``typescript/src/cli/`` focused on the pieces that give Claude Code a
stable machine-readable surface (NDJSON stdin/stdout, exit helpers, output
formatting). These modules intentionally contain *no* rendering code so they
can be used from headless pipelines, SDK clients and future Textual-based
TUIs without pulling in Rich.
"""

from .exit import cli_error, cli_ok
from .ndjson import ndjson_safe_dumps
from .structured_io import (
    AssistantEvent,
    HeadlessEvent,
    PartialTextEvent,
    ResultEvent,
    StreamJsonReader,
    StreamJsonWriter,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserInputMessage,
)

__all__ = [
    "cli_error",
    "cli_ok",
    "ndjson_safe_dumps",
    "AssistantEvent",
    "HeadlessEvent",
    "PartialTextEvent",
    "ResultEvent",
    "StreamJsonReader",
    "StreamJsonWriter",
    "SystemEvent",
    "ToolResultEvent",
    "ToolUseEvent",
    "UserInputMessage",
]
