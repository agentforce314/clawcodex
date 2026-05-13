"""Audit-only scaffolding moved out of ``src/`` in Phase 3 of the
ch01-architecture refactor.

These modules form a closed graph driven by
``python -m scripts.audit.cli <subcommand>``. They support TS↔Python
parity reporting (``parity_audit``, ``port_manifest``,
``bootstrap_graph``, ``command_graph``) and demo dispatchers
(``runtime``, ``query_engine``, ``execution_registry``) used by the
audit subcommands and by ``tests/test_porting_workspace.py``.

None of these modules is reachable from the production CLI entry
``src.cli:main`` (verified before the move).

For the architecture overview and the rationale for keeping the audit
graph separate from production, see ``docs/ARCHITECTURE.md`` §"Audit-only
scaffolding".
"""
