"""Session-scoped git worktrees — the ``--worktree`` / ``-w`` feature.

Port of ``typescript/src/utils/worktree.ts`` (the *session* subset used by
``setup.ts`` and ``WorktreeExitDialog.tsx``): create-or-resume an isolated
worktree at ``<repoRoot>/.claude/worktrees/<slug>`` on branch
``worktree-<slug>``, run the whole session inside it, and at exit either keep
it or remove it (directory + branch).

Relationship to the other worktree helpers in this repo — do NOT merge them:
``src/utils/git.py::create_worktree/remove_worktree`` (used by
``src/workflow/worktree.py``) and ``src/bridge/worktree.py`` are throwaway
agent-isolation primitives (``-b``/``--detach``, no base resolution, no
resume, no post-creation setup). This module is the session-lifecycle port
with resume semantics, base-branch resolution, ``.worktreeinclude`` support
and keep/remove bookkeeping.

Process topology: the CLI launcher (``src/cli.py`` / ``tui_launcher``) creates
the worktree BEFORE spawning the Ink TUI, and advertises it to the child
processes via ``CLAWCODEX_WORKTREE_*`` env vars (see :class:`WorktreeSession`).
The agent-server reads the same vars to service the exit-time ``worktree_status``
/ ``worktree_exit`` control requests. ``src/cli.py::main`` strips any INHERITED
``CLAWCODEX_WORKTREE_*`` at entry so a nested clawcodex launched from inside a
worktree (e.g. by the agent's Bash tool) can never adopt — and delete — the
outer session's worktree.

Security note on canonical-root resolution: TS validates ``.git`` gitfile /
commondir backpointers itself (typescript/src/utils/git.ts:126-158) because it
parses those files in-process. We shell out to ``git rev-parse`` instead —
git FOLLOWS pointer files but does not enforce TS's backpointer/ancestry
policy, so a crafted ``.git`` file could still resolve ``--git-common-dir``
into another repo, landing worktree creation and the settings.local.json copy
there. Accepted residual risk: the folder-trust gate has already run on the
launch directory, and the impact is local-integrity-only. Revisit if a
backend-side trust gate ever lands.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_WORKTREE_SLUG_LENGTH = 64
_VALID_SLUG_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")

WORKTREES_SUBDIR = os.path.join(".claude", "worktrees")

#: Env channel launcher → (Ink TUI, agent-server). Keep in sync with
#: ``ui-tui/src/lib/worktree.ts``.
ENV_NAME = "CLAWCODEX_WORKTREE_NAME"
ENV_PATH = "CLAWCODEX_WORKTREE_PATH"
ENV_BRANCH = "CLAWCODEX_WORKTREE_BRANCH"
ENV_ORIGINAL_CWD = "CLAWCODEX_WORKTREE_ORIGINAL_CWD"
ENV_REPO_ROOT = "CLAWCODEX_WORKTREE_REPO_ROOT"
ENV_OWNER_PID = "CLAWCODEX_WORKTREE_OWNER_PID"
ENV_PREFIX = "CLAWCODEX_WORKTREE_"

# Prevent git/SSH from prompting for credentials (which would hang the CLI):
# GIT_TERMINAL_PROMPT=0 stops git opening /dev/tty; empty GIT_ASKPASS disables
# askpass GUIs; stdin is DEVNULL on every call below. Mirrors GIT_NO_PROMPT_ENV
# in the TS reference.
_GIT_NO_PROMPT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}

# Slug words for unnamed sessions. Deviation from TS (setup.ts uses
# getPlanSlug()'s adjective-verb-noun word slug with plan-file-collision
# retry): ours is adj-noun-<4 base36> retried against worktree-path existence
# — the actual collision domain here.
_SLUG_ADJECTIVES = (
    "swift", "bright", "calm", "keen", "bold", "quiet", "amber", "coral",
    "misty", "nimble", "lucid", "mellow", "brisk", "sunny", "vivid", "wry",
)
_SLUG_NOUNS = (
    "fox", "owl", "elm", "oak", "ray", "wren", "fern", "reef",
    "dune", "cove", "peak", "glen", "pine", "brook", "cliff", "vale",
)
_SLUG_SUFFIX_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


class WorktreeError(Exception):
    """A worktree operation failed; ``str(exc)`` is user-presentable."""


@dataclass
class WorktreeSession:
    """A live ``--worktree`` session, serializable over the env channel."""

    worktree_name: str
    worktree_path: str
    worktree_branch: str
    original_cwd: str
    repo_root: str

    def to_env(self) -> dict[str, str]:
        """Env vars advertising this session to child processes.

        ``OWNER_PID`` is the CALLING process — only the launcher that created
        the worktree ever calls this, and the Ink TUI validates its ppid
        against it so a stale/leaked env block is ignored.
        """
        return {
            ENV_NAME: self.worktree_name,
            ENV_PATH: self.worktree_path,
            ENV_BRANCH: self.worktree_branch,
            ENV_ORIGINAL_CWD: self.original_cwd,
            ENV_REPO_ROOT: self.repo_root,
            ENV_OWNER_PID: str(os.getpid()),
        }

    @classmethod
    def from_env(cls, environ: dict[str, str] | os._Environ | None = None) -> "WorktreeSession | None":
        env = os.environ if environ is None else environ
        values = {key: env.get(key, "") for key in (ENV_NAME, ENV_PATH, ENV_BRANCH,
                                                    ENV_ORIGINAL_CWD, ENV_REPO_ROOT)}
        if not all(values.values()):
            return None
        return cls(
            worktree_name=values[ENV_NAME],
            worktree_path=values[ENV_PATH],
            worktree_branch=values[ENV_BRANCH],
            original_cwd=values[ENV_ORIGINAL_CWD],
            repo_root=values[ENV_REPO_ROOT],
        )


@dataclass
class WorktreeChanges:
    """What would be lost if the worktree were removed right now.

    ``git_ok=False`` means a git command failed — callers MUST fail closed:
    treat as has-changes, never silent-remove, and render no counts (they are
    zeroed placeholders, not measurements).
    """

    git_ok: bool
    dirty_files: int
    commits: int

    @property
    def is_clean(self) -> bool:
        return self.git_ok and self.dirty_files == 0 and self.commits == 0


@dataclass
class _CreateResult:
    worktree_path: str
    worktree_branch: str
    existed: bool
    base_branch: str | None = None


def _git(
    args: list[str],
    cwd: str,
    timeout: float = 60.0,
    no_prompt: bool = False,
) -> tuple[str, str, int]:
    """Run ``git <args>`` in ``cwd``; (stdout, stderr, rc). Never raises."""
    env = None
    if no_prompt:
        env = {**os.environ, **_GIT_NO_PROMPT_ENV}
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", f"git {' '.join(args[:2])} timed out after {timeout:.0f}s", -1
    except FileNotFoundError:
        return "", "git not found on PATH", -1
    except OSError as exc:
        return "", str(exc), -1


def validate_worktree_slug(slug: str) -> None:
    """Reject slugs that could escape ``.claude/worktrees/`` (TS parity).

    The slug is joined into the worktrees dir with ``os.path.join``, which
    normalizes ``..`` segments and discards the prefix for absolute paths —
    so both must be rejected up front. Forward slashes are allowed for
    nesting (``user/feature``); each segment is validated independently.
    Raises :class:`WorktreeError` (callers surface the message verbatim).
    """
    if len(slug) > MAX_WORKTREE_SLUG_LENGTH:
        raise WorktreeError(
            f"Invalid worktree name: must be {MAX_WORKTREE_SLUG_LENGTH} "
            f"characters or fewer (got {len(slug)})"
        )
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise WorktreeError(
                f'Invalid worktree name "{slug}": must not contain "." or ".." path segments'
            )
        if not _VALID_SLUG_SEGMENT.match(segment):
            raise WorktreeError(
                f'Invalid worktree name "{slug}": each "/"-separated segment must be '
                "non-empty and contain only letters, digits, dots, underscores, and dashes"
            )


def flatten_slug(slug: str) -> str:
    """``user/feature`` → ``user+feature`` for both branch name and dir name.

    Nesting is unsafe in both places: git refs D/F-conflict
    (``worktree-user`` file vs ``worktree-user/feature`` dir) and a nested
    worktree dir lives INSIDE the parent worktree, so removing the parent
    deletes the child. ``+`` is valid in branch names and paths but not in
    the slug allowlist, so the mapping is injective (TS parity).
    """
    return slug.replace("/", "+")


def worktree_branch_name(slug: str) -> str:
    return f"worktree-{flatten_slug(slug)}"


def worktrees_dir(repo_root: str) -> str:
    return os.path.join(repo_root, WORKTREES_SUBDIR)


def worktree_path_for(repo_root: str, slug: str) -> str:
    return os.path.join(worktrees_dir(repo_root), flatten_slug(slug))


def parse_pr_reference(text: str) -> int | None:
    """``#123`` or a GitHub-style ``…/pull/123`` URL → 123, else None.

    The ``/pull/N`` path shape is GitHub-specific (GitLab uses
    ``/-/merge_requests/N``, Bitbucket ``/pull-requests/N``), so matching any
    host is safe (TS parity, incl. GHE hosts).
    """
    url_match = re.match(
        r"^https?://[^/]+/[^/]+/[^/]+/pull/(\d+)/?(?:[?#].*)?$", text, re.IGNORECASE
    )
    if url_match:
        return int(url_match.group(1))
    hash_match = re.match(r"^#(\d+)$", text)
    if hash_match:
        return int(hash_match.group(1))
    return None


def generate_worktree_slug(repo_root: str, max_tries: int = 10) -> str:
    """Random ``adj-noun-xxxx`` slug whose worktree path doesn't exist yet."""
    slug = ""
    for _ in range(max_tries):
        suffix = "".join(random.choices(_SLUG_SUFFIX_ALPHABET, k=4))
        slug = f"{random.choice(_SLUG_ADJECTIVES)}-{random.choice(_SLUG_NOUNS)}-{suffix}"
        if not os.path.exists(worktree_path_for(repo_root, slug)):
            return slug
    return slug


