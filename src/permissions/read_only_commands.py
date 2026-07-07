"""Read-only Bash command validation — the default-mode auto-allow gate.

Faithful port of the ORIGINAL Claude Code's read-only validator:

* harness: ``typescript/src/tools/BashTool/readOnlyValidation.ts``
  (``isCommandSafeViaFlagParsing:1180``, ``containsUnquotedExpansion:1534``,
  ``isCommandReadOnly:1612``, ``checkReadOnlyConstraints:1810``) and the
  shared flag-walking loop ``validateFlags``
  (``typescript/src/utils/shell/readOnlyCommandValidation.ts:1684``);
* data tables: :mod:`src.permissions.read_only_tables` (COMMAND_ALLOWLIST,
  READONLY_COMMAND_REGEXES, git/rg/docker/pyright read-only tables).

This is what lets ``ls``, ``cat``, ``git status``, ``git diff``, ``grep`` …
run with NO prompt and NO rule in default mode (TS bashPermissions.ts:1136:
"Read-only command is allowed"), which the port previously never did.

Port adaptations, each strictly narrower than TS (a refusal degrades to
"prompt", never to a wider allow):

* TS detects operators via shell-quote's operator tokens; ``shlex`` has no
  operator model, so :func:`_contains_unquoted_operator` pre-refuses any
  unquoted ``< > | & ; ( )`` before tokenization. All TS acceptance paths
  reject those characters anyway (the allowlist path via operator tokens, the
  regex path via ``[^<>()$`|{}&;\\n\\r]`` character classes), so outcomes
  match.
* TS splits compounds with ``splitCommand_DEPRECATED``; this port uses
  :func:`split_chained_command`, which additionally REFUSES substitution /
  subshells / heredocs / ANSI-C quoting (→ not read-only → prompt).
* TS runs ``checkPathConstraints`` (a separate engine this port doesn't have)
  BEFORE its read-only allow, so out-of-project reads still prompt. This port
  substitutes :func:`_paths_within_roots` — every path-looking argument must
  resolve inside the allowed roots, else the command is not auto-allowed.
  Slightly stricter than TS (e.g. ``df /`` prompts here); never looser.
* The TS sandbox-specific git guard (git outside the original cwd while
  sandboxed) is dropped: the port has no sandbox enforcement, and TS itself
  waives the guard when sandboxing is off ("attack is moot").
* TS's "compound writes git-internal paths then runs git" guard is subsumed:
  TS needs it because its splitter STRIPS redirections from sub-commands; this
  port's splitter keeps them in the text, so any write redirect already fails
  the per-sub read-only check.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Sequence

from .bash_suggestions import (
    contains_executable_substitution,
    contains_unquoted_chaining,
    split_chained_command,
)
from .read_only_tables import (
    COMMAND_ALLOWLIST,
    CommandConfig,
    READONLY_COMMAND_REGEXES,
    SAFE_TARGET_COMMANDS_FOR_XARGS,
)

__all__ = [
    "check_read_only_constraints",
    "contains_unquoted_expansion",
    "contains_vulnerable_unc_path",
    "is_command_read_only",
    "is_command_safe_via_flag_parsing",
    "is_current_directory_bare_git_repo",
    "validate_flag_argument",
    "validate_flags",
]

# TS readOnlyCommandValidation.ts:1645
FLAG_PATTERN = re.compile(r"^-[a-zA-Z0-9_-]")


# ---------------------------------------------------------------------------
# validateFlags — port of readOnlyCommandValidation.ts:1650-1893
# ---------------------------------------------------------------------------

def validate_flag_argument(value: str, arg_type: str) -> bool:
    """Port of ``validateFlagArgument`` (readOnlyCommandValidation.ts:1650)."""
    if arg_type == "none":
        return False  # should not be called for 'none'
    if arg_type == "number":
        return re.fullmatch(r"\d+", value) is not None
    if arg_type == "string":
        return True
    if arg_type == "char":
        return len(value) == 1
    if arg_type == "{}":
        return value == "{}"
    if arg_type == "EOF":
        return value == "EOF"
    return False


def validate_flags(
    tokens: list[str],
    start_index: int,
    config: CommandConfig,
    *,
    command_name: str | None = None,
    raw_command: str = "",
    xargs_target_commands: Sequence[str] | None = None,
) -> bool:
    """Port of ``validateFlags`` (readOnlyCommandValidation.ts:1684-1893).

    Walks the flag/argument tokens after the command words and accepts the
    command only when every flag is on the config's safe list with a valid
    argument. Preserves the TS security semantics verbatim: ``hasEquals``
    tracking (``-E=`` provides an EMPTY value, it must not consume the next
    token), bundled short flags must all be no-arg, git ``-<num>`` shorthand,
    grep/rg attached numerics (``-A20``), the xargs safe-target break, and
    ``respects_double_dash=False`` tools that keep validating past ``--``.
    """
    i = start_index
    n = len(tokens)

    while i < n:
        token = tokens[i]
        if not token:
            i += 1
            continue

        # xargs: once the target command is found, stop validating flags.
        if (
            xargs_target_commands is not None
            and command_name == "xargs"
            and (not token.startswith("-") or token == "--")
        ):
            if token == "--" and i + 1 < n:
                i += 1
                token = tokens[i]
            if token and token in xargs_target_commands:
                break
            return False

        if token == "--":
            # Only break when the tool respects POSIX `--` (default). Tools
            # like pyright treat `--` as a path and keep parsing flags after
            # it — breaking would let `pyright -- --createstub os` slip by.
            if config.respects_double_dash:
                i += 1
                break
            i += 1
            continue

        if token.startswith("-") and len(token) > 1 and FLAG_PATTERN.match(token):
            # `-E=` has has_equals=True but an EMPTY inline value: GNU getopt
            # sees `-E` with ATTACHED arg `=`, so we must NOT consume the next
            # token (TS parser-differential fix, :1745-1770).
            has_equals = "=" in token
            flag, _, inline_value = token.partition("=")

            if not flag:
                return False

            flag_arg_type = config.safe_flags.get(flag)

            if flag_arg_type is None:
                # git -<number> shorthand for -n <number>.
                if command_name == "git" and re.fullmatch(r"-\d+", flag):
                    i += 1
                    continue

                # grep/rg attached numeric args (-A20, -B10).
                if (
                    command_name in ("grep", "rg")
                    and flag.startswith("-")
                    and not flag.startswith("--")
                    and len(flag) > 2
                ):
                    potential_flag = flag[:2]
                    potential_value = flag[2:]
                    attached_type = config.safe_flags.get(potential_flag)
                    if attached_type and re.fullmatch(r"\d+", potential_value):
                        if attached_type in ("number", "string"):
                            if validate_flag_argument(potential_value, attached_type):
                                i += 1
                                continue
                            return False

                # Bundled single-letter flags (-nr): ALL must exist and ALL
                # must be no-arg. An arg-taking flag inside a bundle consumes
                # the NEXT token in GNU getopt, which this walker does not
                # model (TS xargs `-rI` RCE fix, :1800-1830).
                if flag.startswith("-") and not flag.startswith("--") and len(flag) > 2:
                    for ch in flag[1:]:
                        single_type = config.safe_flags.get("-" + ch)
                        if single_type is None:
                            return False
                        if single_type != "none":
                            return False
                    i += 1
                    continue
                return False  # unknown flag

            if flag_arg_type == "none":
                if has_equals:
                    return False  # `-FLAG=` supplies a value to a no-arg flag
                i += 1
            else:
                if has_equals:
                    arg_value = inline_value
                    i += 1
                else:
                    nxt = tokens[i + 1] if i + 1 < n else None
                    if nxt is None or (
                        nxt.startswith("-") and len(nxt) > 1 and FLAG_PATTERN.match(nxt)
                    ):
                        return False  # missing required argument
                    arg_value = nxt or ""
                    i += 2

                # String args must not start with '-' (type-confusion guard);
                # git --sort allows a reverse-sort '-key' exception.
                if flag_arg_type == "string" and arg_value.startswith("-"):
                    if not (
                        flag == "--sort"
                        and command_name == "git"
                        and re.match(r"^-[a-zA-Z]", arg_value)
                    ):
                        return False

                if not validate_flag_argument(arg_value, flag_arg_type):
                    return False
        else:
            i += 1  # positional argument (revspec, file path, …)

    return True


# ---------------------------------------------------------------------------
# UNC paths — port of containsVulnerableUncPath
# (readOnlyCommandValidation.ts:1562-1640)
# ---------------------------------------------------------------------------

_UNC_PATTERNS = [
    re.compile(r"\\\\[\w.\[\]:@-]+"),   # \\server\share, \\server@SSL@8443\
    re.compile(r"(?:^|[\s\"'=(])//[\w.\[\]:-]+\.[\w.\[\]:-]+/"),  # //host.tld/share
    re.compile(r"DavWWWRoot", re.IGNORECASE),
]


def contains_vulnerable_unc_path(path_or_command: str) -> bool:
    """UNC / WebDAV path detection (NTLM credential-leak surface).

    Port of ``containsVulnerableUncPath``; POSIX hosts still refuse these so a
    pasted Windows-style path never counts as provably read-only.
    """
    return any(p.search(path_or_command) for p in _UNC_PATTERNS)


# ---------------------------------------------------------------------------
# Unquoted expansion — port of containsUnquotedExpansion
# (readOnlyValidation.ts:1534-1603)
# ---------------------------------------------------------------------------

_EXPANSION_NEXT = re.compile(r"[A-Za-z_@*#?!$0-9-]")


def contains_unquoted_expansion(command: str) -> bool:
    """Unquoted glob (``? * [ ]``) or expandable ``$`` outside single quotes.

    Either can expand at runtime into flags/paths the validators never saw
    (``python *`` → ``python --help``; ``uniq --skip-chars=0$_`` smuggles
    positionals), so such a command is never provably read-only.
    """
    in_single = False
    in_double = False
    escaped = False
    n = len(command)
    for i in range(n):
        ch = command[i]
        if escaped:
            escaped = False
            continue
        # Backslash escapes only OUTSIDE single quotes (bash: '\' is literal
        # inside single quotes; treating it as an escape desyncs the tracker).
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single:
            continue
        # `$` expands unquoted AND inside double quotes.
        if ch == "$":
            nxt = command[i + 1] if i + 1 < n else ""
            if nxt and _EXPANSION_NEXT.match(nxt):
                return True
        if in_double:
            continue
        # Globs are literal inside both quote kinds; only unquoted ones count.
        if ch in "?*[]":
            return True
    return False


# ---------------------------------------------------------------------------
# Unquoted shell operators (port-specific pre-guard; see module docstring)
# ---------------------------------------------------------------------------

def _contains_unquoted_operator(command: str) -> bool:
    """True when an unquoted ``< > | & ; ( )`` appears outside quotes.

    Replaces shell-quote's operator tokens: any redirect/pipe/paren means the
    string is not a single simple command, so it cannot be validated by flag
    walking or the read-only regexes (whose character classes exclude exactly
    these). Backslash-escaped characters are literal (``find \\( … \\)`` is
    handled by the find regex on the raw string, not here — the escaped paren
    is skipped).
    """
    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and ch in "<>|&;()":
            return True
        i += 1
    return False


# ---------------------------------------------------------------------------
# isCommandSafeViaFlagParsing — port of readOnlyValidation.ts:1180-1342
# ---------------------------------------------------------------------------

def _tokenize_simple(command: str) -> list[str] | None:
    """shlex tokenization of an operator-free simple command.

    Callers must have refused unquoted operators/expansion first —
    that is what makes ``shlex.split`` faithful to what bash would exec.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    return tokens


