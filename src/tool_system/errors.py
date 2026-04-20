from __future__ import annotations


class ToolError(Exception):
    pass


class ToolInputError(ToolError):
    pass


class ToolPermissionError(ToolError):
    pass


class ToolExecutionError(ToolError):
    pass