def find_canonical_git_root(cwd: str) -> str | None:
    """The MAIN repository root, resolving through linked worktrees.

    ``--git-common-dir`` from inside a linked worktree is ``<main>/.git`` —
    its parent is the main root. When the common dir is NOT named ``.git``
    (submodules resolve to ``<super>/.git/modules/<name>``; unusual layouts),
    fall back to the worktree's own toplevel rather than fabricating a root.
    See the module docstring for why TS's gitfile backpointer validation is
    deliberately not replicated here.
    """
    out, _, rc = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd)
    if rc == 0 and out:
        common = Path(out)
        if common.name == ".git":
            return str(common.parent)
    top, _, rc = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return top or None if rc == 0 else None


def is_git_repository(cwd: str) -> bool:
    _, _, rc = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return rc == 0


def build_rev_parse_failure_message(base_branch: str, stderr: str, exit_code: int) -> str:
    """Human-readable message for base-branch resolution failures (TS parity)."""
    detail = stderr.strip() or f"exit code {exit_code}"
    hint = (
        " (HEAD has no resolvable commit — make at least one commit, or check "
        "whether git is installed and on PATH)"
        if base_branch == "HEAD"
        else ""
    )
    return f'Failed to resolve base branch "{base_branch}": {detail}{hint}'