def is_command_safe_via_flag_parsing(command: str) -> bool:
    """Allowlist + strict flag validation (COMMAND_ALLOWLIST path)."""
    tokens = _tokenize_simple(command)
    if not tokens:
        return False

    # Find the config: first table entry (insertion order, multi-word keys
    # like "git diff" included) whose words prefix the tokens — TS iterates
    # Object.entries and takes the first match.
    command_config: CommandConfig | None = None
    command_tokens = 0
    for cmd_pattern, cfg in COMMAND_ALLOWLIST.items():
        cmd_words = cmd_pattern.split(" ")
        if len(tokens) >= len(cmd_words) and tokens[: len(cmd_words)] == cmd_words:
            command_config = cfg
            command_tokens = len(cmd_words)
            break
    if command_config is None:
        return False

    # git ls-remote: reject URL/remote-looking args (data exfiltration).
    if tokens[0] == "git" and len(tokens) > 1 and tokens[1] == "ls-remote":
        for token in tokens[2:]:
            if token and not token.startswith("-"):
                if "://" in token or "@" in token or ":" in token or "$" in token:
                    return False

    # Reject `$` in ANY post-command token (runtime expansion defeats both
    # the flag walker and the callbacks — TS :1262-1303), and brace-expansion
    # obfuscation (`{`+`,` or `{`+`..`).
    for token in tokens[command_tokens:]:
        if not token:
            continue
        if "$" in token:
            return False
        if "{" in token and ("," in token or ".." in token):
            return False

    if not validate_flags(
        tokens,
        command_tokens,
        command_config,
        command_name=tokens[0],
        raw_command=command,
        xargs_target_commands=(
            SAFE_TARGET_COMMANDS_FOR_XARGS if tokens[0] == "xargs" else None
        ),
    ):
        return False

    if command_config.regex is not None and not command_config.regex.search(command):
        return False
    if command_config.regex is None and "`" in command:
        return False
    # Newlines/CRs in grep/rg patterns can be used for injection.
    if (
        command_config.regex is None
        and tokens[0] in ("rg", "grep")
        and re.search(r"[\n\r]", command)
    ):
        return False

    if command_config.additional_command_is_dangerous is not None:
        if command_config.additional_command_is_dangerous(
            command, tokens[command_tokens:]
        ):
            return False

    return True


