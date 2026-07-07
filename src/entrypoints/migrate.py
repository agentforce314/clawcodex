"""``clawcodex migrate`` — explicit legacy ``.claude`` → ``.clawcodex`` migration.

The user-level copy (``~/.claude`` → ``~/.clawcodex``) also runs
automatically once at startup (see ``src/cli.py``); this subcommand
re-attempts it (still destination-absent-only, so it never overwrites)
and additionally migrates the CURRENT project's ``.claude/`` directory —
project migration mutates the user's repo, so it only ever runs from
this explicit command.
"""

from __future__ import annotations

import os


_USAGE = """\
Usage: clawcodex migrate [--user-only | --project-only]

Copies legacy .claude state into the clawcodex locations. Nothing under
~/.claude or ./.claude is ever modified or deleted; existing .clawcodex
files always win.

  (default)        migrate ~/.claude -> ~/.clawcodex AND ./.claude -> ./.clawcodex
  --user-only      only the user-level migration
  --project-only   only the current directory's project migration
"""


def run_migrate_subcommand(rest: list[str]) -> int:
    if any(arg in ("--help", "-h") for arg in rest):
        print(_USAGE, end="")
        return 0
    unknown = [a for a in rest if a not in ("--user-only", "--project-only")]
    if unknown:
        print(f"unknown argument(s): {' '.join(unknown)}\n\n{_USAGE}", end="")
        return 2
    if "--user-only" in rest and "--project-only" in rest:
        print(f"--user-only and --project-only are mutually exclusive\n\n{_USAGE}", end="")
        return 2

    from src.utils.legacy_migration import (
        format_report,
        migrate_project_dir,
        migrate_user_dir_once,
    )

    do_user = "--project-only" not in rest
    do_project = "--user-only" not in rest

    if do_user:
        report = migrate_user_dir_once(force=True)
        print("User-level migration:")
        if report is None:
            print("  nothing to do")
        else:
            print(format_report(report))
    if do_project:
        print("Project migration:")
        print(format_report(migrate_project_dir(os.getcwd())))
    return 0