def _read_resumable_worktree(worktree_path: str) -> bool:
    """True iff ``worktree_path`` is an existing LINKED worktree we can resume.

    The gate requires ``<path>/.git`` to exist as a FILE (a linked worktree's
    gitfile pointer) before trusting ``rev-parse``: inside an orphaned plain
    directory, ``git -C <path> rev-parse HEAD`` walks UP and reports the main
    repo's HEAD — "resuming" into an unisolated subdirectory of the main tree.
    Mirrors TS ``readWorktreeHeadSha`` reading the gitfile directly.
    """
    gitfile = Path(worktree_path) / ".git"
    if not gitfile.is_file():
        return False
    _, _, rc = _git(["rev-parse", "--verify", "HEAD"], cwd=worktree_path)
    return rc == 0


def _get_or_create_worktree(
    repo_root: str, slug: str, pr_number: int | None = None
) -> _CreateResult:
    """Create the worktree for ``slug``, or resume it if it already exists."""
    worktree_path = worktree_path_for(repo_root, slug)
    branch = worktree_branch_name(slug)

    if _read_resumable_worktree(worktree_path):
        return _CreateResult(worktree_path=worktree_path, worktree_branch=branch, existed=True)

    os.makedirs(worktrees_dir(repo_root), exist_ok=True)

    # ── base resolution (TS order) ──
    base_branch: str
    if pr_number is not None:
        _, fetch_err, fetch_rc = _git(
            ["fetch", "origin", f"pull/{pr_number}/head"],
            cwd=repo_root, timeout=300.0, no_prompt=True,
        )
        if fetch_rc != 0:
            raise WorktreeError(
                f"Failed to fetch PR #{pr_number}: "
                + (fetch_err.strip() or 'PR may not exist or the repository may not have a remote named "origin"')
            )
        base_branch = "FETCH_HEAD"
    else:
        from src.utils.git import get_default_branch

        default_branch = get_default_branch(repo_root)
        origin_ref = f"origin/{default_branch}"
        _, _, local_rc = _git(
            ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default_branch}"],
            cwd=repo_root,
        )
        if local_rc == 0:
            # origin/<branch> already known locally: skip the fetch. A slightly
            # stale base is fine — the user can pull inside the worktree.
            base_branch = origin_ref
        else:
            _, _, fetch_rc = _git(
                ["fetch", "origin", default_branch],
                cwd=repo_root, timeout=300.0, no_prompt=True,
            )
            base_branch = origin_ref if fetch_rc == 0 else "HEAD"

    _, sha_err, sha_rc = _git(["rev-parse", base_branch], cwd=repo_root)
    if sha_rc != 0:
        raise WorktreeError(build_rev_parse_failure_message(base_branch, sha_err, sha_rc))

    # -B (not -b): reset an orphan branch left behind by a removed worktree
    # dir instead of failing (TS parity).
    _, add_err, add_rc = _git(
        ["worktree", "add", "-B", branch, worktree_path, base_branch],
        cwd=repo_root, timeout=600.0,
    )
    if add_rc != 0:
        message = f"Failed to create worktree: {add_err.strip()}"
        if os.path.exists(worktree_path):
            # Never auto-delete a directory we don't understand (e.g. a
            # half-removed worktree after a kill): fail loud with a hint.
            message += (
                f"\nA non-worktree directory already exists at {worktree_path}. "
                'Run "git worktree prune" and remove the directory if it is a '
                "leftover, then retry."
            )
        raise WorktreeError(message)

    return _CreateResult(
        worktree_path=worktree_path, worktree_branch=branch,
        existed=False, base_branch=base_branch,
    )


