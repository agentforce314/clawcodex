"""/skills backend — the enriched ``list_skills`` control.

The TUI's /skills command + skills hub group skills by ``category`` and
show ``path``, so the control must carry both (the original #463 payload
had only name + 80-char description, capped at 120 entries).
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch


def _session():
    from src.server.agent_server import AgentServerConfig, _AgentSession

    sess = _AgentSession(
        session_id="s1", cwd="/tmp",
        config=AgentServerConfig(single_session=True),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    replies = []
    sess._reply = lambda rid, payload: replies.append(payload)
    return sess, replies


def _list_skills(skills):
    sess, replies = _session()
    with patch("src.skills.loader.get_all_skills", return_value=skills):
        asyncio.run(sess._handle_control_request({
            "request_id": "r1",
            "request": {"subtype": "list_skills"},
        }))
    return replies[-1]


class TestListSkillsControl(unittest.TestCase):
    def test_payload_carries_category_and_path(self):
        from src.skills.model import Skill

        payload = _list_skills([
            Skill(name="qa", description="QA a web app", source="userSettings",
                  loaded_from="skills", skill_root="/u/qa"),
            Skill(name="deep-research", description="d" * 500, source="",
                  loaded_from="bundled"),
        ])

        self.assertEqual(payload["total"], 2)
        by_name = {s["name"]: s for s in payload["skills"]}
        # Settings scope wins over the generic 'skills' disk bucket.
        self.assertEqual(by_name["qa"]["category"], "user")
        self.assertEqual(by_name["qa"]["path"], "/u/qa")
        # No scope → the loaded_from bucket; description capped at 400.
        self.assertEqual(by_name["deep-research"]["category"], "bundled")
        self.assertEqual(len(by_name["deep-research"]["description"]), 400)

    def test_scope_map_covers_project_and_managed(self):
        from src.skills.model import Skill

        payload = _list_skills([
            Skill(name="p", description="", source="projectSettings", loaded_from="skills"),
            Skill(name="m", description="", source="policySettings", loaded_from="skills"),
        ])

        cats = {s["name"]: s["category"] for s in payload["skills"]}
        self.assertEqual(cats, {"p": "project", "m": "managed"})

    def test_total_reports_full_count_beyond_the_entry_cap(self):
        from src.skills.model import Skill

        payload = _list_skills([
            Skill(name=f"s{i}", description="", loaded_from="bundled")
            for i in range(1005)
        ])

        self.assertEqual(payload["total"], 1005)
        self.assertEqual(len(payload["skills"]), 1000)


if __name__ == "__main__":
    unittest.main()
