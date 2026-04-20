"""
Compaction prompts and summary formatting.

Port of ``typescript/src/services/compact/prompt.ts``.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Preamble that prevents the model from calling tools during summarization
# (port of NO_TOOLS_PREAMBLE in prompt.ts)
# ---------------------------------------------------------------------------
NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)

# ---------------------------------------------------------------------------
# Detailed analysis instruction (chain-of-thought scratchpad)
# ---------------------------------------------------------------------------
DETAILED_ANALYSIS_INSTRUCTION_BASE = """\
Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

DETAILED_ANALYSIS_INSTRUCTION_PARTIAL = """\
Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Analyze the recent messages chronologically. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

# ---------------------------------------------------------------------------
# Base compact prompt — full conversation summarization
# (port of BASE_COMPACT_PROMPT in prompt.ts)
# ---------------------------------------------------------------------------
BASE_COMPACT_PROMPT = f"""\
Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION_BASE}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""

# ---------------------------------------------------------------------------
# Partial compact prompts
# ---------------------------------------------------------------------------

# direction='from' / 'later': summarize recent messages, keep earlier ones
PARTIAL_COMPACT_PROMPT = f"""\
Your task is to create a detailed summary of the RECENT portion of the conversation — the messages that follow earlier retained context. The earlier messages are being kept intact and do NOT need to be summarized. Focus your summary on what was discussed, learned, and accomplished in the recent messages only.

{DETAILED_ANALYSIS_INSTRUCTION_PARTIAL}

Your summary should include the following sections:

1. Primary Request and Intent: Capture the user's explicit requests and intents from the recent messages
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed recently.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages from the recent portion that are not tool results.
7. Pending Tasks: Outline any pending tasks from the recent messages.
8. Current Work: Describe precisely what was being worked on immediately before this summary request.
9. Optional Next Step: List the next step related to the most recent work. Include direct quotes from the most recent conversation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Important Code Snippet]

4. Errors and fixes:
    - [Error description]:
      - [How you fixed it]

5. Problem Solving:
   [Description]

6. All user messages:
    - [Detailed non tool use user message]

7. Pending Tasks:
   - [Task 1]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the RECENT messages only (after the retained earlier context), following this structure and ensuring precision and thoroughness in your response.
"""

# direction='up_to' / 'earlier': summarize earlier messages, keep later ones
PARTIAL_COMPACT_UP_TO_PROMPT = f"""\
Your task is to create a detailed summary of this conversation. This summary will be placed at the start of a continuing session; newer messages that build on this context will follow after your summary (you do not see them here). Summarize thoroughly so that someone reading only your summary and then the newer messages can fully understand what happened and continue the work.

{DETAILED_ANALYSIS_INSTRUCTION_BASE}

Your summary should include the following sections:

1. Primary Request and Intent: Capture the user's explicit requests and intents in detail
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results.
7. Pending Tasks: Outline any pending tasks.
8. Work Completed: Describe what was accomplished by the end of this portion.
9. Context for Continuing Work: Summarize any context, decisions, or state that would be needed to understand and continue the work in subsequent messages.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Important Code Snippet]

4. Errors and fixes:
    - [Error description]:
      - [How you fixed it]

5. Problem Solving:
   [Description]

6. All user messages:
    - [Detailed non tool use user message]

7. Pending Tasks:
   - [Task 1]

8. Work Completed:
   [Description of what was accomplished]

9. Context for Continuing Work:
   [Key context, decisions, or state needed to continue the work]

</summary>
</example>

Please provide your summary following this structure, ensuring precision and thoroughness in your response.
"""


def get_compact_prompt(
    custom_instructions: str | None = None,
    *,
    has_tool_search: bool = False,
) -> str:
    """Return the full compact prompt, optionally appending user instructions."""
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT
    if custom_instructions:
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def get_partial_compact_prompt(
    direction: str = "earlier",
    custom_instructions: str | None = None,
) -> str:
    """
    Return the partial compact prompt for a given direction.

    Args:
        direction: ``"earlier"`` or ``"up_to"`` summarizes a prefix, keeps suffix.
                   ``"later"`` or ``"from"`` summarizes a suffix, keeps prefix.
        custom_instructions: Optional user instructions appended to the prompt.
    """
    if direction in ("earlier", "up_to"):
        template = PARTIAL_COMPACT_UP_TO_PROMPT
    else:
        template = PARTIAL_COMPACT_PROMPT

    prompt = NO_TOOLS_PREAMBLE + template
    if custom_instructions:
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(
    raw_summary: str,
    *,
    files_modified: list[str] | None = None,
    tools_used: list[str] | None = None,
) -> str:
    """
    Format the raw compact summary by stripping the <analysis> scratchpad
    and replacing <summary> XML tags with readable section headers.

    Port of ``formatCompactSummary`` in prompt.ts.
    """
    text = raw_summary

    # Strip analysis section — it's a drafting scratchpad that improves summary
    # quality but has no informational value once the summary is written.
    text = re.sub(r"<analysis>[\s\S]*?</analysis>", "", text)

    # Extract and format summary section
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", text)
    if summary_match:
        content = summary_match.group(1) or ""
        text = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content.strip()}",
            text,
        )

    # Clean up extra whitespace between sections
    text = re.sub(r"\n\n+", "\n\n", text)

    # Append supplementary sections if not already present
    if files_modified and "## Files Modified" not in text:
        files_section = "\n\n## Files Modified\n"
        for f in files_modified:
            files_section += f"- {f}\n"
        text += files_section

    if tools_used and "## Tools Used" not in text:
        unique_tools = sorted(set(tools_used))
        tools_section = "\n\n## Tools Used\n"
        for t in unique_tools:
            count = tools_used.count(t)
            tools_section += f"- {t} (x{count})\n"
        text += tools_section

    return text.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up: bool = False,
    transcript_path: str | None = None,
    recent_messages_preserved: bool = False,
) -> str:
    """
    Build the user-visible summary message inserted after compaction.

    Mirrors ``getCompactUserSummaryMessage`` in the TypeScript reference.
    """
    base = (
        "This session is being continued from a previous conversation "
        "that ran out of context. The summary below covers the earlier "
        "portion of the conversation.\n\n"
        f"{summary}"
    )

    if transcript_path:
        base += (
            f"\n\nIf you need specific details from before compaction "
            f"(like exact code snippets, error messages, or content you generated), "
            f"read the full transcript at: {transcript_path}"
        )

    if recent_messages_preserved:
        base += "\n\nRecent messages are preserved verbatim."

    if suppress_follow_up:
        base += (
            "\nContinue the conversation from where it left off without "
            "asking the user any further questions. Resume directly — do not "
            "acknowledge the summary, do not recap what was happening, do not "
            "preface with \"I'll continue\" or similar. Pick up the last task "
            "as if the break never happened."
        )

    return base