def _copy_local_settings(repo_root: str, worktree_path: str) -> None:
    """Propagate local settings (permission grants, hook config the worktree
    session needs) — best-effort, TS parity.

    Two tiers, both copied when present: clawcodex's own project tier
    (``.clawcodex/settings.local.json`` — permission rules; namespaced away
    from the real Claude Code harness, see src/permissions/settings_paths.py)
    and the harness-compatible ``.claude/settings.local.json`` (read for
    hooks; the tier TS itself copies).
    """
    from src.permissions.settings_paths import local_settings_path

    sources = [
        local_settings_path(repo_root),
        os.path.join(repo_root, ".claude", "settings.local.json"),
    ]
    for source in sources:
        rel = os.path.relpath(source, repo_root)
        dest = os.path.join(worktree_path, rel)
        try:
            if os.path.isfile(source):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(source, dest)
        except OSError as exc:
            logger.warning("[worktree] failed to copy %s: %s", rel, exc)


def _configure_hooks_path(repo_root: str, worktree_path: str) -> None:
    """Point the shared repo config at the main repo's hooks dir so relative
    ``.husky``-style hooks keep working from inside the worktree (TS parity,
    minus the config-read memoization)."""
    for candidate in (os.path.join(repo_root, ".husky"),
                      os.path.join(repo_root, ".git", "hooks")):
        if os.path.isdir(candidate):
            _, err, rc = _git(["config", "core.hooksPath", candidate], cwd=worktree_path)
            if rc != 0:
                logger.warning("[worktree] failed to configure hooks path: %s", err)
            return


