from __future__ import annotations

import re
import shlex


def quote(s: str) -> str:
    if not s:
        return "''"
    if re.match(r"^[a-zA-Z0-9._/=:@%^,+-]+$", s):
        return s
    return shlex.quote(s)


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def is_glob_pattern(s: str) -> bool:
    return bool(re.search(r"(?<!\\)[*?[\]]", s))


def expand_home(path: str) -> str:
    if path.startswith("~/") or path == "~":
        import os
        return os.path.expanduser(path)
    return path
