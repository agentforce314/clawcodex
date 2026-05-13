"""Combined private-and-team memory prompt builder.

Ports `typescript/src/memdir/teamMemPrompts.ts`. Emitted when both
auto-memory and team-memory are active. Uses a COMBINED variant of the
type taxonomy that adds ``<scope>`` tags and swaps the single-directory
prose for a two-directory variant.

The :data:`TYPES_SECTION_COMBINED` constant lives in this module rather
than in ``memory_types.py`` because it is consumed exclusively here.
Eval-tuned prose — do NOT paraphrase any of the strings.
"""

from __future__ import annotations

from typing import Iterable

from .memdir import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
)
from .memory_types import (
    MEMORY_DRIFT_CAVEAT,
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    WHAT_NOT_TO_SAVE_SECTION,
)
from .paths import get_auto_mem_path
from .team_mem_paths import get_team_mem_path

__all__ = [
    "TYPES_SECTION_COMBINED",
    "DIRS_EXIST_GUIDANCE",
    "build_combined_memory_prompt",
]


# Plural form of ``DIR_EXISTS_GUIDANCE`` from memdir.py. Used in the
# combined-prompt header sentence that mentions both directories.
DIRS_EXIST_GUIDANCE = (
    "Both directories already exist — write to them directly with the "
    "Write tool (do not run mkdir or check for their existence)."
)


# Verbatim from ``typescript/src/memdir/memoryTypes.ts``. Adds ``<scope>``
# tags per type and uses ``private``/``team`` qualifiers in examples.
TYPES_SECTION_COMBINED: tuple[str, ...] = (
    "## Types of memory",
    "",
    "There are several discrete types of memory that you can store in your memory system. Each type below declares a <scope> of `private`, `team`, or guidance for choosing between the two.",
    "",
    "<types>",
    "<type>",
    "    <name>user</name>",
    "    <scope>always private</scope>",
    "    <description>Contain information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective. Your goal in reading and writing these memories is to build up an understanding of who the user is and how you can be most helpful to them specifically. For example, you should collaborate with a senior software engineer differently than a student who is coding for the very first time. Keep in mind, that the aim here is to be helpful to the user. Avoid writing memories about the user that could be viewed as a negative judgement or that are not relevant to the work you're trying to accomplish together.</description>",
    "    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>",
    "    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>",
    "    <examples>",
    "    user: I'm a data scientist investigating what logging we have in place",
    "    assistant: [saves private user memory: user is a data scientist, currently focused on observability/logging]",
    "",
    "    user: I've been writing Go for ten years but this is my first time touching the React side of this repo",
    "    assistant: [saves private user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>feedback</name>",
    "    <scope>default to private. Save as team only when the guidance is clearly a project-wide convention that every contributor should follow (e.g., a testing policy, a build invariant), not a personal style preference.</scope>",
    "    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious. Before saving a private feedback memory, check that it doesn't contradict a team feedback memory — if it does, either don't save it or note the override explicitly.</description>",
    "    <when_to_save>Any time the user corrects your approach (\"no not that\", \"don't\", \"stop doing X\") OR confirms a non-obvious approach worked (\"yes exactly\", \"perfect, keep doing that\", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>",
    "    <how_to_use>Let these memories guide your behavior so that the user and other users in the project do not need to offer the same guidance twice.</how_to_use>",
    "    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>",
    "    <examples>",
    "    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed",
    "    assistant: [saves team feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration. Team scope: this is a project testing policy, not a personal preference]",
    "",
    "    user: stop summarizing what you just did at the end of every response, I can read the diff",
    "    assistant: [saves private feedback memory: this user wants terse responses with no trailing summaries. Private because it's a communication preference, not a project convention]",
    "",
    "    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn",
    "    assistant: [saves private feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>project</name>",
    "    <scope>private or team, but strongly bias toward team</scope>",
    "    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work users are working on within this working directory.</description>",
    "    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., \"Thursday\" → \"2026-03-05\"), so the memory remains interpretable after time passes.</when_to_save>",
    "    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request, anticipate coordination issues across users, make better informed suggestions.</how_to_use>",
    "    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>",
    "    <examples>",
    "    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch",
    "    assistant: [saves team project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]",
    "",
    "    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements",
    "    assistant: [saves team project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>reference</name>",
    "    <scope>usually team</scope>",
    "    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>",
    "    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>",
    "    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>",
    "    <examples>",
    "    user: check the Linear project \"INGEST\" if you want context on these tickets, that's where we track all pipeline bugs",
    "    assistant: [saves team reference memory: pipeline bugs are tracked in Linear project \"INGEST\"]",
    "",
    "    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone",
    "    assistant: [saves team reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]",
    "    </examples>",
    "</type>",
    "</types>",
    "",
)