def copy_worktree_include_files(repo_root: str, worktree_path: str) -> list[str]:
    """Copy gitignored files matched by ``.worktreeinclude`` into the worktree.

    Only files that are BOTH matched by a ``.worktreeinclude`` pattern
    (.gitignore syntax, via ``pathspec``) AND gitignored are copied — e.g.
    ``.env`` files a fresh checkout lacks. Uses ``git ls-files … --directory``
    so fully-ignored dirs (node_modules/…) collapse to one entry, expanding a
    collapsed dir only when a pattern explicitly targets inside it (TS parity).
    """
    include_file = os.path.join(repo_root, ".worktreeinclude")
    try:
        with open(include_file, encoding="utf-8") as fh:
            include_content = fh.read()
    except OSError:
        return []

    patterns = [
        line.strip() for line in include_content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not patterns:
        return []

    import pathspec

    try:
        # GitIgnoreSpec = .gitignore semantics — the same contract as the
        # `ignore` npm library the TS reference matches with.
        spec = pathspec.GitIgnoreSpec.from_lines(patterns)
    except Exception as exc:  # noqa: BLE001 — malformed user patterns
        logger.warning("[worktree] invalid .worktreeinclude patterns: %s", exc)
        return []

    listed, _, rc = _git(
        ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory"],
        cwd=repo_root, timeout=300.0,
    )
    if rc != 0 or not listed:
        return []

    entries = [entry for entry in listed.splitlines() if entry]
    collapsed_dirs = [entry for entry in entries if entry.endswith("/")]
    files = [entry for entry in entries if not entry.endswith("/") and spec.match_file(entry)]

    def _should_expand(directory: str) -> bool:
        for pattern in patterns:
            normalized = pattern[1:] if pattern.startswith("/") else pattern
            if normalized.startswith(directory):
                return True
            glob_match = re.search(r"[*?\[]", normalized)
            if glob_match and glob_match.start() > 0:
                literal_prefix = normalized[: glob_match.start()]
                if directory.startswith(literal_prefix):
                    return True
        return spec.match_file(directory.rstrip("/"))

    dirs_to_expand = [d for d in collapsed_dirs if _should_expand(d)]
    if dirs_to_expand:
        expanded, _, rc = _git(
            ["ls-files", "--others", "--ignored", "--exclude-standard", "--", *dirs_to_expand],
            cwd=repo_root, timeout=300.0,
        )
        if rc == 0 and expanded:
            files.extend(f for f in expanded.splitlines() if f and spec.match_file(f))

    copied: list[str] = []
    for relative in files:
        src = os.path.join(repo_root, relative)
        dest = os.path.join(worktree_path, relative)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)
            copied.append(relative)
        except OSError as exc:
            logger.warning("[worktree] failed to copy %s: %s", relative, exc)
    if copied:
        logger.info("[worktree] copied %d files from .worktreeinclude", len(copied))
    return copied


def _post_creation_setup(repo_root: str, worktree_path: str) -> None:
    _copy_local_settings(repo_root, worktree_path)
    _configure_hooks_path(repo_root, worktree_path)
    copy_worktree_include_files(repo_root, worktree_path)


def create_worktree_for_session(
    slug: str | None,
    cwd: str | None = None,
    pr_number: int | None = None,
) -> WorktreeSession:
    """Create or resume the session worktree; raises :class:`WorktreeError`.

    ``slug=None`` generates a random name. A PR-based session should pass
    both the ``pr-<n>`` slug and ``pr_number`` (see the CLI wiring).
    """
    original_cwd = os.path.abspath(cwd or os.getcwd())

    if not is_git_repository(original_cwd):
        raise WorktreeError(
            f"Can only use --worktree in a git repository, but {original_cwd} "
            "is not a git repository. (WorktreeCreate hooks for other VCS "
            "systems are not supported in clawcodex yet.)"
        )

    repo_root = find_canonical_git_root(original_cwd)
    if not repo_root:
        raise WorktreeError("Could not determine the main git repository root.")

    if slug is None:
        slug = generate_worktree_slug(repo_root)
    validate_worktree_slug(slug)

    result = _get_or_create_worktree(repo_root, slug, pr_number=pr_number)
    if result.existed:
        logger.info("[worktree] resuming existing worktree at %s", result.worktree_path)
    else:
        logger.info(
            "[worktree] created %s on branch %s (base %s)",
            result.worktree_path, result.worktree_branch, result.base_branch,
        )
        _post_creation_setup(repo_root, result.worktree_path)

    return WorktreeSession(
        worktree_name=slug,
        worktree_path=result.worktree_path,
        worktree_branch=result.worktree_branch,
        original_cwd=original_cwd,
        repo_root=repo_root,
    )