# ---------------------------------------------------------------------------
# isCommandReadOnly — port of readOnlyValidation.ts:1612-1686
# ---------------------------------------------------------------------------

_GIT_C_FLAG = re.compile(r"\s-c[\s=]")
_GIT_EXEC_PATH = re.compile(r"\s--exec-path[\s=]")
_GIT_CONFIG_ENV = re.compile(r"\s--config-env[\s=]")


def is_command_read_only(command: str) -> bool:
    """True when a single (already-split) command is provably read-only."""
    test = command.strip()
    if test.endswith(" 2>&1"):
        test = test[:-5].strip()
    if not test:
        return False

    if contains_vulnerable_unc_path(test):
        return False
    if contains_unquoted_expansion(test):
        return False
    if _contains_unquoted_operator(test):
        return False

    if is_command_safe_via_flag_parsing(test):
        return True

    for regex in READONLY_COMMAND_REGEXES:
        if regex.search(test):
            # git -c / --exec-path / --config-env can execute arbitrary code
            # via config (core.fsmonitor, diff.external, …).
            if "git" in test and (
                _GIT_C_FLAG.search(test)
                or _GIT_EXEC_PATH.search(test)
                or _GIT_CONFIG_ENV.search(test)
            ):
                return False
            return True
    return False


# ---------------------------------------------------------------------------
# cd / git sub-command detection — port of isNormalizedCdCommand /
# isNormalizedGitCommand (bashPermissions.ts:2570-2634)
# ---------------------------------------------------------------------------

