"""Skill-system utilities (chapter-12 / Phase-8 + later).

This package hosts skill-related helpers that don't belong in
``src/skills/`` proper. The first inhabitant is
``skill_change_detector`` — a watchdog file watcher that fires on
``SKILL.md`` changes and clears skill registry caches.
"""