def create_session_from_cli_option(
    option: bool | str, cwd: str | None = None
) -> WorktreeSession:
    """CLI adapter: ``--worktree`` (bare → ``True``) or ``--worktree NAME``.

    ``NAME`` may be a PR reference (``#123`` or a GitHub-style PR URL) —
    those become slug ``pr-<n>`` based on ``pull/<n>/head`` (TS parity).
    """
    slug: str | None = None
    pr_number: int | None = None
    if isinstance(option, str):
        pr_number = parse_pr_reference(option)
        slug = f"pr-{pr_number}" if pr_number is not None else option
    return create_worktree_for_session(slug, cwd=cwd, pr_number=pr_number)


def worktree_changes(session: WorktreeSession) -> WorktreeChanges:
    """Measure what removal would lose. Fail-closed on any git failure.

    ``commits`` counts commits reachable from HEAD but from no SURVIVING ref:
    the worktree's own branch is excluded (cleanup is about to ``branch -D``
    it), every other local branch and all remotes count as safe keepers.
    Deviation from TS's ``base..HEAD`` (which under-counts on resumed
    worktrees — the resumed base is HEAD-at-resume — letting the silent-remove
    path discard committed unmerged work): the lost-set is safer and also
    avoids TS's false warning on merged-back work. The exclude pattern MUST be
    the branch's short name — ``refs/heads/<branch>`` silently fails to match
    ``--branches`` (patterns match after prefix-stripping), which would zero
    the count and flip the failure into silent data loss.
    """
    status_out, _, status_rc = _git(["status", "--porcelain"], cwd=session.worktree_path)
    if status_rc != 0:
        return WorktreeChanges(git_ok=False, dirty_files=0, commits=0)
    dirty_files = len([line for line in status_out.splitlines() if line.strip()])

    count_out, _, count_rc = _git(
        ["rev-list", "--count", "HEAD", "--not",
         f"--exclude={session.worktree_branch}", "--branches", "--remotes"],
        cwd=session.worktree_path,
    )
    try:
        commits = int(count_out)
    except ValueError:
        count_rc = -1
        commits = 0
    if count_rc != 0:
        return WorktreeChanges(git_ok=False, dirty_files=dirty_files, commits=0)

    return WorktreeChanges(git_ok=True, dirty_files=dirty_files, commits=commits)


def cleanup_worktree(session: WorktreeSession) -> tuple[bool, str]:
    """Remove the worktree directory and its branch. Returns ``(ok, error)``.

    Branch deletion is best-effort (TS parity): a failure there is logged but
    doesn't fail the removal.
    """
    _, remove_err, remove_rc = _git(
        ["worktree", "remove", "--force", session.worktree_path],
        cwd=session.repo_root, timeout=600.0,
    )
    if remove_rc != 0:
        return False, remove_err.strip() or "git worktree remove failed"

    _, branch_err, branch_rc = _git(
        ["branch", "-D", session.worktree_branch], cwd=session.repo_root
    )
    if branch_rc != 0:
        logger.warning("[worktree] could not delete branch %s: %s",
                       session.worktree_branch, branch_err)
    return True, ""


def keep_message(session: WorktreeSession) -> str:
    return (
        f"Worktree kept. Your work is saved at {session.worktree_path} "
        f"on branch {session.worktree_branch}"
    )


def removal_message(session: WorktreeSession, changes: WorktreeChanges) -> str:
    """TS WorktreeExitDialog wording matrix for the removal result."""
    commits, dirty = changes.commits, changes.dirty_files
    if not changes.git_ok:
        return "Worktree removed."
    if commits > 0 and dirty > 0:
        noun = "commit" if commits == 1 else "commits"
        return f"Worktree removed. {commits} {noun} and uncommitted changes were discarded."
    if commits > 0:
        noun = "commit" if commits == 1 else "commits"
        verb = "was" if commits == 1 else "were"
        return (f"Worktree removed. {commits} {noun} on "
                f"{session.worktree_branch} {verb} discarded.")
    if dirty > 0:
        return "Worktree removed. Uncommitted changes were discarded."
    return "Worktree removed (no changes)"


def strip_worktree_env(environ: os._Environ | dict[str, str] | None = None) -> None:
    """Delete every inherited ``CLAWCODEX_WORKTREE_*`` var (nested-session
    hygiene — see module docstring). Call at CLI entry, before anything
    snapshots the environment."""
    env = os.environ if environ is None else environ
    for key in [k for k in env if k.startswith(ENV_PREFIX)]:
        del env[key]
