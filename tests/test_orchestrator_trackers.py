from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from typing import Any

import httpx

from src.orchestrator.config.schema import WorkflowConfig
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