def _strip_env_and_wrappers(command: str) -> str:
    from .check import _normalize_for_deny_ask

    return _normalize_for_deny_ask(command)


# Builtins that re-parse a NAME operand and ARITHMETICALLY evaluate an
# ``arr[EXPR]`` subscript — so ``printf -v 'a[$(id)]'`` / ``test -v 'a[`id`]'``
# / ``[[ 'a[$(id)]' -eq 0 ]]`` / ``read -a 'a[$(id)]'`` / ``unset 'a[$(id)]'``
# / ``wait -p 'a[$(id)]'`` run the substitution even from a SINGLE-QUOTED
# string (which ``contains_executable_substitution`` correctly treats as
# literal for a normal command). Port of TS checkSemantics
# SUBSCRIPT_EVAL_FLAGS / BARE_SUBSCRIPT_NAME_BUILTINS / declaration builtins
# (utils/bash/ast.ts:2143-2185). We take the conservative whole-command view:
# for a sub-command whose head is one of these, a ``[`` together with a
# ``$(`` / backtick / ``${`` anywhere in it is refused. Over-blocks only exotic
# literal-bracket+substitution strings under exactly these builtins (safe —
# just prompts); normal ``printf '%s' x`` / ``test -f x`` / ``read var`` pass.
_NAME_EVAL_BUILTINS: frozenset[str] = frozenset({
    "test", "[", "[[", "printf", "read", "unset", "wait",
    "declare", "typeset", "local", "readonly", "getopts",
})

_SUBSCRIPT_SUBST_RE = re.compile(r"\[[^\]]*(?:\$\(|`|\$\{)")


# /proc/*/environ exposes another process's environment (secrets). TS
# checkSemantics refuses any argv/redirect target matching this REGARDLESS of
# permission rules (utils/bash/ast.ts:2197,2658-2677). ``.*`` (not ``[^/]*``)
# because Linux resolves ``..`` in procfs (``/proc/self/../self/environ``).
_PROC_ENVIRON_RE = re.compile(r"/proc/.*/environ")


