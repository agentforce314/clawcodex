from __future__ import annotations

import re
import shlex
from typing import List, Sequence


def parse_arguments(args: str) -> List[str]:
    if not args or not args.strip():
        return []
    try:
        return shlex.split(args)
    except Exception:
        return [x for x in re.split(r"\s+", args.strip()) if x]


def parse_argument_names(argument_names) -> List[str]:
    if not argument_names:
        return []
    if isinstance(argument_names, list):
        names = [str(x).strip() for x in argument_names]
    else:
        names = [x.strip() for x in str(argument_names).split() if x.strip()]
    return [n for n in names if n and not re.fullmatch(r"\d+", n)]


def substitute_arguments(
    content: str,
    args: str | None,
    *,
    append_if_no_placeholder: bool = True,
    argument_names: Sequence[str] = (),
) -> str:
    if args is None:
        return content

    parsed_args = parse_arguments(args)
    original = content

    for idx, name in enumerate(argument_names):
        if not name:
            continue
        pattern = re.compile(rf"\${re.escape(name)}(?![\[\w])")
        content = pattern.sub(parsed_args[idx] if idx < len(parsed_args) else "", content)

    def repl_indexed(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return parsed_args[i] if i < len(parsed_args) else ""

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", repl_indexed, content)

    def repl_shorthand(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return parsed_args[i] if i < len(parsed_args) else ""

    content = re.sub(r"\$(\d+)(?!\w)", repl_shorthand, content)

    content = content.replace("$ARGUMENTS", args)

    if content == original and append_if_no_placeholder and args:
        content = content + f"\n\nARGUMENTS: {args}"

    return content

