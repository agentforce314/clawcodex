"""Faithful Python transcription of the TypeScript read-only command
validation data tables.

Source (read-only reference, byte-for-byte fidelity is the acceptance bar):
  - typescript/src/tools/BashTool/readOnlyValidation.ts
  - typescript/src/utils/shell/readOnlyCommandValidation.ts
  - typescript/src/tools/BashTool/sedValidation.ts

This module holds ONLY the declarative tables + the sed allowlist port.
The flag-walking engine (validateFlags), the operator/`$`/brace-expansion
rejection, the `git ls-remote` URL guard, and the compound-command orchestration
(checkReadOnlyConstraints) live in the harness and are intentionally NOT here.

Stdlib only (re, dataclasses, typing). The single approximation is
`_try_parse_shell_command`, a self-contained quote-aware tokenizer standing in
for the JS `shell-quote` parse() that sedValidation relies on — see its docstring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

# ---------------------------------------------------------------------------
# Types  (TS readOnlyCommandValidation.ts:18-24, readOnlyValidation.ts:34-49)
# ---------------------------------------------------------------------------

# FlagArgType — the complete set of literal arg-type tags used across ALL
# source tables. No other literal values appear in the three source files.
FlagArgType = Literal[
    "none",  # No argument (--color, -n)
    "number",  # Integer argument (--context=3)
    "string",  # Any string argument (--relative=path)
    "char",  # Single character (delimiter)
    "{}",  # Literal "{}" only
    "EOF",  # Literal "EOF" only
]


@dataclass
class CommandConfig:
    """Unified command config. TS unifies two shapes: CommandConfig
    (readOnlyValidation.ts:34) which additionally carries `regex`, and
    ExternalCommandConfig (readOnlyCommandValidation.ts:26) which does not.
    Merged here; git/rg/pyright/docker entries simply leave `regex` None.
    """

    # Record mapping the command (e.g. `xargs` or `git diff`) to its safe
    # flags and the values they accept.
    safe_flags: dict[str, str]
    # Optional regex for additional validation beyond flag parsing.
    regex: re.Pattern[str] | None = None
    # Optional callback. Returns True if the command is DANGEROUS, False if it
    # appears safe. Used in conjunction with the safe_flags-based validation.
    additional_command_is_dangerous: Callable[[str, list[str]], bool] | None = None
    # When False, the tool does NOT respect POSIX `--` end-of-options.
    # Default: True (most tools respect `--`).
    respects_double_dash: bool = True


# ===========================================================================
# sed validation port  (TS sedValidation.ts, complete)
# ===========================================================================


class _SedParseError(Exception):
    """Raised when sed argument parsing fails (mirrors the thrown Errors in
    extractSedExpressions)."""


@dataclass
class _GlobToken:
    """Stand-in for shell-quote's `{op:'glob', pattern}` entry. Only the
    glob case of a non-string token is observable to the sed logic."""

    op: str  # always 'glob'
    pattern: str


def _try_parse_shell_command(command: str) -> tuple[bool, list[object]]:
    """Self-contained, stdlib-only approximation of the JS `shell-quote`
    parse() that `tryParseShellCommand(withoutSed)` uses in sedValidation.ts.

    Returns (success, tokens) where each token is either a `str` or a
    `_GlobToken`. It faithfully reproduces the behaviors the sed logic
    depends on:
      - quote-aware whitespace splitting (single + double quotes),
      - single quotes are fully literal; double quotes honor `\\` before
        one of " \\ $ ` (bash semantics),
      - an UNQUOTED token containing a glob metacharacter (* ? [ ]) becomes a
        _GlobToken (shell-quote's glob entry) — this is what makes a bare
        `*.log` count as a file argument in has_file_args,
      - unbalanced quotes -> success=False (shell-quote / the JS wrapper
        surface this as a parse failure).

    It deliberately does NOT model unquoted control operators (| ; & < > ( )):
    the only caller of `sed_command_is_allowed_by_allowlist` is the sed
    COMMAND_ALLOWLIST callback, which the harness reaches only AFTER rejecting
    any operator-containing command (isCommandSafeViaFlagParsing's hasOperators
    guard). Where this simplification diverges it fails closed (more
    restrictive), never open.
    """
    tokens: list[object] = []
    current: list[str] = []
    has_token = False
    is_glob = False
    in_single = False
    in_double = False
    i = 0
    n = len(command)

    def _flush() -> None:
        nonlocal current, has_token, is_glob
        text = "".join(current)
        tokens.append(_GlobToken("glob", text) if is_glob else text)
        current = []
        has_token = False
        is_glob = False

    while i < n:
        c = command[i]
        if in_single:
            if c == "'":
                in_single = False
            else:
                current.append(c)
            i += 1
            continue
        if in_double:
            if c == '"':
                in_double = False
            elif c == "\\" and i + 1 < n and command[i + 1] in ('"', "\\", "$", "`"):
                current.append(command[i + 1])
                i += 2
                continue
            else:
                current.append(c)
            i += 1
            continue
        # Outside all quotes.
        if c == "'":
            in_single = True
            has_token = True
            i += 1
            continue
        if c == '"':
            in_double = True
            has_token = True
            i += 1
            continue
        if c == "\\":
            if i + 1 < n:
                current.append(command[i + 1])
                has_token = True
                i += 2
                continue
            i += 1
            continue
        if c.isspace():
            if has_token:
                _flush()
            i += 1
            continue
        if c in "*?[]":
            is_glob = True
        current.append(c)
        has_token = True
        i += 1

    if in_single or in_double:
        return (False, [])
    if has_token:
        _flush()
    return (True, tokens)


def _validate_flags_against_allowlist(
    flags: list[str], allowed_flags: list[str]
) -> bool:
    """TS sedValidation.ts:13 validateFlagsAgainstAllowlist. Handles combined
    short flags (e.g. -nE) by checking each character."""
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 2:
            for i in range(1, len(flag)):
                single_flag = "-" + flag[i]
                if single_flag not in allowed_flags:
                    return False
        else:
            if flag not in allowed_flags:
                return False
    return True


_PRINT_COMMAND_RE = re.compile(r"^(?:\d+|\d+,\d+)?p$")


def is_print_command(cmd: str) -> bool:
    """TS sedValidation.ts:128. STRICT: matches p, 1p, 123p, 1,5p, 10,200p."""
    if not cmd:
        return False
    return bool(_PRINT_COMMAND_RE.search(cmd))


_SED_PREFIX_RE = re.compile(r"^\s*sed\s+")


def is_line_printing_command(command: str, expressions: list[str]) -> bool:
    """TS sedValidation.ts:44. Pattern 1: `sed -n 'Np'` style line printing.
    File arguments are ALLOWED for this pattern."""
    m = _SED_PREFIX_RE.match(command)
    if not m:
        return False

    without_sed = command[m.end() :]
    ok, parsed = _try_parse_shell_command(without_sed)
    if not ok:
        return False

    flags = [
        a
        for a in parsed
        if isinstance(a, str) and a.startswith("-") and a != "--"
    ]

    allowed_flags = [
        "-n",
        "--quiet",
        "--silent",
        "-E",
        "--regexp-extended",
        "-r",
        "-z",
        "--zero-terminated",
        "--posix",
    ]

    if not _validate_flags_against_allowlist(flags, allowed_flags):
        return False

    # -n flag is required for Pattern 1.
    has_n_flag = False
    for flag in flags:
        if flag in ("-n", "--quiet", "--silent"):
            has_n_flag = True
            break
        if flag.startswith("-") and not flag.startswith("--") and "n" in flag:
            has_n_flag = True
            break

    if not has_n_flag:
        return False

    if len(expressions) == 0:
        return False

    # All expressions must be print commands (strict allowlist); allow
    # semicolon-separated print commands.
    for expr in expressions:
        commands = expr.split(";")
        for cmd in commands:
            if not is_print_command(cmd.strip()):
                return False

    return True


_SUBST_EXPR_RE = re.compile(r"^s/(.*?)$")
_SUBST_FLAGS_RE = re.compile(r"^[gpimIM]*[1-9]?[gpimIM]*$")


def is_substitution_command(
    command: str,
    expressions: list[str],
    has_file_arguments: bool,
    allow_file_writes: bool = False,
) -> bool:
    """TS sedValidation.ts:142. Pattern 2: `sed 's/pat/repl/flags'`. In
    read-only mode (allow_file_writes False) requires stdout-only (no file
    args, no -i)."""
    if not allow_file_writes and has_file_arguments:
        return False

    m = _SED_PREFIX_RE.match(command)
    if not m:
        return False

    without_sed = command[m.end() :]
    ok, parsed = _try_parse_shell_command(without_sed)
    if not ok:
        return False

    flags = [
        a
        for a in parsed
        if isinstance(a, str) and a.startswith("-") and a != "--"
    ]

    allowed_flags = ["-E", "--regexp-extended", "-r", "--posix"]
    if allow_file_writes:
        allowed_flags.extend(["-i", "--in-place"])

    if not _validate_flags_against_allowlist(flags, allowed_flags):
        return False

    if len(expressions) != 1:
        return False

    expr = expressions[0].strip()

    # Must be a substitution command starting with 's'.
    if not expr.startswith("s"):
        return False

    # Only allow / as delimiter (strict): s/pattern/replacement/flags
    substitution_match = _SUBST_EXPR_RE.match(expr)
    if not substitution_match:
        return False

    rest = substitution_match.group(1)

    # Find the positions of / delimiters (skipping escaped chars).
    delimiter_count = 0
    last_delimiter_pos = -1
    i = 0
    while i < len(rest):
        if rest[i] == "\\":
            i += 2
            continue
        if rest[i] == "/":
            delimiter_count += 1
            last_delimiter_pos = i
        i += 1

    # Exactly 2 delimiters (pattern and replacement).
    if delimiter_count != 2:
        return False

    expr_flags = rest[last_delimiter_pos + 1 :]

    # Only allow g, p, i, I, m, M, and optionally ONE digit 1-9.
    if not _SUBST_FLAGS_RE.search(expr_flags):
        return False

    return True


def has_file_args(command: str) -> bool:
    """TS sedValidation.ts:307. True if the sed command has file arguments
    (not just stdin). Fails closed (True) on parse failure."""
    m = _SED_PREFIX_RE.match(command)
    if not m:
        return False

    without_sed = command[m.end() :]
    ok, parsed = _try_parse_shell_command(without_sed)
    if not ok:
        return True

    arg_count = 0
    has_e_flag = False
    i = 0
    while i < len(parsed):
        arg = parsed[i]

        # A glob pattern counts as a file argument.
        if isinstance(arg, _GlobToken):
            return True
        # Skip non-string tokens that aren't globs (operators — none reach here).
        if not isinstance(arg, str):
            i += 1
            continue

        # -e / --expression consumes the following expression token.
        if (arg == "-e" or arg == "--expression") and i + 1 < len(parsed):
            has_e_flag = True
            i += 2
            continue

        if arg.startswith("--expression="):
            has_e_flag = True
            i += 1
            continue

        if arg.startswith("-e="):
            has_e_flag = True
            i += 1
            continue

        if arg.startswith("-"):
            i += 1
            continue

        arg_count += 1

        # If -e flags were used, ALL non-flag args are file arguments.
        if has_e_flag:
            return True

        # Without -e, the first non-flag arg is the sed expression; a second
        # non-flag arg means file arguments are present.
        if arg_count > 1:
            return True

        i += 1

    return False


def extract_sed_expressions(command: str) -> list[str]:
    """TS sedValidation.ts:388. Extract sed expressions (ignoring flags and
    filenames). Raises _SedParseError on dangerous flag combos / malformed
    syntax (mirrors the thrown Errors)."""
    expressions: list[str] = []

    m = _SED_PREFIX_RE.match(command)
    if not m:
        return expressions

    without_sed = command[m.end() :]

    # Reject dangerous combined -e/-w flag forms (e.g. -ew, -eW, -ee, -we).
    if re.search(r"-e[wWe]", without_sed) or re.search(r"-w[eE]", without_sed):
        raise _SedParseError("Dangerous flag combination detected")

    ok, parsed = _try_parse_shell_command(without_sed)
    if not ok:
        raise _SedParseError("Malformed shell syntax")

    found_e_flag = False
    found_expression = False

    i = 0
    while i < len(parsed):
        arg = parsed[i]

        # Skip non-string arguments (control operators / globs).
        if not isinstance(arg, str):
            i += 1
            continue

        # -e / --expression followed by expression.
        if (arg == "-e" or arg == "--expression") and i + 1 < len(parsed):
            found_e_flag = True
            next_arg = parsed[i + 1]
            if isinstance(next_arg, str):
                expressions.append(next_arg)
                i += 2  # consume flag + expression
            else:
                i += 1
            continue

        if arg.startswith("--expression="):
            found_e_flag = True
            expressions.append(arg[len("--expression=") :])
            i += 1
            continue

        if arg.startswith("-e="):
            found_e_flag = True
            expressions.append(arg[len("-e=") :])
            i += 1
            continue

        if arg.startswith("-"):
            i += 1
            continue

        # First non-flag arg is the sed expression when no -e was used.
        if not found_e_flag and not found_expression:
            expressions.append(arg)
            found_expression = True
            i += 1
            continue

        # Remaining non-flag args are filenames.
        break

    return expressions


def contains_dangerous_operations(expression: str) -> bool:
    """TS sedValidation.ts:473 (denylist). True if the sed expression contains
    dangerous operations (w/W write, e/E execute, and obfuscations thereof)."""
    cmd = expression.strip()
    if not cmd:
        return False

    # Reject non-ASCII (homoglyphs, combining chars). ASCII 0x01-0x7F only.
    if re.search(r"[^\x01-\x7F]", cmd):
        return True

    # Reject curly braces (blocks) — too complex to parse.
    if "{" in cmd or "}" in cmd:
        return True

    # Reject newlines.
    if "\n" in cmd:
        return True

    # Reject comments (# not immediately after an s command delimiter).
    hash_index = cmd.find("#")
    if hash_index != -1 and not (hash_index > 0 and cmd[hash_index - 1] == "s"):
        return True

    # Reject negation operator.
    if re.search(r"^!", cmd) or re.search(r"[/\d$]!", cmd):
        return True

    # Reject GNU step address (digit~digit, ,~digit, $~digit).
    if re.search(r"\d\s*~\s*\d|,\s*~\s*\d|\$\s*~\s*\d", cmd):
        return True

    # Reject bare leading comma (shorthand for 1,$ range).
    if re.search(r"^,", cmd):
        return True

    # Reject comma followed by +/- (GNU offset addresses).
    if re.search(r",\s*[+-]", cmd):
        return True

    # Reject backslash tricks: s\ (backslash delim) or \X alt-delimiters.
    if re.search(r"s\\", cmd) or re.search(r"\\[|#%@]", cmd):
        return True

    # Reject escaped slashes followed by w/W.
    if re.search(r"\\\/.*[wW]", cmd):
        return True

    # Reject slash-then-nonslash, whitespace, then dangerous command.
    if re.search(r"\/[^/]*\s+[wWeE]", cmd):
        return True

    # Reject malformed substitution commands.
    if re.search(r"^s\/", cmd) and not re.search(
        r"^s\/[^/]*\/[^/]*\/[^/]*$", cmd
    ):
        return True

    # PARANOID: 's...' ending in w/W/e/E that isn't a proper substitution.
    if re.search(r"^s.", cmd) and re.search(r"[wWeE]$", cmd):
        proper_subst = re.search(r"^s([^\\\n]).*?\1.*?\1[^wWeE]*$", cmd)
        if not proper_subst:
            return True

    # Dangerous write commands: [addr]w file, /pattern/w file, ranges, etc.
    if (
        re.search(r"^[wW]\s*\S+", cmd)
        or re.search(r"^\d+\s*[wW]\s*\S+", cmd)
        or re.search(r"^\$\s*[wW]\s*\S+", cmd)
        or re.search(r"^\/[^/]*\/[IMim]*\s*[wW]\s*\S+", cmd)
        or re.search(r"^\d+,\d+\s*[wW]\s*\S+", cmd)
        or re.search(r"^\d+,\$\s*[wW]\s*\S+", cmd)
        or re.search(r"^\/[^/]*\/[IMim]*,\/[^/]*\/[IMim]*\s*[wW]\s*\S+", cmd)
    ):
        return True

    # Dangerous execute commands: [addr]e cmd, /pattern/e, ranges, etc.
    if (
        re.search(r"^e", cmd)
        or re.search(r"^\d+\s*e", cmd)
        or re.search(r"^\$\s*e", cmd)
        or re.search(r"^\/[^/]*\/[IMim]*\s*e", cmd)
        or re.search(r"^\d+,\d+\s*e", cmd)
        or re.search(r"^\d+,\$\s*e", cmd)
        or re.search(r"^\/[^/]*\/[IMim]*,\/[^/]*\/[IMim]*\s*e", cmd)
    ):
        return True

    # Substitution with dangerous flags: s<d>pat<d>repl<d>flags where flags
    # contain w or e. POSIX allows any char except backslash/newline as delim.
    substitution_match = re.search(r"s([^\\\n]).*?\1.*?\1(.*?)$", cmd)
    if substitution_match:
        flags = substitution_match.group(2) or ""
        if "w" in flags or "W" in flags:
            return True
        if "e" in flags or "E" in flags:
            return True

    # y (transliterate) command followed by any w/W/e/E (paranoid).
    y_command_match = re.search(r"y([^\\\n])", cmd)
    if y_command_match:
        if re.search(r"[wWeE]", cmd):
            return True

    return False


def sed_command_is_allowed_by_allowlist(
    raw_command: str, allow_file_writes: bool = False
) -> bool:
    """TS sedValidation.ts:247 sedCommandIsAllowedByAllowlist. The COMMAND_
    ALLOWLIST callback invokes this with allow_file_writes defaulting False
    (read-only). Returns True if the sed command matches the allowlist patterns
    AND passes the denylist check."""
    # Extract sed expressions (content inside quotes where sed commands live).
    try:
        expressions = extract_sed_expressions(raw_command)
    except Exception:
        # If parsing failed, treat as not allowed.
        return False

    has_file_arguments = has_file_args(raw_command)

    is_pattern1 = False
    is_pattern2 = False

    if allow_file_writes:
        # Only substitution commands need file writes (Pattern 2 variant).
        is_pattern2 = is_substitution_command(
            raw_command, expressions, has_file_arguments, allow_file_writes=True
        )
    else:
        is_pattern1 = is_line_printing_command(raw_command, expressions)
        is_pattern2 = is_substitution_command(
            raw_command, expressions, has_file_arguments
        )

    if not is_pattern1 and not is_pattern2:
        return False

    # Pattern 2 does not allow semicolons; Pattern 1 does (for print separators).
    for expr in expressions:
        if is_pattern2 and ";" in expr:
            return False

    # Defense-in-depth: even if the allowlist matches, check the denylist.
    for expr in expressions:
        if contains_dangerous_operations(expr):
            return False

    return True


# ===========================================================================
# COMMAND_ALLOWLIST callbacks  (additionalCommandIsDangerousCallback ports)
# ===========================================================================

_PS_BSD_E_RE = re.compile(r"^[a-zA-Z]*e[a-zA-Z]*$")


def _ps_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyValidation.ts:419. Block BSD-style 'e' modifier (shows env
    vars). A BSD-style option is a letter-only token (no leading dash) with 'e'."""
    return any(
        (not a.startswith("-")) and bool(_PS_BSD_E_RE.search(a)) for a in args
    )


def _date_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyValidation.ts:755. Positional args in MMDDhhmm[[CC]YY][.ss]
    set system time; require positional args to start with '+' (format strings)."""
    flags_with_args = {
        "-d",
        "--date",
        "-r",
        "--reference",
        "--iso-8601",
        "--rfc-3339",
    }
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--") and "=" in token:
            i += 1
        elif token.startswith("-"):
            if token in flags_with_args:
                i += 2
            else:
                i += 1
        else:
            if not token.startswith("+"):
                return True
            i += 1
    return False


def _lsof_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyValidation.ts:907. Block +m (create mount supplement file)."""
    return any(a == "+m" or a.startswith("+m") for a in args)


_TPUT_DANGEROUS_CAPABILITIES = {
    "init",
    "reset",
    "rs1",
    "rs2",
    "rs3",
    "is1",
    "is2",
    "is3",
    "iprog",
    "if",
    "rf",
    "clear",
    "flash",
    "mc0",
    "mc4",
    "mc5",
    "mc5i",
    "mc5p",
    "pfkey",
    "pfloc",
    "pfx",
    "pfxl",
    "smcup",
    "rmcup",
}


def _tput_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyValidation.ts:977. Block terminal-state-modifying capabilities
    and -S (read capability names from stdin, incl. bundled -xS)."""
    flags_with_args = {"-T"}
    i = 0
    after_double_dash = False
    while i < len(args):
        token = args[i]
        if token == "--":
            after_double_dash = True
            i += 1
        elif not after_double_dash and token.startswith("-"):
            # Defense-in-depth: block -S even if it passes validateFlags.
            if token == "-S":
                return True
            # -S bundled with other flags (e.g., -xS).
            if not token.startswith("--") and len(token) > 2 and "S" in token:
                return True
            if token in flags_with_args:
                i += 2
            else:
                i += 1
        else:
            if token in _TPUT_DANGEROUS_CAPABILITIES:
                return True
            i += 1
    return False


# --- git callbacks (TS readOnlyCommandValidation.ts) ---


def _git_reflog_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:283. Block write-capable subcommands
    (expire/delete/exists); bare/show/ref-name are safe."""
    dangerous_subcommands = {"expire", "delete", "exists"}
    for token in args:
        if not token or token.startswith("-"):
            continue
        if token in dangerous_subcommands:
            return True
        # First positional is safe (show/HEAD/ref) — subsequent are ref args.
        return False
    return False


_GIT_REMOTE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _git_remote_show_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:478. Allow optional -n then exactly one
    alphanumeric remote name."""
    positional = [a for a in args if a != "-n"]
    if len(positional) != 1:
        return True
    return not bool(_GIT_REMOTE_NAME_RE.search(positional[0]))


def _git_remote_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:495. Only bare 'git remote' or
    'git remote -v/--verbose'."""
    return any(a != "-v" and a != "--verbose" for a in args)


def _git_tag_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:739. Block tag creation via positional
    args; only listing/filtering forms are read-only."""
    flags_with_args = {
        "--contains",
        "--no-contains",
        "--merged",
        "--no-merged",
        "--points-at",
        "--sort",
        "--format",
        "-n",
    }
    i = 0
    seen_list_flag = False
    seen_dash_dash = False
    while i < len(args):
        token = args[i]
        if not token:
            i += 1
            continue
        # `--` ends flag parsing; subsequent tokens are positional even if `-`.
        if token == "--" and not seen_dash_dash:
            seen_dash_dash = True
            i += 1
            continue
        if not seen_dash_dash and token.startswith("-"):
            if token == "--list" or token == "-l":
                seen_list_flag = True
            elif (
                len(token) > 2
                and token[0] == "-"
                and token[1] != "-"
                and "=" not in token
                and "l" in token[1:]
            ):
                # Short-flag bundle like -li, -il containing 'l'.
                seen_list_flag = True
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                i += 2
            else:
                i += 1
        else:
            # Positional arg without --list = tag creation.
            if not seen_list_flag:
                return True
            i += 1
    return False


def _git_branch_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:851. Block branch creation via positional
    args; only listing/filtering forms are read-only."""
    flags_with_args = {"--contains", "--no-contains", "--points-at", "--sort"}
    flags_with_optional_args = {"--merged", "--no-merged"}
    i = 0
    last_flag = ""
    seen_list_flag = False
    seen_dash_dash = False
    while i < len(args):
        token = args[i]
        if not token:
            i += 1
            continue
        if token == "--" and not seen_dash_dash:
            seen_dash_dash = True
            last_flag = ""
            i += 1
            continue
        if not seen_dash_dash and token.startswith("-"):
            if token == "--list" or token == "-l":
                seen_list_flag = True
            elif (
                len(token) > 2
                and token[0] == "-"
                and token[1] != "-"
                and "=" not in token
                and "l" in token[1:]
            ):
                seen_list_flag = True
            if "=" in token:
                last_flag = token.split("=")[0] or ""
                i += 1
            elif token in flags_with_args:
                last_flag = token
                i += 2
            else:
                last_flag = token
                i += 1
        else:
            last_flag_has_optional_arg = last_flag in flags_with_optional_args
            if not seen_list_flag and not last_flag_has_optional_arg:
                return True
            i += 1
    return False


def _pyright_is_dangerous(_raw_command: str, args: list[str]) -> bool:
    """TS readOnlyCommandValidation.ts:1523. Block --watch / -w."""
    return any(t == "--watch" or t == "-w" for t in args)


def _sed_is_dangerous(raw_command: str, _args: list[str]) -> bool:
    """TS readOnlyValidation.ts:241. Delegates to the sed allowlist port."""
    return not sed_command_is_allowed_by_allowlist(raw_command)


# ===========================================================================
# Shared git flag groups  (TS readOnlyCommandValidation.ts:44-101)
# ===========================================================================

GIT_REF_SELECTION_FLAGS: dict[str, str] = {
    "--all": "none",
    "--branches": "none",
    "--tags": "none",
    "--remotes": "none",
}

GIT_DATE_FILTER_FLAGS: dict[str, str] = {
    "--since": "string",
    "--after": "string",
    "--until": "string",
    "--before": "string",
}

GIT_LOG_DISPLAY_FLAGS: dict[str, str] = {
    "--oneline": "none",
    "--graph": "none",
    "--decorate": "none",
    "--no-decorate": "none",
    "--date": "string",
    "--relative-date": "none",
}

GIT_COUNT_FLAGS: dict[str, str] = {
    "--max-count": "number",
    "-n": "number",
}

# Stat output flags — used in git log, show, diff.
GIT_STAT_FLAGS: dict[str, str] = {
    "--stat": "none",
    "--numstat": "none",
    "--shortstat": "none",
    "--name-only": "none",
    "--name-status": "none",
}

# Color output flags — used in git log, show, diff.
GIT_COLOR_FLAGS: dict[str, str] = {
    "--color": "none",
    "--no-color": "none",
}

# Patch display flags — used in git log, show.
GIT_PATCH_FLAGS: dict[str, str] = {
    "--patch": "none",
    "-p": "none",
    "--no-patch": "none",
    "--no-ext-diff": "none",
    "-s": "none",
}

# Author/committer filter flags — used in git log, reflog.
GIT_AUTHOR_FILTER_FLAGS: dict[str, str] = {
    "--author": "string",
    "--committer": "string",
    "--grep": "string",
}


# ===========================================================================
# GIT_READ_ONLY_COMMANDS  (TS readOnlyCommandValidation.ts:107-923)
# NOTE: 'git remote show' MUST precede 'git remote' so longer patterns match
# first — insertion order is preserved.
# ===========================================================================

GIT_READ_ONLY_COMMANDS: dict[str, CommandConfig] = {
    "git diff": CommandConfig(
        safe_flags={
            **GIT_STAT_FLAGS,
            **GIT_COLOR_FLAGS,
            "--dirstat": "none",
            "--summary": "none",
            "--patch-with-stat": "none",
            "--word-diff": "none",
            "--word-diff-regex": "string",
            "--color-words": "none",
            "--no-renames": "none",
            "--no-ext-diff": "none",
            "--check": "none",
            "--ws-error-highlight": "string",
            "--full-index": "none",
            "--binary": "none",
            "--abbrev": "number",
            "--break-rewrites": "none",
            "--find-renames": "none",
            "--find-copies": "none",
            "--find-copies-harder": "none",
            "--irreversible-delete": "none",
            "--diff-algorithm": "string",
            "--histogram": "none",
            "--patience": "none",
            "--minimal": "none",
            "--ignore-space-at-eol": "none",
            "--ignore-space-change": "none",
            "--ignore-all-space": "none",
            "--ignore-blank-lines": "none",
            "--inter-hunk-context": "number",
            "--function-context": "none",
            "--exit-code": "none",
            "--quiet": "none",
            "--cached": "none",
            "--staged": "none",
            "--pickaxe-regex": "none",
            "--pickaxe-all": "none",
            "--no-index": "none",
            "--relative": "string",
            "--diff-filter": "string",
            "-p": "none",
            "-u": "none",
            "-s": "none",
            "-M": "none",
            "-C": "none",
            "-B": "none",
            "-D": "none",
            "-l": "none",
            # SECURITY: -S/-G/-O take REQUIRED string args (pickaxe search,
            # pickaxe regex, orderfile). 'none' caused a parser differential
            # allowing `git diff -S -- --output=/tmp/pwned` file write.
            "-S": "string",
            "-G": "string",
            "-O": "string",
            "-R": "none",
        },
    ),
    "git log": CommandConfig(
        safe_flags={
            **GIT_LOG_DISPLAY_FLAGS,
            **GIT_REF_SELECTION_FLAGS,
            **GIT_DATE_FILTER_FLAGS,
            **GIT_COUNT_FLAGS,
            **GIT_STAT_FLAGS,
            **GIT_COLOR_FLAGS,
            **GIT_PATCH_FLAGS,
            **GIT_AUTHOR_FILTER_FLAGS,
            "--abbrev-commit": "none",
            "--full-history": "none",
            "--dense": "none",
            "--sparse": "none",
            "--simplify-merges": "none",
            "--ancestry-path": "none",
            "--source": "none",
            "--first-parent": "none",
            "--merges": "none",
            "--no-merges": "none",
            "--reverse": "none",
            "--walk-reflogs": "none",
            "--skip": "number",
            "--max-age": "number",
            "--min-age": "number",
            "--no-min-parents": "none",
            "--no-max-parents": "none",
            "--follow": "none",
            "--no-walk": "none",
            "--left-right": "none",
            "--cherry-mark": "none",
            "--cherry-pick": "none",
            "--boundary": "none",
            "--topo-order": "none",
            "--date-order": "none",
            "--author-date-order": "none",
            "--pretty": "string",
            "--format": "string",
            "--diff-filter": "string",
            "-S": "string",
            "-G": "string",
            "--pickaxe-regex": "none",
            "--pickaxe-all": "none",
        },
    ),
    "git show": CommandConfig(
        safe_flags={
            **GIT_LOG_DISPLAY_FLAGS,
            **GIT_STAT_FLAGS,
            **GIT_COLOR_FLAGS,
            **GIT_PATCH_FLAGS,
            "--abbrev-commit": "none",
            "--word-diff": "none",
            "--word-diff-regex": "string",
            "--color-words": "none",
            "--pretty": "string",
            "--format": "string",
            "--first-parent": "none",
            "--raw": "none",
            "--diff-filter": "string",
            "-m": "none",
            "--quiet": "none",
        },
    ),
    "git shortlog": CommandConfig(
        safe_flags={
            **GIT_REF_SELECTION_FLAGS,
            **GIT_DATE_FILTER_FLAGS,
            "-s": "none",
            "--summary": "none",
            "-n": "none",
            "--numbered": "none",
            "-e": "none",
            "--email": "none",
            "-c": "none",
            "--committer": "none",
            "--group": "string",
            "--format": "string",
            "--no-merges": "none",
            "--author": "string",
        },
    ),
    "git reflog": CommandConfig(
        safe_flags={
            **GIT_LOG_DISPLAY_FLAGS,
            **GIT_REF_SELECTION_FLAGS,
            **GIT_DATE_FILTER_FLAGS,
            **GIT_COUNT_FLAGS,
            **GIT_AUTHOR_FILTER_FLAGS,
        },
        # SECURITY: block `git reflog expire/delete` (write .git/logs/**).
        additional_command_is_dangerous=_git_reflog_is_dangerous,
    ),
    "git stash list": CommandConfig(
        safe_flags={
            **GIT_LOG_DISPLAY_FLAGS,
            **GIT_REF_SELECTION_FLAGS,
            **GIT_COUNT_FLAGS,
        },
    ),
    "git ls-remote": CommandConfig(
        safe_flags={
            "--branches": "none",
            "-b": "none",
            "--tags": "none",
            "-t": "none",
            "--heads": "none",
            "-h": "none",
            "--refs": "none",
            "--quiet": "none",
            "-q": "none",
            "--exit-code": "none",
            "--get-url": "none",
            "--symref": "none",
            "--sort": "string",
            # SECURITY: --server-option / -o EXCLUDED (network write primitive).
        },
    ),
    "git status": CommandConfig(
        safe_flags={
            "--short": "none",
            "-s": "none",
            "--branch": "none",
            "-b": "none",
            "--porcelain": "none",
            "--long": "none",
            "--verbose": "none",
            "-v": "none",
            "--untracked-files": "string",
            "-u": "string",
            "--ignored": "none",
            "--ignore-submodules": "string",
            "--column": "none",
            "--no-column": "none",
            "--ahead-behind": "none",
            "--no-ahead-behind": "none",
            "--renames": "none",
            "--no-renames": "none",
            "--find-renames": "string",
            "-M": "string",
        },
    ),
    "git blame": CommandConfig(
        safe_flags={
            **GIT_COLOR_FLAGS,
            "-L": "string",
            "--porcelain": "none",
            "-p": "none",
            "--line-porcelain": "none",
            "--incremental": "none",
            "--root": "none",
            "--show-stats": "none",
            "--show-name": "none",
            "--show-number": "none",
            "-n": "none",
            "--show-email": "none",
            "-e": "none",
            "-f": "none",
            "--date": "string",
            "-w": "none",
            "--ignore-rev": "string",
            "--ignore-revs-file": "string",
            "-M": "none",
            "-C": "none",
            "--score-debug": "none",
            "--abbrev": "number",
            "-s": "none",
            "-l": "none",
            "-t": "none",
        },
    ),
    "git ls-files": CommandConfig(
        safe_flags={
            "--cached": "none",
            "-c": "none",
            "--deleted": "none",
            "-d": "none",
            "--modified": "none",
            "-m": "none",
            "--others": "none",
            "-o": "none",
            "--ignored": "none",
            "-i": "none",
            "--stage": "none",
            "-s": "none",
            "--killed": "none",
            "-k": "none",
            "--unmerged": "none",
            "-u": "none",
            "--directory": "none",
            "--no-empty-directory": "none",
            "--eol": "none",
            "--full-name": "none",
            "--abbrev": "number",
            "--debug": "none",
            "-z": "none",
            "-t": "none",
            "-v": "none",
            "-f": "none",
            "--exclude": "string",
            "-x": "string",
            "--exclude-from": "string",
            "-X": "string",
            "--exclude-per-directory": "string",
            "--exclude-standard": "none",
            "--error-unmatch": "none",
            "--recurse-submodules": "none",
        },
    ),
    "git config --get": CommandConfig(
        safe_flags={
            "--local": "none",
            "--global": "none",
            "--system": "none",
            "--worktree": "none",
            "--default": "string",
            "--type": "string",
            "--bool": "none",
            "--int": "none",
            "--bool-or-int": "none",
            "--path": "none",
            "--expiry-date": "none",
            "-z": "none",
            "--null": "none",
            "--name-only": "none",
            "--show-origin": "none",
            "--show-scope": "none",
        },
    ),
    # 'git remote show' before 'git remote' (longer pattern first).
    "git remote show": CommandConfig(
        safe_flags={
            "-n": "none",
        },
        additional_command_is_dangerous=_git_remote_show_is_dangerous,
    ),
    "git remote": CommandConfig(
        safe_flags={
            "-v": "none",
            "--verbose": "none",
        },
        additional_command_is_dangerous=_git_remote_is_dangerous,
    ),
    "git merge-base": CommandConfig(
        safe_flags={
            "--is-ancestor": "none",
            "--fork-point": "none",
            "--octopus": "none",
            "--independent": "none",
            "--all": "none",
        },
    ),
    "git rev-parse": CommandConfig(
        safe_flags={
            "--verify": "none",
            "--short": "string",
            "--abbrev-ref": "none",
            "--symbolic": "none",
            "--symbolic-full-name": "none",
            "--show-toplevel": "none",
            "--show-cdup": "none",
            "--show-prefix": "none",
            "--git-dir": "none",
            "--git-common-dir": "none",
            "--absolute-git-dir": "none",
            "--show-superproject-working-tree": "none",
            "--is-inside-work-tree": "none",
            "--is-inside-git-dir": "none",
            "--is-bare-repository": "none",
            "--is-shallow-repository": "none",
            "--is-shallow-update": "none",
            "--path-prefix": "none",
        },
    ),
    "git rev-list": CommandConfig(
        safe_flags={
            **GIT_REF_SELECTION_FLAGS,
            **GIT_DATE_FILTER_FLAGS,
            **GIT_COUNT_FLAGS,
            **GIT_AUTHOR_FILTER_FLAGS,
            "--count": "none",
            "--reverse": "none",
            "--first-parent": "none",
            "--ancestry-path": "none",
            "--merges": "none",
            "--no-merges": "none",
            "--min-parents": "number",
            "--max-parents": "number",
            "--no-min-parents": "none",
            "--no-max-parents": "none",
            "--skip": "number",
            "--max-age": "number",
            "--min-age": "number",
            "--walk-reflogs": "none",
            "--oneline": "none",
            "--abbrev-commit": "none",
            "--pretty": "string",
            "--format": "string",
            "--abbrev": "number",
            "--full-history": "none",
            "--dense": "none",
            "--sparse": "none",
            "--source": "none",
            "--graph": "none",
        },
    ),
    "git describe": CommandConfig(
        safe_flags={
            "--tags": "none",
            "--match": "string",
            "--exclude": "string",
            "--long": "none",
            "--abbrev": "number",
            "--always": "none",
            "--contains": "none",
            "--first-match": "none",
            "--exact-match": "none",
            "--candidates": "number",
            "--dirty": "none",
            "--broken": "none",
        },
    ),
    "git cat-file": CommandConfig(
        safe_flags={
            "-t": "none",
            "-s": "none",
            "-p": "none",
            "-e": "none",
            "--batch-check": "none",
            "--allow-undetermined-type": "none",
        },
    ),
    "git for-each-ref": CommandConfig(
        safe_flags={
            "--format": "string",
            "--sort": "string",
            "--count": "number",
            "--contains": "string",
            "--no-contains": "string",
            "--merged": "string",
            "--no-merged": "string",
            "--points-at": "string",
        },
    ),
    "git grep": CommandConfig(
        safe_flags={
            "-e": "string",
            "-E": "none",
            "--extended-regexp": "none",
            "-G": "none",
            "--basic-regexp": "none",
            "-F": "none",
            "--fixed-strings": "none",
            "-P": "none",
            "--perl-regexp": "none",
            "-i": "none",
            "--ignore-case": "none",
            "-v": "none",
            "--invert-match": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-n": "none",
            "--line-number": "none",
            "-c": "none",
            "--count": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "-L": "none",
            "--files-without-match": "none",
            "-h": "none",
            "-H": "none",
            "--heading": "none",
            "--break": "none",
            "--full-name": "none",
            "--color": "none",
            "--no-color": "none",
            "-o": "none",
            "--only-matching": "none",
            "-A": "number",
            "--after-context": "number",
            "-B": "number",
            "--before-context": "number",
            "-C": "number",
            "--context": "number",
            "--and": "none",
            "--or": "none",
            "--not": "none",
            "--max-depth": "number",
            "--untracked": "none",
            "--no-index": "none",
            "--recurse-submodules": "none",
            "--cached": "none",
            "--threads": "number",
            "-q": "none",
            "--quiet": "none",
        },
    ),
    "git stash show": CommandConfig(
        safe_flags={
            **GIT_STAT_FLAGS,
            **GIT_COLOR_FLAGS,
            **GIT_PATCH_FLAGS,
            "--word-diff": "none",
            "--word-diff-regex": "string",
            "--diff-filter": "string",
            "--abbrev": "number",
        },
    ),
    "git worktree list": CommandConfig(
        safe_flags={
            "--porcelain": "none",
            "-v": "none",
            "--verbose": "none",
            "--expire": "string",
        },
    ),
    "git tag": CommandConfig(
        safe_flags={
            "-l": "none",
            "--list": "none",
            "-n": "number",
            "--contains": "string",
            "--no-contains": "string",
            "--merged": "string",
            "--no-merged": "string",
            "--sort": "string",
            "--format": "string",
            "--points-at": "string",
            "--column": "none",
            "--no-column": "none",
            "-i": "none",
            "--ignore-case": "none",
        },
        # SECURITY: block tag creation via positional args (write refs/tags/**).
        additional_command_is_dangerous=_git_tag_is_dangerous,
    ),
    "git branch": CommandConfig(
        safe_flags={
            "-l": "none",
            "--list": "none",
            "-a": "none",
            "--all": "none",
            "-r": "none",
            "--remotes": "none",
            "-v": "none",
            "-vv": "none",
            "--verbose": "none",
            "--color": "none",
            "--no-color": "none",
            "--column": "none",
            "--no-column": "none",
            "--abbrev": "number",
            "--no-abbrev": "none",
            "--contains": "string",
            "--no-contains": "string",
            "--merged": "none",
            "--no-merged": "none",
            "--points-at": "string",
            "--sort": "string",
            "--show-current": "none",
            "-i": "none",
            "--ignore-case": "none",
        },
        # SECURITY: block branch creation via positional args.
        additional_command_is_dangerous=_git_branch_is_dangerous,
    ),
}


# ===========================================================================
# DOCKER_READ_ONLY_COMMANDS  (TS readOnlyCommandValidation.ts:1386)
# ===========================================================================

DOCKER_READ_ONLY_COMMANDS: dict[str, CommandConfig] = {
    "docker logs": CommandConfig(
        safe_flags={
            "--follow": "none",
            "-f": "none",
            "--tail": "string",
            "-n": "string",
            "--timestamps": "none",
            "-t": "none",
            "--since": "string",
            "--until": "string",
            "--details": "none",
        },
    ),
    "docker inspect": CommandConfig(
        safe_flags={
            "--format": "string",
            "-f": "string",
            "--type": "string",
            "--size": "none",
            "-s": "none",
        },
    ),
}


# ===========================================================================
# RIPGREP_READ_ONLY_COMMANDS  (TS readOnlyCommandValidation.ts:1416)
# ===========================================================================

RIPGREP_READ_ONLY_COMMANDS: dict[str, CommandConfig] = {
    "rg": CommandConfig(
        safe_flags={
            "-e": "string",
            "--regexp": "string",
            "-f": "string",
            "-i": "none",
            "--ignore-case": "none",
            "-S": "none",
            "--smart-case": "none",
            "-F": "none",
            "--fixed-strings": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-v": "none",
            "--invert-match": "none",
            "-c": "none",
            "--count": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "--files-without-match": "none",
            "-n": "none",
            "--line-number": "none",
            "-o": "none",
            "--only-matching": "none",
            "-A": "number",
            "--after-context": "number",
            "-B": "number",
            "--before-context": "number",
            "-C": "number",
            "--context": "number",
            "-H": "none",
            "-h": "none",
            "--heading": "none",
            "--no-heading": "none",
            "-q": "none",
            "--quiet": "none",
            "--column": "none",
            "-g": "string",
            "--glob": "string",
            "-t": "string",
            "--type": "string",
            "-T": "string",
            "--type-not": "string",
            "--type-list": "none",
            "--hidden": "none",
            "--no-ignore": "none",
            "-u": "none",
            "-m": "number",
            "--max-count": "number",
            "-d": "number",
            "--max-depth": "number",
            "-a": "none",
            "--text": "none",
            "-z": "none",
            "-L": "none",
            "--follow": "none",
            "--color": "string",
            "--json": "none",
            "--stats": "none",
            "--help": "none",
            "--version": "none",
            "--debug": "none",
            "--": "none",
        },
    ),
}


# ===========================================================================
# PYRIGHT_READ_ONLY_COMMANDS  (TS readOnlyCommandValidation.ts:1504)
# ===========================================================================

PYRIGHT_READ_ONLY_COMMANDS: dict[str, CommandConfig] = {
    "pyright": CommandConfig(
        # pyright treats `--` as a file path, not end-of-options.
        respects_double_dash=False,
        safe_flags={
            "--outputjson": "none",
            "--project": "string",
            "-p": "string",
            "--pythonversion": "string",
            "--pythonplatform": "string",
            "--typeshedpath": "string",
            "--venvpath": "string",
            "--level": "string",
            "--stats": "none",
            "--verbose": "none",
            "--version": "none",
            "--dependencies": "none",
            "--warnings": "none",
        },
        additional_command_is_dangerous=_pyright_is_dangerous,
    ),
}


# ===========================================================================
# EXTERNAL_READONLY_COMMANDS  (TS readOnlyCommandValidation.ts:1539)
# Cross-shell commands that work identically in bash and PowerShell.
# ===========================================================================

EXTERNAL_READONLY_COMMANDS: list[str] = [
    "docker ps",
    "docker images",
]


# ===========================================================================
# FD_SAFE_FLAGS  (TS readOnlyValidation.ts:54)
# SECURITY: -x/--exec, -X/--exec-batch, -l/--list-details deliberately excluded.
# ===========================================================================

FD_SAFE_FLAGS: dict[str, str] = {
    "-h": "none",
    "--help": "none",
    "-V": "none",
    "--version": "none",
    "-H": "none",
    "--hidden": "none",
    "-I": "none",
    "--no-ignore": "none",
    "--no-ignore-vcs": "none",
    "--no-ignore-parent": "none",
    "-s": "none",
    "--case-sensitive": "none",
    "-i": "none",
    "--ignore-case": "none",
    "-g": "none",
    "--glob": "none",
    "--regex": "none",
    "-F": "none",
    "--fixed-strings": "none",
    "-a": "none",
    "--absolute-path": "none",
    "-L": "none",
    "--follow": "none",
    "-p": "none",
    "--full-path": "none",
    "-0": "none",
    "--print0": "none",
    "-d": "number",
    "--max-depth": "number",
    "--min-depth": "number",
    "--exact-depth": "number",
    "-t": "string",
    "--type": "string",
    "-e": "string",
    "--extension": "string",
    "-S": "string",
    "--size": "string",
    "--changed-within": "string",
    "--changed-before": "string",
    "-o": "string",
    "--owner": "string",
    "-E": "string",
    "--exclude": "string",
    "--ignore-file": "string",
    "-c": "string",
    "--color": "string",
    "-j": "number",
    "--threads": "number",
    "--max-buffer-time": "string",
    "--max-results": "number",
    "-1": "none",
    "-q": "none",
    "--quiet": "none",
    "--show-errors": "none",
    "--strip-cwd-prefix": "none",
    "--one-file-system": "none",
    "--prune": "none",
    "--search-path": "string",
    "--base-directory": "string",
    "--path-separator": "string",
    "--batch-size": "number",
    "--no-require-git": "none",
    "--hyperlink": "string",
    "--and": "string",
    "--format": "string",
}


# ===========================================================================
# COMMAND_ALLOWLIST  (TS readOnlyValidation.ts:127-1136)
# Spreads keep the shared tables single-source; insertion order preserved so
# multi-word command matching sees longer patterns appropriately.
# ===========================================================================

COMMAND_ALLOWLIST: dict[str, CommandConfig] = {
    "xargs": CommandConfig(
        safe_flags={
            "-I": "{}",
            # SECURITY: lowercase -i / -e REMOVED (GNU optional-attached-arg
            # semantics create a validator/xargs differential -> exfil / RCE).
            "-n": "number",
            "-P": "number",
            "-L": "number",
            "-s": "number",
            "-E": "EOF",  # POSIX, MANDATORY separate arg
            "-0": "none",
            "-t": "none",
            "-r": "none",
            "-x": "none",
            "-d": "char",
        },
    ),
    # All git read-only commands from the shared validation map.
    **GIT_READ_ONLY_COMMANDS,
    "file": CommandConfig(
        safe_flags={
            "--brief": "none",
            "-b": "none",
            "--mime": "none",
            "-i": "none",
            "--mime-type": "none",
            "--mime-encoding": "none",
            "--apple": "none",
            "--check-encoding": "none",
            "-c": "none",
            "--exclude": "string",
            "--exclude-quiet": "string",
            "--print0": "none",
            "-0": "none",
            "-f": "string",
            "-F": "string",
            "--separator": "string",
            "--help": "none",
            "--version": "none",
            "-v": "none",
            "--no-dereference": "none",
            "-h": "none",
            "--dereference": "none",
            "-L": "none",
            "--magic-file": "string",
            "-m": "string",
            "--keep-going": "none",
            "-k": "none",
            "--list": "none",
            "-l": "none",
            "--no-buffer": "none",
            "-n": "none",
            "--preserve-date": "none",
            "-p": "none",
            "--raw": "none",
            "-r": "none",
            "-s": "none",
            "--special-files": "none",
            "--uncompress": "none",
            "-z": "none",
        },
    ),
    "sed": CommandConfig(
        safe_flags={
            "--expression": "string",
            "-e": "string",
            "--quiet": "none",
            "--silent": "none",
            "-n": "none",
            "--regexp-extended": "none",
            "-r": "none",
            "--posix": "none",
            "-E": "none",
            "--line-length": "number",
            "-l": "number",
            "--zero-terminated": "none",
            "-z": "none",
            "--separate": "none",
            "-s": "none",
            "--unbuffered": "none",
            "-u": "none",
            "--debug": "none",
            "--help": "none",
            "--version": "none",
        },
        additional_command_is_dangerous=_sed_is_dangerous,
    ),
    "sort": CommandConfig(
        safe_flags={
            "--ignore-leading-blanks": "none",
            "-b": "none",
            "--dictionary-order": "none",
            "-d": "none",
            "--ignore-case": "none",
            "-f": "none",
            "--general-numeric-sort": "none",
            "-g": "none",
            "--human-numeric-sort": "none",
            "-h": "none",
            "--ignore-nonprinting": "none",
            "-i": "none",
            "--month-sort": "none",
            "-M": "none",
            "--numeric-sort": "none",
            "-n": "none",
            "--random-sort": "none",
            "-R": "none",
            "--reverse": "none",
            "-r": "none",
            "--sort": "string",
            "--stable": "none",
            "-s": "none",
            "--unique": "none",
            "-u": "none",
            "--version-sort": "none",
            "-V": "none",
            "--zero-terminated": "none",
            "-z": "none",
            "--key": "string",
            "-k": "string",
            "--field-separator": "string",
            "-t": "string",
            "--check": "none",
            "-c": "none",
            "--check-char-order": "none",
            "-C": "none",
            "--merge": "none",
            "-m": "none",
            "--buffer-size": "string",
            "-S": "string",
            "--parallel": "number",
            "--batch-size": "number",
            "--help": "none",
            "--version": "none",
        },
    ),
    "man": CommandConfig(
        safe_flags={
            "-a": "none",
            "--all": "none",
            "-d": "none",
            "-f": "none",
            "--whatis": "none",
            "-h": "none",
            "-k": "none",
            "--apropos": "none",
            "-l": "string",
            "-w": "none",
            "-S": "string",
            "-s": "string",
        },
    ),
    # help — only bash builtin help flags (man's -P allows arbitrary exec).
    "help": CommandConfig(
        safe_flags={
            "-d": "none",
            "-m": "none",
            "-s": "none",
        },
    ),
    "netstat": CommandConfig(
        safe_flags={
            "-a": "none",
            "-L": "none",
            "-l": "none",
            "-n": "none",
            "-f": "string",
            "-g": "none",
            "-i": "none",
            "-I": "string",
            "-s": "none",
            "-r": "none",
            "-m": "none",
            "-v": "none",
        },
    ),
    "ps": CommandConfig(
        safe_flags={
            "-e": "none",
            "-A": "none",
            "-a": "none",
            "-d": "none",
            "-N": "none",
            "--deselect": "none",
            "-f": "none",
            "-F": "none",
            "-l": "none",
            "-j": "none",
            "-y": "none",
            "-w": "none",
            "-ww": "none",
            "--width": "number",
            "-c": "none",
            "-H": "none",
            "--forest": "none",
            "--headers": "none",
            "--no-headers": "none",
            "-n": "string",
            "--sort": "string",
            "-L": "none",
            "-T": "none",
            "-m": "none",
            "-C": "string",
            "-G": "string",
            "-g": "string",
            "-p": "string",
            "--pid": "string",
            "-q": "string",
            "--quick-pid": "string",
            "-s": "string",
            "--sid": "string",
            "-t": "string",
            "--tty": "string",
            "-U": "string",
            "-u": "string",
            "--user": "string",
            "--help": "none",
            "--info": "none",
            "-V": "none",
            "--version": "none",
        },
        # Block BSD-style 'e' modifier (shows env vars).
        additional_command_is_dangerous=_ps_is_dangerous,
    ),
    "base64": CommandConfig(
        respects_double_dash=False,  # macOS base64 does not respect POSIX --
        safe_flags={
            "-d": "none",
            "-D": "none",
            "--decode": "none",
            "-b": "number",
            "--break": "number",
            "-w": "number",
            "--wrap": "number",
            "-i": "string",
            "--input": "string",
            "--ignore-garbage": "none",
            "-h": "none",
            "--help": "none",
            "--version": "none",
        },
    ),
    "grep": CommandConfig(
        safe_flags={
            "-e": "string",
            "--regexp": "string",
            "-f": "string",
            "--file": "string",
            "-F": "none",
            "--fixed-strings": "none",
            "-G": "none",
            "--basic-regexp": "none",
            "-E": "none",
            "--extended-regexp": "none",
            "-P": "none",
            "--perl-regexp": "none",
            "-i": "none",
            "--ignore-case": "none",
            "--no-ignore-case": "none",
            "-v": "none",
            "--invert-match": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-x": "none",
            "--line-regexp": "none",
            "-c": "none",
            "--count": "none",
            "--color": "string",
            "--colour": "string",
            "-L": "none",
            "--files-without-match": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "-m": "number",
            "--max-count": "number",
            "-o": "none",
            "--only-matching": "none",
            "-q": "none",
            "--quiet": "none",
            "--silent": "none",
            "-s": "none",
            "--no-messages": "none",
            "-b": "none",
            "--byte-offset": "none",
            "-H": "none",
            "--with-filename": "none",
            "-h": "none",
            "--no-filename": "none",
            "--label": "string",
            "-n": "none",
            "--line-number": "none",
            "-T": "none",
            "--initial-tab": "none",
            "-u": "none",
            "--unix-byte-offsets": "none",
            "-Z": "none",
            "--null": "none",
            "-z": "none",
            "--null-data": "none",
            "-A": "number",
            "--after-context": "number",
            "-B": "number",
            "--before-context": "number",
            "-C": "number",
            "--context": "number",
            "--group-separator": "string",
            "--no-group-separator": "none",
            "-a": "none",
            "--text": "none",
            "--binary-files": "string",
            "-D": "string",
            "--devices": "string",
            "-d": "string",
            "--directories": "string",
            "--exclude": "string",
            "--exclude-from": "string",
            "--exclude-dir": "string",
            "--include": "string",
            "-r": "none",
            "--recursive": "none",
            "-R": "none",
            "--dereference-recursive": "none",
            "--line-buffered": "none",
            "-U": "none",
            "--binary": "none",
            "--help": "none",
            "-V": "none",
            "--version": "none",
        },
    ),
    # rg (ripgrep) from the shared validation map.
    **RIPGREP_READ_ONLY_COMMANDS,
    "sha256sum": CommandConfig(
        safe_flags={
            "-b": "none",
            "--binary": "none",
            "-t": "none",
            "--text": "none",
            "-c": "none",
            "--check": "none",
            "--ignore-missing": "none",
            "--quiet": "none",
            "--status": "none",
            "--strict": "none",
            "-w": "none",
            "--warn": "none",
            "--tag": "none",
            "-z": "none",
            "--zero": "none",
            "--help": "none",
            "--version": "none",
        },
    ),
    "sha1sum": CommandConfig(
        safe_flags={
            "-b": "none",
            "--binary": "none",
            "-t": "none",
            "--text": "none",
            "-c": "none",
            "--check": "none",
            "--ignore-missing": "none",
            "--quiet": "none",
            "--status": "none",
            "--strict": "none",
            "-w": "none",
            "--warn": "none",
            "--tag": "none",
            "-z": "none",
            "--zero": "none",
            "--help": "none",
            "--version": "none",
        },
    ),
    "md5sum": CommandConfig(
        safe_flags={
            "-b": "none",
            "--binary": "none",
            "-t": "none",
            "--text": "none",
            "-c": "none",
            "--check": "none",
            "--ignore-missing": "none",
            "--quiet": "none",
            "--status": "none",
            "--strict": "none",
            "-w": "none",
            "--warn": "none",
            "--tag": "none",
            "-z": "none",
            "--zero": "none",
            "--help": "none",
            "--version": "none",
        },
    ),
    # tree — -o/--output writes to a file, so it's excluded. -R excluded (writes
    # 00Tree.html). All other flags are display/filter options.
    "tree": CommandConfig(
        safe_flags={
            "-a": "none",
            "-d": "none",
            "-l": "none",
            "-f": "none",
            "-x": "none",
            "-L": "number",
            "-P": "string",
            "-I": "string",
            "--gitignore": "none",
            "--gitfile": "string",
            "--ignore-case": "none",
            "--matchdirs": "none",
            "--metafirst": "none",
            "--prune": "none",
            "--info": "none",
            "--infofile": "string",
            "--noreport": "none",
            "--charset": "string",
            "--filelimit": "number",
            "-q": "none",
            "-N": "none",
            "-Q": "none",
            "-p": "none",
            "-u": "none",
            "-g": "none",
            "-s": "none",
            "-h": "none",
            "--si": "none",
            "--du": "none",
            "-D": "none",
            "--timefmt": "string",
            "-F": "none",
            "--inodes": "none",
            "--device": "none",
            "-v": "none",
            "-t": "none",
            "-c": "none",
            "-U": "none",
            "-r": "none",
            "--dirsfirst": "none",
            "--filesfirst": "none",
            "--sort": "string",
            "-i": "none",
            "-A": "none",
            "-S": "none",
            "-n": "none",
            "-C": "none",
            "-X": "none",
            "-J": "none",
            "-H": "string",
            "--nolinks": "none",
            "--hintro": "string",
            "--houtro": "string",
            "-T": "string",
            "--hyperlink": "none",
            "--scheme": "string",
            "--authority": "string",
            "--fromfile": "none",
            "--fromtabfile": "none",
            "--fflinks": "none",
            "--help": "none",
            "--version": "none",
        },
    ),
    # date — -s/--set and -f/--file can set system time; only safe display
    # options allowed, and positional args must start with '+' (see callback).
    "date": CommandConfig(
        safe_flags={
            "-d": "string",
            "--date": "string",
            "-r": "string",
            "--reference": "string",
            "-u": "none",
            "--utc": "none",
            "--universal": "none",
            "-I": "none",
            "--iso-8601": "string",
            "-R": "none",
            "--rfc-email": "none",
            "--rfc-3339": "string",
            "--debug": "none",
            "--help": "none",
            "--version": "none",
        },
        additional_command_is_dangerous=_date_is_dangerous,
    ),
    # hostname — positional args set the hostname; block them via regex.
    "hostname": CommandConfig(
        safe_flags={
            "-f": "none",
            "--fqdn": "none",
            "--long": "none",
            "-s": "none",
            "--short": "none",
            "-i": "none",
            "--ip-address": "none",
            "-I": "none",
            "--all-ip-addresses": "none",
            "-a": "none",
            "--alias": "none",
            "-d": "none",
            "--domain": "none",
            "-A": "none",
            "--all-fqdns": "none",
            "-v": "none",
            "--verbose": "none",
            "-h": "none",
            "--help": "none",
            "-V": "none",
            "--version": "none",
        },
        regex=re.compile(r"^hostname(?:\s+(?:-[a-zA-Z]|--[a-zA-Z-]+))*\s*$"),
    ),
    # info — -o/--output writes files; only safe display/navigation options.
    "info": CommandConfig(
        safe_flags={
            "-f": "string",
            "--file": "string",
            "-d": "string",
            "--directory": "string",
            "-n": "string",
            "--node": "string",
            "-a": "none",
            "--all": "none",
            "-k": "string",
            "--apropos": "string",
            "-w": "none",
            "--where": "none",
            "--location": "none",
            "--show-options": "none",
            "--vi-keys": "none",
            "--subnodes": "none",
            "-h": "none",
            "--help": "none",
            "--usage": "none",
            "--version": "none",
        },
    ),
    "lsof": CommandConfig(
        safe_flags={
            "-?": "none",
            "-h": "none",
            "-v": "none",
            "-a": "none",
            "-b": "none",
            "-C": "none",
            "-l": "none",
            "-n": "none",
            "-N": "none",
            "-O": "none",
            "-P": "none",
            "-Q": "none",
            "-R": "none",
            "-t": "none",
            "-U": "none",
            "-V": "none",
            "-X": "none",
            "-H": "none",
            "-E": "none",
            "-F": "none",
            "-g": "none",
            "-i": "none",
            "-K": "none",
            "-L": "none",
            "-o": "none",
            "-r": "none",
            "-s": "none",
            "-S": "none",
            "-T": "none",
            "-x": "none",
            "-A": "string",
            "-c": "string",
            "-d": "string",
            "-e": "string",
            "-k": "string",
            "-p": "string",
            "-u": "string",
            # OMITTED (writes to disk): -D (device cache file build/update)
        },
        # Block +m (create mount supplement file) — writes to disk.
        additional_command_is_dangerous=_lsof_is_dangerous,
    ),
    "pgrep": CommandConfig(
        safe_flags={
            "-d": "string",
            "--delimiter": "string",
            "-l": "none",
            "--list-name": "none",
            "-a": "none",
            "--list-full": "none",
            "-v": "none",
            "--inverse": "none",
            "-w": "none",
            "--lightweight": "none",
            "-c": "none",
            "--count": "none",
            "-f": "none",
            "--full": "none",
            "-g": "string",
            "--pgroup": "string",
            "-G": "string",
            "--group": "string",
            "-i": "none",
            "--ignore-case": "none",
            "-n": "none",
            "--newest": "none",
            "-o": "none",
            "--oldest": "none",
            "-O": "string",
            "--older": "string",
            "-P": "string",
            "--parent": "string",
            "-s": "string",
            "--session": "string",
            "-t": "string",
            "--terminal": "string",
            "-u": "string",
            "--euid": "string",
            "-U": "string",
            "--uid": "string",
            "-x": "none",
            "--exact": "none",
            "-F": "string",
            "--pidfile": "string",
            "-L": "none",
            "--logpidfile": "none",
            "-r": "string",
            "--runstates": "string",
            "--ns": "string",
            "--nslist": "string",
            "--help": "none",
            "-V": "none",
            "--version": "none",
        },
    ),
    "tput": CommandConfig(
        safe_flags={
            "-T": "string",
            "-V": "none",
            "-x": "none",
            # SECURITY: -S (read capability names from stdin) EXCLUDED.
        },
        additional_command_is_dangerous=_tput_is_dangerous,
    ),
    # ss — socket statistics (iproute2). -K/--kill, -D/--diag, -F/--filter,
    # -N/--net deliberately excluded.
    "ss": CommandConfig(
        safe_flags={
            "-h": "none",
            "--help": "none",
            "-V": "none",
            "--version": "none",
            "-n": "none",
            "--numeric": "none",
            "-r": "none",
            "--resolve": "none",
            "-a": "none",
            "--all": "none",
            "-l": "none",
            "--listening": "none",
            "-o": "none",
            "--options": "none",
            "-e": "none",
            "--extended": "none",
            "-m": "none",
            "--memory": "none",
            "-p": "none",
            "--processes": "none",
            "-i": "none",
            "--info": "none",
            "-s": "none",
            "--summary": "none",
            "-4": "none",
            "--ipv4": "none",
            "-6": "none",
            "--ipv6": "none",
            "-0": "none",
            "--packet": "none",
            "-t": "none",
            "--tcp": "none",
            "-M": "none",
            "--mptcp": "none",
            "-S": "none",
            "--sctp": "none",
            "-u": "none",
            "--udp": "none",
            "-d": "none",
            "--dccp": "none",
            "-w": "none",
            "--raw": "none",
            "-x": "none",
            "--unix": "none",
            "--tipc": "none",
            "--vsock": "none",
            "-f": "string",
            "--family": "string",
            "-A": "string",
            "--query": "string",
            "--socket": "string",
            "-Z": "none",
            "--context": "none",
            "-z": "none",
            "--contexts": "none",
            "-b": "none",
            "--bpf": "none",
            "-E": "none",
            "--events": "none",
            "-H": "none",
            "--no-header": "none",
            "-O": "none",
            "--oneline": "none",
            "--tipcinfo": "none",
            "--tos": "none",
            "--cgroup": "none",
            "--inet-sockopt": "none",
        },
    ),
    # fd/fdfind — fast file finder. -x/--exec and -X/--exec-batch excluded.
    "fd": CommandConfig(safe_flags={**FD_SAFE_FLAGS}),
    # fdfind is the Debian/Ubuntu package name for fd — same binary/flags.
    "fdfind": CommandConfig(safe_flags={**FD_SAFE_FLAGS}),
    **PYRIGHT_READ_ONLY_COMMANDS,
    **DOCKER_READ_ONLY_COMMANDS,
}


# ===========================================================================
# SAFE_TARGET_COMMANDS_FOR_XARGS  (TS readOnlyValidation.ts:1166)
# ===========================================================================

SAFE_TARGET_COMMANDS_FOR_XARGS: list[str] = [
    "echo",
    "printf",
    "wc",
    "grep",
    "head",
    "tail",
]


# ===========================================================================
# makeRegexForSafeCommand + READONLY_COMMANDS + READONLY_COMMAND_REGEXES
# (TS readOnlyValidation.ts:1356-1504)
# ===========================================================================


def make_regex_for_safe_command(command: str) -> re.Pattern[str]:
    r"""TS readOnlyValidation.ts:1356. Matches safe invocations of `command`,
    blocking shell metacharacters / substitution / expansion / assignment.
    The command name is interpolated raw (unescaped), matching the TS template
    literal: new RegExp(`^${command}(?:\s|$)[^<>()$\`|{}&;\n\r]*$`)."""
    return re.compile("^" + command + r"(?:\s|$)[^<>()$`|{}&;\n\r]*$")


# Simple commands that are safe for execution (each -> makeRegexForSafeCommand).
READONLY_COMMANDS: list[str] = [
    # Cross-platform commands from shared validation.
    *EXTERNAL_READONLY_COMMANDS,
    # Time and date.
    "cal",
    "uptime",
    # File content viewing.
    "cat",
    "head",
    "tail",
    "wc",
    "stat",
    "strings",
    "hexdump",
    "od",
    "nl",
    # System info.
    "id",
    "uname",
    "free",
    "df",
    "du",
    "locale",
    "groups",
    "nproc",
    # Path information.
    "basename",
    "dirname",
    "realpath",
    # Text processing.
    "cut",
    "paste",
    "tr",
    "column",
    "tac",
    "rev",
    "fold",
    "expand",
    "unexpand",
    "fmt",
    "comm",
    "cmp",
    "numfmt",
    # Path information (additional).
    "readlink",
    # File comparison.
    "diff",
    # true and false.
    "true",
    "false",
    # Misc. safe commands.
    "sleep",
    "which",
    "type",
    "expr",
    "test",
    "getconf",
    "seq",
    "tsort",
    "pr",
]


# Complex commands that require custom regex patterns.
# TS uses a Set; a list preserves order and is equivalent for `.test()` scans.
READONLY_COMMAND_REGEXES: list[re.Pattern[str]] = [
    # Simple commands converted via make_regex_for_safe_command.
    *[make_regex_for_safe_command(c) for c in READONLY_COMMANDS],
    # Echo that doesn't execute commands or use variables. Allow newlines in
    # single quotes (safe), optional trailing 2>&1.
    re.compile(
        r"""^echo(?:\s+(?:'[^']*'|"[^"$<>\n\r]*"|[^|;&`$(){}><#\\!"'\s]+))*(?:\s+2>&1)?\s*$"""
    ),
    # Claude CLI help.
    re.compile(r"^claude -h$"),
    re.compile(r"^claude --help$"),
    # Only flags, no input/output files.
    re.compile(r"^uniq(?:\s+(?:-[a-zA-Z]+|--[a-zA-Z-]+(?:=\S+)?|-[fsw]\s+\d+))*(?:\s|$)\s*$"),
    # System info.
    re.compile(r"^pwd$"),
    re.compile(r"^whoami$"),
    # Development tools version checking — exact match only, no suffix allowed.
    re.compile(r"^node -v$"),
    re.compile(r"^node --version$"),
    re.compile(r"^python --version$"),
    re.compile(r"^python3 --version$"),
    # Misc. safe commands.
    re.compile(r"^history(?:\s+\d+)?\s*$"),
    re.compile(r"^alias$"),
    re.compile(r"^arch(?:\s+(?:--help|-h))?\s*$"),
    # Network commands — exact commands with no arguments.
    re.compile(r"^ip addr$"),
    re.compile(r"^ifconfig(?:\s+[a-zA-Z][a-zA-Z0-9_-]*)?\s*$"),
    # JSON processing with jq — inline filters and file args; block dangerous
    # flags (-f/--from-file, --rawfile, --slurpfile, --run-tests, -L/--library-
    # path), the `env` builtin, and `$ENV`.
    re.compile(
        r"""^jq(?!\s+.*(?:-f\b|--from-file|--rawfile|--slurpfile|--run-tests|-L\b|--library-path|\benv\b|\$ENV\b))(?:\s+(?:-[a-zA-Z]+|--[a-zA-Z-]+(?:=\S+)?))*(?:\s+'[^'`]*'|\s+"[^"`]*"|\s+[^-\s'"][^\s]*)+\s*$"""
    ),
    # Path commands (path validation ensures they're allowed).
    # cd — allows changing to directories.
    re.compile(r"""^cd(?:\s+(?:'[^']*'|"[^"]*"|[^\s;|&`$(){}><#\\]+))?$"""),
    # ls — allows listing directories.
    re.compile(r"^ls(?:\s+[^<>()$`|{}&;\n\r]*)?$"),
    # find — blocks dangerous flags. Allow escaped parens \( \) for grouping.
    re.compile(
        r"^find(?:\s+(?:\\[()]|(?!-delete\b|-exec\b|-execdir\b|-ok\b|-okdir\b|-fprint0?\b|-fls\b|-fprintf\b)[^<>()$`|{}&;\n\r\s]|\s)+)?$"
    ),
]


__all__ = [
    "FlagArgType",
    "CommandConfig",
    "GIT_REF_SELECTION_FLAGS",
    "GIT_DATE_FILTER_FLAGS",
    "GIT_LOG_DISPLAY_FLAGS",
    "GIT_COUNT_FLAGS",
    "GIT_STAT_FLAGS",
    "GIT_COLOR_FLAGS",
    "GIT_PATCH_FLAGS",
    "GIT_AUTHOR_FILTER_FLAGS",
    "GIT_READ_ONLY_COMMANDS",
    "RIPGREP_READ_ONLY_COMMANDS",
    "PYRIGHT_READ_ONLY_COMMANDS",
    "DOCKER_READ_ONLY_COMMANDS",
    "EXTERNAL_READONLY_COMMANDS",
    "FD_SAFE_FLAGS",
    "COMMAND_ALLOWLIST",
    "SAFE_TARGET_COMMANDS_FOR_XARGS",
    "make_regex_for_safe_command",
    "READONLY_COMMANDS",
    "READONLY_COMMAND_REGEXES",
    "sed_command_is_allowed_by_allowlist",
    "is_line_printing_command",
    "is_substitution_command",
    "is_print_command",
    "has_file_args",
    "extract_sed_expressions",
    "contains_dangerous_operations",
]