def accesses_proc_environ(command: str) -> bool:
    """True if ``command`` reads ``/proc/*/environ`` (env/secret exfiltration).

    Scans the raw command (covers argv AND redirect targets like
    ``cat < /proc/self/environ``). A backslash before ``environ`` is unescaped
    first — bash reads ``/proc/self/\\environ`` as ``.../environ`` (TS
    ast.ts:1098)."""
    unescaped = command.replace("\\", "")
    return bool(
        _PROC_ENVIRON_RE.search(command) or _PROC_ENVIRON_RE.search(unescaped)
    )


def find_name_eval_subscript_attack(command: str) -> str | None:
    """Head builtin of a sub-command that would arithmetically evaluate a
    ``arr[$(cmd)]`` subscript, or ``None``. Compound-aware; strips env + safe
    wrappers before reading the head so ``FOO=1 timeout 5 printf -v 'a[$(id)]'``
    is still caught."""
    subs: list[str] | None
    if contains_unquoted_chaining(command):
        subs = split_chained_command(command)
        if subs is None:
            return None
    else:
        subs = [command]
    for sub in subs:
        stripped = _strip_env_and_wrappers(sub.strip())
        head = stripped.split(None, 1)[0] if stripped.split() else ""
        if head in _NAME_EVAL_BUILTINS and _SUBSCRIPT_SUBST_RE.search(sub):
            return head
    return None


def find_eval_like_builtin(command: str) -> str | None:
    """First eval-like builtin invoked by ``command`` (or a sub-command), or
    ``None``. Port of the checkSemantics EVAL_LIKE_BUILTINS refusal
    (TS utils/bash/ast.ts:2086 → bashPermissions.ts:1780-1803): these
    builtins execute or re-parse their arguments as code, so no analyzer
    (and no prefix rule) can reason about what actually runs.

    Detection strips env assignments and safe wrappers first (``nohup FOO=1
    eval x`` → ``eval``); the token must equal the builtin exactly — a PATH
    binary invoked as ``./eval`` is not the builtin. Splitter refusal is
    already handled upstream (too-complex ask), so this only sees splittable
    commands.
    """
    subs: list[str] | None
    if contains_unquoted_chaining(command):
        subs = split_chained_command(command)
        if subs is None:
            return None  # unsplittable → the structural too-complex path owns it
    else:
        subs = [command]

    from .bash_suggestions import EVAL_LIKE_BUILTINS, ZSH_DANGEROUS_BUILTINS

    refuse = EVAL_LIKE_BUILTINS | ZSH_DANGEROUS_BUILTINS
    for sub in subs:
        stripped = _strip_env_and_wrappers(sub.strip())
        head = stripped.split(None, 1)[0] if stripped.split() else ""
        if head in refuse:
            return head
    return None


def is_normalized_git_command(command: str) -> bool:
    command = command.strip()
    if command == "git" or command.startswith("git "):
        return True
    stripped = _strip_env_and_wrappers(command)
    tokens = _tokenize_simple(stripped)
    if tokens:
        if tokens[0] == "git":
            return True
        # `xargs git …` runs git in the cwd — same cd+git surface.
        if tokens[0] == "xargs" and "git" in tokens:
            return True
        return False
    return re.match(r"^git(?:\s|$)", stripped) is not None


def is_normalized_cd_command(command: str) -> bool:
    stripped = _strip_env_and_wrappers(command.strip())
    tokens = _tokenize_simple(stripped)
    if tokens:
        return tokens[0] in ("cd", "pushd", "popd")
    return re.match(r"^(?:cd|pushd|popd)(?:\s|$)", stripped) is not None


# ---------------------------------------------------------------------------
# Bare-repo cwd detection — port of isCurrentDirectoryBareGitRepo
# (typescript/src/utils/git.ts:876-925)
# ---------------------------------------------------------------------------

