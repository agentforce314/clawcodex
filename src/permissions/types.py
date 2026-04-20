from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


PermissionMode = Literal[
    "default",
    "plan",
    "acceptEdits",
    "bypassPermissions",
    "dontAsk",
]

PERMISSION_MODES: tuple[PermissionMode, ...] = (
    "default",
    "plan",
    "acceptEdits",
    "bypassPermissions",
    "dontAsk",
)

EXTERNAL_PERMISSION_MODES: tuple[PermissionMode, ...] = (
    "acceptEdits",
    "bypassPermissions",
    "default",
    "dontAsk",
    "plan",
)

PermissionBehavior = Literal["allow", "deny", "ask"]

PermissionRuleSource = Literal[
    "userSettings",
    "projectSettings",
    "localSettings",
    "flagSettings",
    "policySettings",
    "cliArg",
    "command",
    "session",
]

PERMISSION_RULE_SOURCES: tuple[PermissionRuleSource, ...] = (
    "userSettings",
    "projectSettings",
    "localSettings",
    "flagSettings",
    "policySettings",
    "cliArg",
    "command",
    "session",
)


@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    rule_content: str | None = None


@dataclass(frozen=True)
class PermissionRule:
    source: PermissionRuleSource
    rule_behavior: PermissionBehavior
    rule_value: PermissionRuleValue


PermissionUpdateDestination = Literal[
    "userSettings",
    "projectSettings",
    "localSettings",
    "session",
    "cliArg",
]


@dataclass(frozen=True)
class PermissionUpdateAddRules:
    type: Literal["addRules"] = "addRules"
    destination: PermissionUpdateDestination = "session"
    rules: tuple[PermissionRuleValue, ...] = ()
    behavior: PermissionBehavior = "allow"


@dataclass(frozen=True)
class PermissionUpdateReplaceRules:
    type: Literal["replaceRules"] = "replaceRules"
    destination: PermissionUpdateDestination = "session"
    rules: tuple[PermissionRuleValue, ...] = ()
    behavior: PermissionBehavior = "allow"


@dataclass(frozen=True)
class PermissionUpdateRemoveRules:
    type: Literal["removeRules"] = "removeRules"
    destination: PermissionUpdateDestination = "session"
    rules: tuple[PermissionRuleValue, ...] = ()
    behavior: PermissionBehavior = "allow"


@dataclass(frozen=True)
class PermissionUpdateSetMode:
    type: Literal["setMode"] = "setMode"
    destination: PermissionUpdateDestination = "session"
    mode: PermissionMode = "default"


PermissionUpdate = Union[
    PermissionUpdateAddRules,
    PermissionUpdateReplaceRules,
    PermissionUpdateRemoveRules,
    PermissionUpdateSetMode,
]


@dataclass(frozen=True)
class AdditionalWorkingDirectory:
    path: str
    source: PermissionRuleSource = "session"


ToolPermissionRulesBySource = dict[PermissionRuleSource, list[str]]


@dataclass(frozen=True)
class RuleDecisionReason:
    type: Literal["rule"] = "rule"
    rule: PermissionRule = field(default_factory=lambda: PermissionRule(
        source="session", rule_behavior="deny", rule_value=PermissionRuleValue(tool_name="")
    ))


@dataclass(frozen=True)
class ModeDecisionReason:
    type: Literal["mode"] = "mode"
    mode: PermissionMode = "default"


@dataclass(frozen=True)
class SafetyCheckDecisionReason:
    type: Literal["safetyCheck"] = "safetyCheck"
    reason: str = ""
    classifier_approvable: bool = False


@dataclass(frozen=True)
class HookDecisionReason:
    type: Literal["hook"] = "hook"
    hook_name: str = ""
    reason: str | None = None


@dataclass(frozen=True)
class AsyncAgentDecisionReason:
    type: Literal["asyncAgent"] = "asyncAgent"
    reason: str = ""


@dataclass(frozen=True)
class WorkingDirDecisionReason:
    type: Literal["workingDir"] = "workingDir"
    reason: str = ""


@dataclass(frozen=True)
class OtherDecisionReason:
    type: Literal["other"] = "other"
    reason: str = ""


@dataclass(frozen=True)
class SubcommandResultsDecisionReason:
    type: Literal["subcommandResults"] = "subcommandResults"
    reasons: dict[str, Any] = field(default_factory=dict)


PermissionDecisionReason = Union[
    RuleDecisionReason,
    ModeDecisionReason,
    SafetyCheckDecisionReason,
    HookDecisionReason,
    AsyncAgentDecisionReason,
    WorkingDirDecisionReason,
    OtherDecisionReason,
    SubcommandResultsDecisionReason,
]


@dataclass
class PermissionAllowDecision:
    behavior: Literal["allow"] = "allow"
    updated_input: dict[str, Any] | None = None
    decision_reason: PermissionDecisionReason | None = None
    tool_use_id: str | None = None


@dataclass
class PermissionAskDecision:
    behavior: Literal["ask"] = "ask"
    message: str = ""
    updated_input: dict[str, Any] | None = None
    decision_reason: PermissionDecisionReason | None = None
    suggestions: list[PermissionUpdate] | None = None
    blocked_path: str | None = None


@dataclass
class PermissionDenyDecision:
    behavior: Literal["deny"] = "deny"
    message: str = ""
    decision_reason: PermissionDecisionReason | None = None
    tool_use_id: str | None = None


PermissionDecision = Union[
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
]


@dataclass
class PermissionPassthroughResult:
    behavior: Literal["passthrough"] = "passthrough"
    message: str = ""
    decision_reason: PermissionDecisionReason | None = None
    suggestions: list[PermissionUpdate] | None = None
    blocked_path: str | None = None


PermissionResult = Union[
    PermissionDecision,
    PermissionPassthroughResult,
]


def _empty_rules_by_source() -> ToolPermissionRulesBySource:
    return {}


@dataclass
class ToolPermissionContext:
    mode: PermissionMode = "default"
    additional_working_directories: dict[str, AdditionalWorkingDirectory] = field(
        default_factory=dict
    )
    always_allow_rules: ToolPermissionRulesBySource = field(
        default_factory=_empty_rules_by_source
    )
    always_deny_rules: ToolPermissionRulesBySource = field(
        default_factory=_empty_rules_by_source
    )
    always_ask_rules: ToolPermissionRulesBySource = field(
        default_factory=_empty_rules_by_source
    )
    is_bypass_permissions_mode_available: bool = False
    should_avoid_permission_prompts: bool = False

    @classmethod
    def from_iterables(
        cls,
        deny_names: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
    ) -> "ToolPermissionContext":
        deny_rules: dict[str, list[str]] = {}
        names = list(deny_names or [])
        if names:
            deny_rules["session"] = names
        return cls(always_deny_rules=deny_rules)

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        for source_rules in self.always_deny_rules.values():
            for rule_str in source_rules:
                if rule_str.lower() == lowered:
                    return True
        return False