def _how_to_save_section(skip_index: bool) -> list[str]:
    if skip_index:
        return [
            "## How to save memories",
            "",
            "Write each memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Keep the name, description, and type fields in memory files up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
    return [
        "## How to save memories",
        "",
        "Saving a memory is a two-step process:",
        "",
        "**Step 1** — write the memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
        "",
        *MEMORY_FRONTMATTER_EXAMPLE,
        "",
        f"**Step 2** — add a pointer to that file in the same directory's `{ENTRYPOINT_NAME}`. Each directory (private and team) has its own `{ENTRYPOINT_NAME}` index — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. They have no frontmatter. Never write memory content directly into a `{ENTRYPOINT_NAME}`.",
        "",
        f"- Both `{ENTRYPOINT_NAME}` indexes are loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep them concise",
        "- Keep the name, description, and type fields in memory files up-to-date with the content",
        "- Organize memory semantically by topic, not chronologically",
        "- Update or remove memories that turn out to be wrong or outdated",
        "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
    ]


def build_combined_memory_prompt(
    extra_guidelines: Iterable[str] | None = None,
    skip_index: bool = False,
) -> str:
    """Assemble the prompt section for combined private + team memory.

    Closed four-type taxonomy (user / feedback / project / reference)
    with per-type ``<scope>`` guidance embedded in the type blocks.
    Mirrors TS ``buildCombinedMemoryPrompt`` line-for-line — the prose
    is eval-tuned and must not be paraphrased.
    """
    auto_dir = get_auto_mem_path()
    team_dir = get_team_mem_path()

    lines: list[str] = [
        "# Memory",
        "",
        f"You have a persistent, file-based memory system with two directories: a private directory at `{auto_dir}` and a shared team directory at `{team_dir}`. {DIRS_EXIST_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        "## Memory scope",
        "",
        "There are two scope levels:",
        "",
        f"- private: memories that are private between you and the current user. They persist across conversations with only this specific user and are stored at the root `{auto_dir}`.",
        f"- team: memories that are shared with and contributed by all of the users who work within this project directory. Team memories are synced at the beginning of every session and they are stored at `{team_dir}`.",
        "",
        *TYPES_SECTION_COMBINED,
        *WHAT_NOT_TO_SAVE_SECTION,
        "- You MUST avoid saving sensitive data within shared team memories. For example, never save API keys or user credentials.",
        "",
        *_how_to_save_section(skip_index),
        "",
        "## When to access memories",
        "- When memories (personal or team) seem relevant, or the user references prior work with them or others in their organization.",
        "- You MUST access memory when the user explicitly asks you to check, recall, or remember.",
        "- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty. Do not apply remembered facts, cite, compare against, or mention memory content.",
        MEMORY_DRIFT_CAVEAT,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        "## Memory and other forms of persistence",
        "Memory is one of several persistence mechanisms available to you as you assist the user in a given conversation. The distinction is often that memory can be recalled in future conversations and should not be used for persisting information that is only useful within the scope of the current conversation.",
        "- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task and would like to reach alignment with the user on your approach you should use a Plan rather than saving this information to memory. Similarly, if you already have a plan within the conversation and you have changed your approach persist that change by updating the plan rather than saving a memory.",
        "- When to use or update tasks instead of memory: When you need to break your work in current conversation into discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for persisting information about the work that needs to be done in the current conversation, but memory should be reserved for information that will be useful in future conversations.",
    ]
    if extra_guidelines:
        for guideline in extra_guidelines:
            if guideline:
                lines.append(guideline)
    return "\n".join(lines)