def is_current_directory_bare_git_repo(cwd: str) -> bool:
    """True when ``cwd`` looks like a bare git repo with no valid ``.git``.

    Running git there would treat the cwd as the git directory and execute
    attacker-planted hooks (core.fsmonitor etc.), so read-only git commands
    must not auto-allow.
    """
    git_path = os.path.join(cwd, ".git")
    try:
        if os.path.isfile(git_path):
            return False  # worktree/submodule gitdir reference
        if os.path.isdir(git_path):
            head = os.path.join(git_path, "HEAD")
            if os.path.isfile(head):
                return False  # normal repo
            # .git exists but no regular HEAD — fall through.
    except OSError:
        pass

    for indicator, kind in (("HEAD", "file"), ("objects", "dir"), ("refs", "dir"), ("hooks", "dir")):
        target = os.path.join(cwd, indicator)
        try:
            if kind == "file" and os.path.isfile(target):
                return True
            if kind == "dir" and os.path.isdir(target):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Path containment (port-specific substitute for TS checkPathConstraints on
# the read-only branch — see module docstring)
# ---------------------------------------------------------------------------

_PATHISH = re.compile(r"^(/|\.|~)|/")


def _extract_path_candidates(tokens: list[str]) -> list[str]:
    """Path-looking arguments of a simple command (flags skipped, ``--``
    honored). Overshooting is harmless: a non-path token that resolves inside
    the roots changes nothing; one that resolves outside merely re-prompts."""
    out: list[str] = []
    after_double_dash = False
    for token in tokens[1:]:
        if not token:
            continue
        if not after_double_dash and token == "--":
            after_double_dash = True
            continue
        if not after_double_dash and token.startswith("-"):
            _, eq, value = token.partition("=")
            if eq and value and _PATHISH.search(value):
                out.append(value)
            continue
        if _PATHISH.search(token) or token == "..":
            out.append(token)
    return out


def _paths_within_roots(
    subs: list[str], cwd: str, allowed_roots: Sequence[str]
) -> bool:
    resolved_roots = []
    for root in allowed_roots:
        try:
            resolved_roots.append(os.path.realpath(str(root)))
        except OSError:
            continue
    if not resolved_roots:
        return False

    def _inside(p: str) -> bool:
        try:
            rp = os.path.realpath(
                os.path.join(cwd, os.path.expanduser(p))
            )
        except OSError:
            return False
        for root in resolved_roots:
            if rp == root or rp.startswith(root + os.sep):
                return True
        return False

    for sub in subs:
        tokens = _tokenize_simple(sub.strip())
        if not tokens:
            return False
        for cand in _extract_path_candidates(tokens):
            if not _inside(cand):
                return False
    return True


# ---------------------------------------------------------------------------
# checkReadOnlyConstraints — port of readOnlyValidation.ts:1810-1924
# ---------------------------------------------------------------------------

def compound_structure_guards_ok(subs: Sequence[str], cwd: str) -> bool:
    """Compound-level guards that must hold before ANY sub-command may be
    treated as read-only: at most one directory change (TS asks on multiple
    cds, bashPermissions.ts:2197), never cd+git together (bare-repository
    attack: cd into a planted repo, git executes its hooks), and no git at
    all when the cwd itself looks like a planted bare repo."""
    cd_count = sum(1 for s in subs if is_normalized_cd_command(s))
    if cd_count > 1:
        return False
    has_git = any(is_normalized_git_command(s) for s in subs)
    if cd_count and has_git:
        return False
    if has_git and is_current_directory_bare_git_repo(cwd):
        return False
    return True


def sub_command_read_only_and_contained(
    sub: str, *, cwd: str, allowed_roots: Sequence[str]
) -> bool:
    """One already-split sub-command: provably read-only AND path-contained.

    Callers holding a compound MUST have checked
    :func:`compound_structure_guards_ok` over the WHOLE sub list first —
    per-sub checks alone cannot see multi-cd / cd+git structure.
    """
    return is_command_read_only(sub) and _paths_within_roots(
        [sub], cwd, allowed_roots
    )


def check_read_only_constraints(
    command: str,
    *,
    cwd: str,
    allowed_roots: Sequence[str],
) -> bool:
    """True when ``command`` (possibly compound) is provably read-only AND
    contained in the allowed roots. False = not provable → normal prompt flow.
    """
    stripped = command.strip()
    if not stripped:
        return False

    # Substitution executes hidden commands the validators never see.
    if contains_executable_substitution(stripped):
        return False

    if contains_unquoted_chaining(stripped):
        subs = split_chained_command(stripped)
        if not subs:
            return False  # splitter refusal (subshell/heredoc/…) → prompt
    else:
        subs = [stripped]

    if not compound_structure_guards_ok(subs, cwd):
        return False

    for sub in subs:
        if not is_command_read_only(sub):
            return False

    if not _paths_within_roots(subs, cwd, allowed_roots):
        return False

    return True
