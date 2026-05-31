from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from src.orchestrator.config.schema import WorkflowConfig
from src.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from src.orchestrator.repo_tracker.adapter import RepositoryTrackerAdapter
from src.orchestrator.tracker import (
    PullRequestRef,
    TrackerConfigError,
    create_tracker_adapter,
    repository_clone_url_for_tracker,
    validate_tracker_config,
)


@contextmanager
def _patched_env(values: dict[str, str]):
    original = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_issue(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestWorkflowTrackerConfig(unittest.TestCase):
    def test_github_tracker_reads_kind_specific_env_defaults(self) -> None:
        with _patched_env(
            {
                "GITHUB_TOKEN": "gh-test-token",
                "GITHUB_OWNER": "acme",
                "GITHUB_REPO": "widget",
                "GITHUB_ASSIGNEE": "codex-bot",
            }
        ):
            config = WorkflowConfig.from_dict(
                {"tracker": {"kind": "github"}}
            )

        self.assertEqual(config.tracker.kind, "github")
        self.assertEqual(config.tracker.endpoint, "https://api.github.com")
        self.assertEqual(config.tracker.api_key, "gh-test-token")
        self.assertEqual(config.tracker.owner, "acme")
        self.assertEqual(config.tracker.repo, "widget")
        self.assertEqual(config.tracker.assignee, "codex-bot")
        self.assertEqual(config.tracker.active_states, ["open"])
        self.assertEqual(config.tracker.terminal_states, ["closed"])

    def test_gitcode_tracker_uses_opened_default_state(self) -> None:
        config = WorkflowConfig.from_dict({"tracker": {"kind": "gitcode"}})
        self.assertEqual(config.tracker.active_states, ["opened"])
        self.assertEqual(config.tracker.endpoint, "https://api.gitcode.com/api/v5")

    def test_validate_tracker_config_requires_repository_for_repo_trackers(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "github",
                    "api_key": "gh-test-token",
                }
            }
        )

        with self.assertRaises(TrackerConfigError):
            validate_tracker_config(config.tracker)

    def test_create_tracker_adapter_returns_repository_adapter(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "github",
                    "api_key": "gh-test-token",
                    "owner": "acme",
                    "repo": "widget",
                    "active_states": ["In Progress"],
                }
            }
        )

        adapter = create_tracker_adapter(config.tracker)

        self.assertIsInstance(adapter, RepositoryTrackerAdapter)
        self.assertEqual(adapter.platform, "github")
        self.assertEqual(adapter.active_states, ["In Progress"])

    def test_repository_clone_url_defaults_from_tracker_kind(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "gitee",
                    "api_key": "gitee-token",
                    "owner": "acme",
                    "repo": "widget",
                }
            }
        )

        clone_url = repository_clone_url_for_tracker(config.tracker)

        self.assertEqual(clone_url, "https://gitee.com/acme/widget.git")

    def test_local_tracker_config_uses_local_defaults(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "~/issues"}}
        )

        self.assertEqual(config.tracker.kind, "local")
        self.assertEqual(config.tracker.issues_path, os.path.expanduser("~/issues"))
        self.assertEqual(config.tracker.active_states, ["open", "ready"])
        self.assertEqual(
            config.tracker.terminal_states,
            ["completed", "closed", "cancelled", "failed", "abandoned"],
        )

    def test_tracker_state_lists_accept_scalar_values(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "local",
                    "issues_path": "/tmp/issues",
                    "active_states": "open",
                    "terminal_states": "completed",
                }
            }
        )

        self.assertEqual(config.tracker.active_states, ["open"])
        self.assertEqual(config.tracker.terminal_states, ["completed"])

    def test_validate_local_tracker_requires_issues_path_not_api_key(self) -> None:
        valid = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )
        validate_tracker_config(valid.tracker)

        invalid = WorkflowConfig.from_dict({"tracker": {"kind": "local"}})
        with self.assertRaises(TrackerConfigError):
            validate_tracker_config(invalid.tracker)

    def test_create_tracker_adapter_returns_local_adapter(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )

        adapter = create_tracker_adapter(config.tracker)

        self.assertIsInstance(adapter, LocalTrackerAdapter)
        self.assertEqual(adapter.active_states, ["open", "ready"])

    def test_repository_clone_url_is_none_for_local_tracker(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )

        self.assertIsNone(repository_clone_url_for_tracker(config.tracker))


class TestLocalTrackerAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_issues_are_filtered_and_sorted(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "ready.md",
                """---
id: LOCAL-002
identifier: LOCAL-002
state: ready
priority: 2
labels:
  - orchestrator
---
# Ready issue

Do this second.
""",
            )
            _write_issue(
                issues_path / "open.md",
                """---
id: LOCAL-001
identifier: LOCAL-001
state: open
priority: 1
---
# Open issue

Do this first.
""",
            )
            _write_issue(
                issues_path / "done.md",
                """---
id: LOCAL-003
identifier: LOCAL-003
state: completed
priority: 0
---
# Done issue
""",
            )

            adapter = LocalTrackerAdapter(issues_path)
            issues = await adapter.fetch_candidate_issues()

        self.assertEqual([issue.id for issue in issues], ["LOCAL-001", "LOCAL-002"])
        self.assertEqual(issues[0].title, "Open issue")
        self.assertEqual(issues[0].description, "Do this first.")
        self.assertEqual(issues[0].branch_name, "local/local-001-open-issue")
        self.assertEqual(issues[1].labels, ["orchestrator"])

    async def test_fetch_issue_states_rereads_document(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            issue_path = issues_path / "issue.md"
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: open
---
# Test issue
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            first = await adapter.fetch_issue_states_by_ids(["LOCAL-001"])
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: ready
---
# Test issue
""",
            )
            second = await adapter.fetch_issue_states_by_ids(["LOCAL-001"])

        self.assertEqual(first["LOCAL-001"].state, "open")
        self.assertEqual(second["LOCAL-001"].state, "ready")

    async def test_update_issue_state_preserves_body(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            issue_path = issues_path / "issue.md"
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: open
---
# Keep me

Body remains.
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            await adapter.update_issue_state("LOCAL-001", "completed")

            updated = issue_path.read_text(encoding="utf-8")

        self.assertIn("state: completed", updated)
        self.assertIn("# Keep me\n\nBody remains.", updated)
        self.assertIn("updated_at:", updated)

    async def test_comments_round_trip_through_ndjson(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(Path(tmp))

            await adapter.create_comment("LOCAL-001", "sync complete")
            clarification = await adapter.create_clarification_comment(
                "LOCAL-001",
                "Need details",
                mentions=["alice"],
            )
            comments = await adapter.fetch_issue_comments("LOCAL-001")
            new_comments = await adapter.fetch_new_comments_since(
                "LOCAL-001",
                comments[0].id,
            )

        self.assertIsNotNone(clarification)
        self.assertEqual([comment.author_login for comment in comments], ["clawcodex", "clawcodex"])
        self.assertEqual(comments[0].body, "sync complete")
        self.assertEqual(comments[1].body, "@alice\n\nNeed details")
        self.assertEqual(new_comments, [comments[1]])

    async def test_comment_files_include_hash_to_avoid_sanitized_name_collision(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            adapter = LocalTrackerAdapter(issues_path)

            await adapter.create_comment("LOCAL/001", "first")
            await adapter.create_comment("LOCAL:001", "second")

            comment_files = sorted(issues_path.glob("*.comments.ndjson"))

        self.assertEqual(len(comment_files), 2)

    async def test_adapter_state_lists_are_returned_as_copies(self) -> None:
        adapter = LocalTrackerAdapter("/tmp/issues")

        active_states = adapter.active_states
        active_states.append("mutated")

        self.assertEqual(adapter.active_states, ["open", "ready"])

    async def test_find_pull_request_skips_matching_document_without_pr_url(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "without-pr.md",
                """---
id: LOCAL-001
state: open
branch_name: local/branch
base_branch: main
---
# Missing PR URL
""",
            )
            _write_issue(
                issues_path / "with-pr.md",
                """---
id: LOCAL-002
state: open
branch_name: local/branch
base_branch: main
pr_number: '43'
pr_url: https://example.invalid/pr/43
pr_title: Complete PR
---
# Complete PR
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            pr = await adapter.find_pull_request(
                head_branch="local/branch",
                base_branch="main",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="43",
                url="https://example.invalid/pr/43",
                title="Complete PR",
            ),
        )

    async def test_find_pull_request_uses_local_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "issue.md",
                """---
id: LOCAL-001
state: open
branch_name: local/branch
base_branch: main
pr_number: '42'
pr_url: https://example.invalid/pr/42
pr_title: Local PR
---
# Test issue
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            pr = await adapter.find_pull_request(
                head_branch="local/branch",
                base_branch="main",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="42",
                url="https://example.invalid/pr/42",
                title="Local PR",
            ),
        )


class TestRepositoryTrackerAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_github_candidate_fetch_normalizes_and_filters_issues(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/repos/acme/widget/issues":
                payload = [
                    {
                        "number": 12,
                        "title": "Fix failing build",
                        "body": "details",
                        "state": "open",
                        "labels": [{"name": "In Progress"}],
                        "assignee": {"login": "codex-bot"},
                        "html_url": "https://github.com/acme/widget/issues/12",
                    },
                    {
                        "number": 13,
                        "title": "PR masquerading as issue",
                        "state": "open",
                        "pull_request": {"url": "https://api.github.com/repos/acme/widget/pulls/13"},
                    },
                    {
                        "number": 14,
                        "title": "Assigned elsewhere",
                        "state": "open",
                        "labels": [{"name": "In Progress"}],
                        "assignee": {"login": "someone-else"},
                    },
                ]
                return httpx.Response(200, json=payload)
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                active_states=["In Progress"],
                assignee="codex-bot",
                http_client=client,
            )

            issues = await adapter.fetch_candidate_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].id, "12")
        self.assertEqual(issues[0].identifier, "#12")
        self.assertEqual(issues[0].state, "in progress")
        self.assertEqual(issues[0].labels, ["in progress"])
        self.assertEqual(issues[0].assignee_id, "codex-bot")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer gh-test-token")

    async def test_github_issue_branch_is_extracted_from_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 15,
                        "title": "Fix branch workflow",
                        "body": "Branch: feature/issue-15\n\nDo the work.",
                        "state": "open",
                    }
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            issues = await adapter.fetch_candidate_issues()

        self.assertEqual(issues[0].branch_name, "feature/issue-15")

    async def test_gitee_comment_uses_access_token_query_param(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["query"] = dict(request.url.params)
            body = request.content.decode("utf-8")
            seen["body"] = body
            return httpx.Response(201, json={"id": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="gitee",
                owner="acme",
                repo="widget",
                api_key="gitee-token",
                http_client=client,
            )
            await adapter.create_comment("99", "job finished")

        self.assertEqual(
            seen["path"],
            "/api/v5/repos/acme/widget/issues/99/comments",
        )
        self.assertEqual(seen["query"]["access_token"], "gitee-token")
        self.assertIn("body=job+finished", seen["body"])

    async def test_github_refresh_by_ids_returns_mapping(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            issue_no = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "number": int(issue_no),
                    "title": f"Issue {issue_no}",
                    "state": "open",
                    "labels": [{"name": "Todo"}],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                active_states=["Todo"],
                http_client=client,
            )
            issues = await adapter.fetch_issue_states_by_ids(["7", "8"])

        self.assertEqual(sorted(issues), ["7", "8"])
        self.assertEqual(issues["7"].state, "todo")

    async def test_ensure_pull_request_uses_existing_open_pr(self) -> None:
        seen_requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append((request.method, request.url.path))
            if request.method == "GET" and request.url.path == "/repos/acme/widget/pulls":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 21,
                            "title": "Existing PR",
                            "html_url": "https://github.com/acme/widget/pull/21",
                        }
                    ],
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            pr = await adapter.ensure_pull_request(
                issue=None,  # type: ignore[arg-type]
                head_branch="feature/issue-1",
                base_branch="main",
                title="PR title",
                body="PR body",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="21",
                title="Existing PR",
                url="https://github.com/acme/widget/pull/21",
            ),
        )
        self.assertEqual(seen_requests, [("GET", "/repos/acme/widget/pulls")])

    async def test_ensure_pull_request_creates_when_missing(self) -> None:
        seen_payloads: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/repos/acme/widget/pulls":
                return httpx.Response(200, json=[])
            if request.method == "POST" and request.url.path == "/repos/acme/widget/pulls":
                seen_payloads.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    201,
                    json={
                        "number": 22,
                        "title": "Created PR",
                        "html_url": "https://github.com/acme/widget/pull/22",
                    },
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            pr = await adapter.ensure_pull_request(
                issue=None,  # type: ignore[arg-type]
                head_branch="feature/issue-2",
                base_branch="main",
                title="PR title",
                body="PR body",
            )

        self.assertEqual(pr.number, "22")
        self.assertEqual(
            seen_payloads[0],
            {
                "title": "PR title",
                "head": "feature/issue-2",
                "base": "main",
                "body": "PR body",
            },
        )
