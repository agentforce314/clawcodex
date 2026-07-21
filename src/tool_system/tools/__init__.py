from __future__ import annotations

from ..build_tool import Tool

from .advisor import AdvisorTool
from .agent import make_agent_tool
from .ask_user_question import AskUserQuestionTool
from .bash import BashTool
from .brief import BriefTool
from .config import ConfigTool
from .cron import CronCreateTool, CronDeleteTool, CronListTool
from .edit import EditTool
from .glob import GlobTool
from .grep import GrepTool
from .lsp import LSPTool
from .mcp import MCPTool
from .memory import MemoryTool
from .notebook_edit import NotebookEditTool
from .mcp_resources import ListMcpResourcesTool, ReadMcpResourceTool
from .misc import ClipboardReadTool, ClipboardWriteTool, StatusTool
from .plan_mode import EnterPlanModeTool, ExitPlanModeTool
from .read import ReadTool
from .schedule_wakeup import ScheduleWakeupTool
from .send_message import SendMessageTool
from .send_user_message import SendUserMessageTool
from .skill import SkillTool
from .sleep import SleepTool
from .monitor import MonitorTool
from .structured_output import StructuredOutputTool
from .task_stop import TaskStopTool
from .tasks_v2 import (
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskUpdateTool,
)
from .team import TeamCreateTool, TeamDeleteTool
from .todo_write import TodoWriteTool
from .tool_search import make_tool_search_tool
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool
from .worktree import EnterWorktreeTool, ExitWorktreeTool
from .write import WriteTool

ALL_STATIC_TOOLS: list[Tool] = [
    AdvisorTool,
    AskUserQuestionTool,
    BashTool,
    MonitorTool,
    BriefTool,
    ClipboardReadTool,
    ClipboardWriteTool,
    ConfigTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    EditTool,
    EnterPlanModeTool,
    EnterWorktreeTool,
    ExitPlanModeTool,
    ExitWorktreeTool,
    GlobTool,
    GrepTool,
    LSPTool,
    ListMcpResourcesTool,
    MCPTool,
    MemoryTool,
    NotebookEditTool,
    ReadMcpResourceTool,
    ReadTool,
    ScheduleWakeupTool,
    SendMessageTool,
    SendUserMessageTool,
    SkillTool,
    SleepTool,
    StatusTool,
    # StructuredOutputTool is deliberately NOT registered: it is a
    # "special" tool (TS tools.ts:293-299 specialTools excludes
    # SyntheticOutputTool from getAllBaseTools). The genuine schema
    # path injects a per-call VALIDATING instance
    # (workflow/structured.py make_structured_output_tool); the static
    # unvalidated one exposed interactively was a footgun — its output
    # dead-ends in outbox with no consumer (tools-round critic, C6).
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
    TeamCreateTool,
    TeamDeleteTool,
    TodoWriteTool,
    WebFetchTool,
    WebSearchTool,
    WriteTool,
]

__all__ = [
    "ALL_STATIC_TOOLS",
    "AdvisorTool",
    "AskUserQuestionTool",
    "BashTool",
    "BriefTool",
    "ClipboardReadTool",
    "ClipboardWriteTool",
    "ConfigTool",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "EditTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "GlobTool",
    "GrepTool",
    "LSPTool",
    "ListMcpResourcesTool",
    "MCPTool",
    "MemoryTool",
    "NotebookEditTool",
    "ReadMcpResourceTool",
    "ReadTool",
    "ScheduleWakeupTool",
    "SendMessageTool",
    "SendUserMessageTool",
    "SkillTool",
    "SleepTool",
    "StatusTool",
    "MonitorTool",
    "StructuredOutputTool",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskOutputTool",
    "TaskStopTool",
    "TaskUpdateTool",
    "TeamCreateTool",
    "TeamDeleteTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
    "WriteTool",
    "make_agent_tool",
    "make_tool_search_tool",
]
