"""Standalone executable test: orchestrator dashboard with 3 mock issues."""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from extensions.orchestrator.linear.issue import Issue
from extensions.orchestrator.status_dashboard import SessionStatus, StatusDashboard


def make_mock_issues():
    """Create 3 mock issues for testing."""
    return [
        Issue(id="issue-1", identifier="TST-100", title="Test Issue 1", state="In Progress"),
        Issue(id="issue-2", identifier="TST-101", title="Test Issue 2", state="In Progress"),
        Issue(id="issue-3", identifier="TST-102", title="Test Issue 3", state="In Progress"),
    ]


def test_dashboard_three_sessions():
    """Test dashboard with 3 running sessions — the main requirement."""
    dashboard = StatusDashboard()

    issues = make_mock_issues()

    # Simulate 3 running sessions
    for i, issue in enumerate(issues):
        session_status = SessionStatus(
            issue_id=issue.id or f"issue-{i+1}",
            issue_identifier=issue.identifier or f"TST-{i+100}",
            status="running",
            turn_count=0,
            max_turns=20,
            workspace_path=f"/tmp/workspace_{issue.id}",
        )
        dashboard.on_session_start(session_status)

    # Verify header shows 3 running
    output = dashboard.render()
    print("=== Dashboard Output ===")
    print(output)
    print()

    assert "RUNNING SESSIONS" in output, "Missing RUNNING SESSIONS header"
    assert "TST-100" in output, "Missing TST-100"
    assert "TST-101" in output, "Missing TST-101"
    assert "TST-102" in output, "Missing TST-102"
    assert "running=3" in output, "Expected running=3"

    print("✅ Dashboard shows 3 running sessions correctly")


def test_dashboard_completion():
    """Test dashboard after completing 2 of 3 sessions."""
    dashboard = StatusDashboard()

    issues = make_mock_issues()

    # Start 3 sessions
    for issue in issues:
        dashboard.on_session_start(
            SessionStatus(
                issue_id=issue.id or "",
                issue_identifier=issue.identifier or "",
                status="running",
                max_turns=20,
            )
        )

    # Complete 2 sessions
    dashboard.on_session_complete("issue-1")
    dashboard.on_session_complete("issue-2")

    output = dashboard.render()
    print("=== After Completing 2 Sessions ===")
    print(output)
    print()

    assert "completed=2" in output, "Expected completed=2"
    # issue-3 should still be running
    assert "TST-102" in output, "TST-102 should still be in running"

    print("✅ Dashboard correctly tracks completed sessions")


def test_dashboard_with_sparkline():
    """Test TPS calculation and sparkline display."""
    dashboard = StatusDashboard()

    # Simulate token throughput samples
    dashboard._token_samples.extend([100, 150, 200, 180, 220, 250])
    dashboard._last_rendered_at = time.time() - 1.0

    sparkline = dashboard.throughput_sparkline()
    tps = dashboard.tps()

    output = dashboard.render()
    print("=== Dashboard with Sparkline ===")
    print(output)
    print()
    print(f"TPS: {tps:.2f}")
    print(f"Sparkline: {sparkline}")

    assert len(sparkline) > 0, "Sparkline should not be empty"

    print("✅ TPS sparkline rendering works")


def test_full_flow():
    """Full orchestrator dashboard flow with 3 issues."""
    dashboard = StatusDashboard()

    issues = make_mock_issues()

    print("=== Full Dashboard Test with 3 Mock Issues ===")
    print()

    # Phase 1: Start all 3
    print("[1] Starting 3 sessions...")
    for issue in issues:
        dashboard.on_session_start(
            SessionStatus(
                issue_id=issue.id or "",
                issue_identifier=issue.identifier or "",
                status="running",
                turn_count=3,
                max_turns=20,
                total_tokens=5000,
                workspace_path=f"/tmp/workspace_{issue.id}",
            )
        )

    output1 = dashboard.render()
    print(output1)
    print()

    # Phase 2: Complete 1, fail 1
    print("[2] Completing TST-100, failing TST-101...")
    dashboard.on_session_complete("issue-1")
    dashboard.on_session_failed("issue-2", "max_turns_exceeded")

    output2 = dashboard.render()
    print(output2)
    print()

    # Phase 3: Complete remaining
    print("[3] Completing TST-102...")
    dashboard.on_session_complete("issue-3")

    output3 = dashboard.render()
    print(output3)
    print()

    print("✅ Full flow test passed")


if __name__ == "__main__":
    print("=" * 80)
    print("ClawCodex Orchestrator Dashboard Test")
    print("=" * 80)
    print()

    test_dashboard_three_sessions()
    print()

    test_dashboard_completion()
    print()

    test_dashboard_with_sparkline()
    print()

    test_full_flow()
    print()

    print("=" * 80)
    print("All tests passed! ✅")
    print("=" * 80)